async function fetchVideos() {
    const res = await fetch("/api/videos");
    const videos = await res.json();
    const container = document.getElementById("video-list");
    container.innerHTML = "";

    if (videos.length === 0) {
        container.textContent = "Nog geen verwerkte video’s.";
        return;
    }

    videos.forEach(video => {
        const div = document.createElement("div");
        div.className = "video-item";

        const title = document.createElement("h3");
        title.textContent = video.filename;
        div.appendChild(title);

        const subLabel = document.createElement("div");
        subLabel.textContent = "Ondertitels: " + (video.available_subtitles.join(", ") || "geen");
        div.appendChild(subLabel);

        const dubLabel = document.createElement("div");
        dubLabel.textContent = "Dubs: " + (video.available_dubs.join(", ") || "geen");
        div.appendChild(dubLabel);

        if (video.available_subtitles.length > 0) {
            const btnSubs = document.createElement("button");
            btnSubs.textContent = "Afspelen met ondertitels";
            btnSubs.onclick = () => playVideo(video, { mode: "subs" });
            div.appendChild(btnSubs);
        }

        if (video.available_dubs.length > 0) {
            const dubWrapper = document.createElement("div");
            dubWrapper.className = "dub-buttons";
            video.available_dubs.forEach((lang) => {
                const btnDub = document.createElement("button");
                btnDub.textContent = `Afspelen met dubbing (${lang})`;
                btnDub.onclick = () => playVideo(video, { mode: "dub", lang });
                dubWrapper.appendChild(btnDub);
            });
            div.appendChild(dubWrapper);
        }
    

        container.appendChild(div);
    });
}

function clearTracks(videoEl) {
    while (videoEl.firstChild) {
        videoEl.removeChild(videoEl.firstChild);
    }
}

let currentSubtitleCleanup = null;

function clearSubtitleOverlay() {
    const overlay = document.getElementById("subtitle-overlay");
    overlay.innerHTML = "";
    overlay.classList.add("hidden");
}

async function playVideo(video, options = {}) {
    const videoEl = document.getElementById("video-player");
    const infoEl = document.getElementById("player-info");
    const overlay = document.getElementById("subtitle-overlay");

    if (currentSubtitleCleanup) {
        currentSubtitleCleanup();
        currentSubtitleCleanup = null;
    }

    const mode = options.mode || "original";
    videoEl.src = `/videos/${video.id}/original`;
    clearTracks(videoEl);
    clearSubtitleOverlay();

    if (mode === "subs") {
            if (!video.available_subtitles || video.available_subtitles.length === 0) {
            infoEl.textContent = "Geen ondertitels beschikbaar.";
        } else {
            try {
                const subtitles = await Promise.all(
                    video.available_subtitles.map(async (lang) => {
                        const res = await fetch(`/videos/${video.id}/subs/${lang}`);
                        if (!res.ok) {
                            throw new Error(`Kon ondertitels voor ${lang} niet laden.`);
                        }
                        const text = await res.text();
                        return { lang, cues: parseVtt(text) };
                    })
                );

                const updateSubtitles = () => {
                    const currentTime = videoEl.currentTime;
                    overlay.innerHTML = "";

                    subtitles.forEach(({ lang, cues }) => {
                        const cue = cues.find(
                            (item) => currentTime >= item.start && currentTime <= item.end
                        );
                        if (cue) {
                            const line = document.createElement("div");
                            line.className = "subtitle-line";
                            const label = document.createElement("span");
                            label.className = "subtitle-lang";
                            label.textContent = lang.toUpperCase();
                            line.appendChild(label);

                            const textSpan = document.createElement("span");
                            textSpan.className = "subtitle-text";
                            cue.text.forEach((segment, index) => {
                                if (index > 0) {
                                    textSpan.appendChild(document.createElement("br"));
                                }
                                textSpan.appendChild(document.createTextNode(segment));
                            });
                            line.appendChild(textSpan);

                            overlay.appendChild(line);
                        }
                    });

                    overlay.classList.toggle("hidden", overlay.children.length === 0);
                };

                const clearSubtitles = () => {
                    clearSubtitleOverlay();
                };

                videoEl.addEventListener("timeupdate", updateSubtitles);
                videoEl.addEventListener("seeked", updateSubtitles);
                videoEl.addEventListener("ended", clearSubtitles);

                currentSubtitleCleanup = () => {
                    videoEl.removeEventListener("timeupdate", updateSubtitles);
                    videoEl.removeEventListener("seeked", updateSubtitles);
                    videoEl.removeEventListener("ended", clearSubtitles);
                    clearSubtitleOverlay();
                };

                infoEl.textContent = `Afspelen met ondertitels (${video.available_subtitles.join(", ")})`;
            } catch (err) {
                console.error(err);
                infoEl.textContent = "Fout bij het laden van ondertitels.";
            }
        }
    } else if (mode === "dub") {
        const selectedLang = options.lang || (video.available_dubs ? video.available_dubs[0] : null);
        if (!selectedLang) {
            infoEl.textContent = "Geen dubbing beschikbaar.";
        } else {
            videoEl.src = `/videos/${video.id}/dub/${selectedLang}`;
            infoEl.textContent = `Afspelen met dubbing (${selectedLang})`;
        }
    } else {
        infoEl.textContent = "Afspelen zonder extra's.";
    }

    videoEl.load();
    const playPromise = videoEl.play();
    if (playPromise !== undefined) {
        playPromise.catch(() => {
            /* automatische afspelen kan worden geblokkeerd */
        });
    }
}

function parseVtt(vttText) {
    const lines = vttText.split(/\r?\n/);
    const cues = [];
    let i = 0;

    while (i < lines.length) {
        const line = lines[i].trim();

        if (!line || line.startsWith("WEBVTT")) {
            i += 1;
            continue;
        }

        // sla cue nummer over
        if (/^\d+$/.test(line)) {
            i += 1;
            continue;
        }

        const timeMatch = line.match(/(\d{2}:\d{2}:\d{2}[\.,]\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}[\.,]\d{3})/);
        if (!timeMatch) {
            i += 1;
            continue;
        }

        const start = timestampToSeconds(timeMatch[1]);
        const end = timestampToSeconds(timeMatch[2]);
        i += 1;

        const textLines = [];
        while (i < lines.length && lines[i].trim() !== "") {
            textLines.push(lines[i]);
            i += 1;
        }

        cues.push({ start, end, text: textLines });

        // overslaan van lege regel
        while (i < lines.length && lines[i].trim() === "") {
            i += 1;
        }
    }

    return cues;
}

function timestampToSeconds(raw) {
    const cleaned = raw.replace(",", ".");
    const [hours, minutes, seconds] = cleaned.split(":");
    return (
        parseInt(hours, 10) * 3600 +
        parseInt(minutes, 10) * 60 +
        parseFloat(seconds)
    );
}

document.getElementById("upload-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const statusEl = document.getElementById("upload-status");
    statusEl.textContent = "Bezig met uploaden en verwerken... Dit kan even duren.";

    const formData = new FormData(form);
    const checkedLangs = [...form.querySelectorAll("input[name='languages']:checked")];

    if (checkedLangs.length === 0 || checkedLangs.length > 2) {
        alert("Selecteer 1 of 2 talen.");
        return;
    }

    try {
        const res = await fetch("/api/upload", {
            method: "POST",
            body: formData,
        });

        if (!res.ok) {
            const err = await res.json();
            statusEl.textContent = "Fout: " + (err.error || res.statusText);
            return;
        }

        const data = await res.json();
        statusEl.textContent = `Verwerking klaar voor video-id: ${data.id}`;
        await fetchVideos();
    } catch (err) {
        console.error(err);
        statusEl.textContent = "Onbekende fout.";
    }
});

window.addEventListener("load", fetchVideos);
