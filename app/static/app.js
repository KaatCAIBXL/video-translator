function filenameWithoutExtension(filename) {
    if (typeof filename !== "string") {
        return "video";
    }
    const lastDot = filename.lastIndexOf(".");
    if (lastDot <= 0) {
        return filename;
    }
    return filename.slice(0, lastDot);
}

function createDownloadLink(text, href, downloadName) {
    const link = document.createElement("a");
    link.className = "download-link";
    link.href = href;
    link.textContent = text;
    if (downloadName) {
        link.setAttribute("download", downloadName);
    }
    return link;
}

async function fetchVideos() {
    const container = document.getElementById("video-list");
    container.innerHTML = "";
    let videos = [];
    try {
        const res = await fetch("/api/videos");
        if (!res.ok) {
            throw new Error(`Request failed with status ${res.status}`);
        }
        videos = await res.json();
    } catch (error) {
        console.error("Failed to load processed videos", error);
        container.textContent = "Unable to load the processed videos right now.";
        return;
    }

    if (!Array.isArray(videos) || videos.length === 0) {
        container.textContent = "No videos have been processed yet.";

        return;
    }

    videos.forEach((video) => {
        const div = document.createElement("div");
        div.className = "video-item";

        const title = document.createElement("h3");
        title.textContent = video.filename;
        div.appendChild(title);

        const summary = document.createElement("div");
        summary.className = "video-summary";
        const subtitlesText =
            video.available_subtitles && video.available_subtitles.length > 0
                ? video.available_subtitles.join(", ")
                : "none";
        const dubsText =
            video.available_dubs && video.available_dubs.length > 0
                ? video.available_dubs.join(", ")
                : "none";
        const audioText =
            video.available_audio && video.available_audio.length > 0
                ? video.available_audio.join(", ")
                : "none";
        summary.textContent = `Subtitles: ${subtitlesText} | Dubbed videos: ${dubsText} | Dubbed audio: ${audioText}`;
        div.appendChild(summary);

        const controls = document.createElement("div");
        controls.className = "video-controls";

        const playOriginalBtn = document.createElement("button");
        playOriginalBtn.textContent = "Play original";
        playOriginalBtn.onclick = () => playVideo(video, { mode: "original" });
        controls.appendChild(playOriginalBtn);

        if (video.available_subtitles && video.available_subtitles.length > 0) {
            const btnSubs = document.createElement("button");
            btnSubs.textContent = "Play with subtitle";
            btnSubs.onclick = () => playVideo(video, { mode: "subs" });
            controls.appendChild(btnSubs);
        }

        if (video.available_dubs && video.available_dubs.length > 0) {
            const dubWrapper = document.createElement("div");
            dubWrapper.className = "dub-buttons";
            video.available_dubs.forEach((lang) => {
                const btnDub = document.createElement("button");
                btnDub.textContent = `Play dubbed (${lang})`;
                btnDub.onclick = () => playVideo(video, { mode: "dub", lang });
                dubWrapper.appendChild(btnDub);
                
            });
            controls.appendChild(dubWrapper);
        }

        div.appendChild(controls);

        const downloads = document.createElement("div");
        downloads.className = "download-links";

        const baseName = filenameWithoutExtension(video.filename);

       

        if (video.available_subtitles && video.available_subtitles.length > 0) {
            video.available_subtitles.forEach((lang) => {
                downloads.appendChild(
                    createDownloadLink(
                        `Download subtitles (${lang})`,
                        `/videos/${video.id}/subs/${lang}`,
                        `${baseName}_${lang}.vtt`
                    )
                );

            });

           if (video.available_subtitles.length > 1) {
                const langParam = video.available_subtitles.join(",");
                const combinedLabel = video.available_subtitles
                    .map((code) => code.toUpperCase())
                    .join(" + ");
                downloads.appendChild(
                    createDownloadLink(
                        `Download subtitles (${combinedLabel})`,
                        `/videos/${video.id}/subs/combined?langs=${encodeURIComponent(langParam)}`,
                        `${baseName}_${video.available_subtitles.join("_")}_combined.vtt`
                    )
                );
            }
        } else {
            downloads.textContent = "No subtitles available for download.";
        }

        div.appendChild(downloads);
        container.appendChild(div);
    });
}

function clearTracks(videoEl) {
    while (videoEl.firstChild) {
        videoEl.removeChild(videoEl.firstChild);
    }
}

let currentSubtitleCleanup = null;

const videoEl = document.getElementById("video-player");
const videoContainer = document.getElementById("video-container");
let pendingContainerFullscreen = false;

function clearSubtitleOverlay() {
    const overlay = document.getElementById("subtitle-overlay");
    overlay.innerHTML = "";
    overlay.classList.add("hidden");
}

function switchToContainerFullscreen() {
    if (!videoContainer || !videoContainer.requestFullscreen) {
        pendingContainerFullscreen = false;
        return;
    }

    videoContainer
        .requestFullscreen()
        .catch(() => {
            pendingContainerFullscreen = false;
        })
        .then(() => {
            pendingContainerFullscreen = false;
        });
}

function handleFullscreenChange() {
    const fullscreenElement = document.fullscreenElement;

    if (fullscreenElement === videoEl) {
        if (!document.exitFullscreen) {
            pendingContainerFullscreen = false;
            return;
        }

        pendingContainerFullscreen = true;
        document.exitFullscreen().catch(() => {
            pendingContainerFullscreen = false;
        });
    } else if (!fullscreenElement && pendingContainerFullscreen) {
        switchToContainerFullscreen();
    } else if (fullscreenElement !== videoContainer) {
        pendingContainerFullscreen = false;
    }
}

document.addEventListener("fullscreenchange", handleFullscreenChange);
async function playVideo(video, options = {}) {
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
            infoEl.textContent = "No subtitles available.";
        } else {
            try {
                const subtitles = await Promise.all(
                    video.available_subtitles.map(async (lang) => {
                        const res = await fetch(`/videos/${video.id}/subs/${lang}`);
                        if (!res.ok) {
                            throw new Error(`Could not load subtitles for ${lang}`);
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

                infoEl.textContent = `Playing with subtitles (${video.available_subtitles.join(", ")})`;
            } catch (err) {
                console.error(err);
                infoEl.textContent = "Failed to load subtitles.";
            }
        }
    } else if (mode === "dub") {
        const selectedLang = options.lang || (video.available_dubs ? video.available_dubs[0] : null);
        if (!selectedLang) {
            infoEl.textContent = "No dubbing available";
        } else {
            videoEl.src = `/videos/${video.id}/dub/${selectedLang}`;
            infoEl.textContent = `Afspelen met dubbing (${selectedLang})`;
        }
    } else {
        infoEl.textContent = "Playing original track.";
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
    statusEl.textContent = "Uploading and processing... This may take a while";

    const formData = new FormData(form);
    const checkedLangs = [...form.querySelectorAll("input[name='languages']:checked")];

    if (checkedLangs.length === 0 || checkedLangs.length > 2) {
        alert("Please select one or two target languages.");
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
        statusEl.innerHTML = "";

        const successMsg = document.createElement("div");
        successMsg.textContent = `Processing finished for video id: ${data.id}`;
        statusEl.appendChild(successMsg);

        if (Array.isArray(data.warnings) && data.warnings.length > 0) {
            const warningBlock = document.createElement("div");
            warningBlock.className = "status-warning";

            const warningTitle = document.createElement("strong");
            warningTitle.textContent = "Warning:";
            warningBlock.appendChild(warningTitle);

            const warningList = document.createElement("ul");
            data.warnings.forEach((msg) => {
                const li = document.createElement("li");
                li.textContent = msg;
                warningList.appendChild(li);
            });

            warningBlock.appendChild(warningList);
            statusEl.appendChild(warningBlock);
        }
        await fetchVideos();
    } catch (err) {
        console.error(err);
        statusEl.textContent = "Onbekende fout.";
    }
});

window.addEventListener("load", fetchVideos);
