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

        const btnSubs = document.createElement("button");
        btnSubs.textContent = "Afspelen met ondertitels";
        btnSubs.onclick = () => playVideo(video.id, "subs");
        div.appendChild(btnSubs);

        const btnDub = document.createElement("button");
        btnDub.textContent = "Afspelen met dubbing (indien beschikbaar)";
        btnDub.onclick = () => playVideo(video.id, "dub");
        div.appendChild(btnDub);

        container.appendChild(div);
    });
}

function clearTracks(videoEl) {
    while (videoEl.firstChild) {
        videoEl.removeChild(videoEl.firstChild);
    }
}

function playVideo(videoId, mode) {
    const videoEl = document.getElementById("video-player");
    const infoEl = document.getElementById("player-info");

    // standaard: originele video
    videoEl.src = `/videos/${videoId}/original`;
    clearTracks(videoEl);

    if (mode === "subs") {
        // vraag de user welke taal
        const lang = prompt("Welke taalcode voor ondertitels? (es/en/nl/pt/fi/ln/lu)");
        if (!lang) return;

        const track = document.createElement("track");
        track.kind = "subtitles";
        track.label = lang;
        track.srclang = lang;
        track.src = `/videos/${videoId}/subs/${lang}`;
        track.default = true;
        videoEl.appendChild(track);

        infoEl.textContent = `Afspelen met ondertitels (${lang})`;
    } else if (mode === "dub") {
        const lang = prompt("Welke taalcode voor dubbing? (es/en/nl/pt/fi/ln/lu)");
        if (!lang) return;

        videoEl.src = `/videos/${videoId}/dub/${lang}`;
        infoEl.textContent = `Afspelen met dubbing (${lang})`;
    }

    videoEl.load();
    videoEl.play();
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
