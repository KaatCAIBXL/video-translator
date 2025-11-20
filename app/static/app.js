// Check if user is editor
const isEditor = document.getElementById("role-indicator")?.textContent.includes("Éditeur") || false;

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

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

function renderWarnings(container, warnings) {
    if (!Array.isArray(warnings) || warnings.length === 0) {
        return;
    }

    const warningBlock = document.createElement("div");
    warningBlock.className = "status-warning";

    const warningTitle = document.createElement("strong");
    warningTitle.textContent = warnings.length === 1 ? "Avertissement :" : "Avertissements :";
    warningBlock.appendChild(warningTitle);

    const warningList = document.createElement("ul");
    warnings.forEach((msg) => {
        const li = document.createElement("li");
        li.textContent = msg;
        warningList.appendChild(li);
    });
    warningBlock.appendChild(warningList);

    container.appendChild(warningBlock);
}

async function pollJobStatus(jobId, statusEl) {
    const pollIntervalMs = 3000;
    const timeoutMs = 10 * 60 * 1000; // 10 minuten
    const start = Date.now();
    let slowProcessingWarned = false;
    const progressEl = document.createElement("div");
    progressEl.textContent = `La tâche ${jobId} est en attente...`;
    statusEl.appendChild(progressEl);

    while (true) {
        let job;
        try {
            const res = await fetch(`/api/jobs/${jobId}`);
            if (!res.ok) {
                throw new Error(`status ${res.status}`);
            }
            job = await res.json();
        } catch (err) {
            progressEl.className = "status-error";
            progressEl.textContent = `Impossible de vérifier l'état ${err.message}`;
            return;
        }

        if (job.status === "completed") {
            statusEl.innerHTML = "";
            const successMsg = document.createElement("div");
            successMsg.className = "status-success";
            const langInfo = job.original_language
                ? `Langue détectée: ${job.original_language}.`
                : "";
            successMsg.textContent = `Vidéo traitée avec succès.${langInfo}`;
            statusEl.appendChild(successMsg);
            renderWarnings(statusEl, job.warnings);
            await fetchVideos();
            return;
        }

        if (job.status === "failed") {
            statusEl.innerHTML = "";
            const errorMsg = document.createElement("div");
            errorMsg.className = "status-error";
            errorMsg.textContent = job.error || "Le traitement a échoué.";
            statusEl.appendChild(errorMsg);
            renderWarnings(statusEl, job.warnings);
            return;
        }

        progressEl.textContent = `Tâche ${job.id} :${job.status}...`;

        if (!slowProcessingWarned && Date.now() - start > timeoutMs) {
            slowProcessingWarned = true;
            const timeoutMsg = document.createElement("div");
            timeoutMsg.className = "status-warning";
            timeoutMsg.textContent = "Le traitement est plus long que prévu. Merci de revenir un peu plus tard";
            statusEl.appendChild(timeoutMsg);
        }

        await sleep(pollIntervalMs);
    }
}


function createVideoItem(video) {
        const div = document.createElement("div");
        div.className = "video-item";
        if (video.is_private) {
            div.style.borderLeft = "4px solid #ffc107";
            div.style.paddingLeft = "10px";
        }

        const title = document.createElement("h3");
        title.textContent = video.filename;
        if (video.is_private) {
            const privateBadge = document.createElement("span");
            privateBadge.textContent = " [PRIVÉ]";
            privateBadge.style.color = "#ffc107";
            title.appendChild(privateBadge);
        }
        div.appendChild(title);
        
        // Editor controls
        if (isEditor) {
            const editorControls = document.createElement("div");
            editorControls.className = "editor-controls";
            editorControls.style.marginBottom = "10px";
            
            // Rename button
            const renameBtn = document.createElement("button");
            renameBtn.textContent = "Renommer";
            renameBtn.onclick = () => renameVideo(video.id);
            editorControls.appendChild(renameBtn);
            
            // Privacy toggle
            const privacyBtn = document.createElement("button");
            privacyBtn.textContent = video.is_private ? "Rendre public" : "Rendre privé";
            privacyBtn.onclick = () => togglePrivacy(video.id, !video.is_private);
            editorControls.appendChild(privacyBtn);
            
            // Delete button
            const deleteBtn = document.createElement("button");
            deleteBtn.textContent = "Supprimer";
            deleteBtn.style.backgroundColor = "#dc3545";
            deleteBtn.onclick = () => deleteVideo(video.id);
            editorControls.appendChild(deleteBtn);
            
            // Edit subtitle buttons
            if (video.available_subtitles && video.available_subtitles.length > 0) {
                video.available_subtitles.forEach((lang) => {
                    const editSubBtn = document.createElement("button");
                    editSubBtn.textContent = `Éditer sous-titres (${lang.toUpperCase()})`;
                    editSubBtn.onclick = () => editSubtitle(video.id, lang);
                    editorControls.appendChild(editSubBtn);
                });
            }
            
            div.appendChild(editorControls);
        }

        const summary = document.createElement("div");
        summary.className = "video-summary";
        const subtitlesText =
            video.available_subtitles && video.available_subtitles.length > 0
             ? video.available_subtitles.map((code) => code.toUpperCase()).join(", ")
                : "aucun";
        const combinedText =
            video.available_combined_subtitles && video.available_combined_subtitles.length > 0
                ? video.available_combined_subtitles
                      .map((entry) => entry.toUpperCase().replace(/\+/g, " + "))
                      .join(", ")
                : "aucun";
        const audioDubText =
            video.available_dub_audios && video.available_dub_audios.length > 0
                ? video.available_dub_audios.map((code) => code.toUpperCase()).join(", ")
                : "aucun";   
        const dubsText =
            video.available_dubs && video.available_dubs.length > 0
                ? video.available_dubs.map((code) => code.toUpperCase()).join(", ")
                : "aucun";
        summary.textContent =
            `Sous-titres : ${subtitlesText} | ` +
            `Sous-titres combinés : ${combinedText} | ` +
            `Audio doublé : ${audioDubText} | ` +
            `Vidéos doublées : ${dubsText}`;
        div.appendChild(summary);

        const controls = document.createElement("div");
        controls.className = "video-controls";

        const playOriginalBtn = document.createElement("button");
        playOriginalBtn.textContent = "Lecture originale";
        playOriginalBtn.onclick = () => playVideo(video, { mode: "original" });
        controls.appendChild(playOriginalBtn);

        // Subtitle selection
        if (video.available_subtitles && video.available_subtitles.length > 0) {
            if (isEditor) {
                // Editors: simple button
                const btnSubs = document.createElement("button");
                btnSubs.textContent = "Lecture avec sous-titres";
                btnSubs.onclick = () => playVideo(video, { mode: "subs" });
                controls.appendChild(btnSubs);
            } else {
                // Viewers: subtitle selection dropdown
                const subtitleSelect = document.createElement("select");
                subtitleSelect.innerHTML = "<option value=''>Sans sous-titres</option>";
                video.available_subtitles.forEach((lang) => {
                    const option = document.createElement("option");
                    option.value = lang;
                    option.textContent = `Sous-titres (${lang.toUpperCase()})`;
                    subtitleSelect.appendChild(option);
                });
                subtitleSelect.onchange = () => {
                    const selectedLang = subtitleSelect.value;
                    if (selectedLang) {
                        playVideo(video, { mode: "subs", lang: selectedLang });
                    } else {
                        playVideo(video, { mode: "original" });
                    }
                };
                controls.appendChild(subtitleSelect);
            }
        }

        if (video.available_dubs && video.available_dubs.length > 0) {
            const dubWrapper = document.createElement("div");
            dubWrapper.className = "dub-buttons";
            video.available_dubs.forEach((lang) => {
                const btnDub = document.createElement("button");
                btnDub.textContent = `Lecture doublée (${lang.toUpperCase()})`;
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
                        `Télécharger les sous-titres (${lang.toUpperCase()})`,
                        `/videos/${video.id}/subs/${lang}`,
                        `${baseName}_${lang}.vtt`
                    )
                );

            });
        }

        if (video.available_combined_subtitles && video.available_combined_subtitles.length > 0) {
            video.available_combined_subtitles.forEach((entry) => {
                const langs = entry.split("+").map((code) => code.trim()).filter(Boolean);
                if (langs.length < 2) {
                    return;
                }
                const langParam = langs.join(",");
                const combinedLabel = langs.map((code) => code.toUpperCase()).join(" + ");
                downloads.appendChild(
                    createDownloadLink(
                        `Télécharger les sous-titres combinés (${combinedLabel})`,
                        `/videos/${video.id}/subs/combined?langs=${encodeURIComponent(langParam)}`,
                        `${baseName}_${langs.join("_")}_combined.vtt`
                    )
                );
            });
        }

        if (video.available_dub_audios && video.available_dub_audios.length > 0) {
            video.available_dub_audios.forEach((lang) => {
                downloads.appendChild(
                    createDownloadLink(
                        `Télécharger l'audio doublé (${lang.toUpperCase()})`,
                        `/videos/${video.id}/dub-audio/${lang}`,
                        `${baseName}_dub_${lang}.mp3`
                    )
                );
            });
        }

        if (!downloads.hasChildNodes()) {
            downloads.textContent = "Aucun téléchargement disponible.";
        }

        div.appendChild(downloads);
        return div;
}

function buildFolderTree(videos, folders) {
    // Build folder structure
    const tree = {};
    const rootVideos = [];
    
    // Helper to get or create folder node
    function getOrCreateFolder(path) {
        const parts = path.split('/').filter(p => p);
        let current = tree;
        let fullPath = '';
        
        parts.forEach((part, index) => {
            fullPath = fullPath ? `${fullPath}/${part}` : part;
            if (!current[part]) {
                // Find folder info
                const folderInfo = folders.find(f => f.path === fullPath);
                current[part] = {
                    type: 'folder',
                    name: part,
                    path: fullPath,
                    isPrivate: folderInfo ? folderInfo.is_private : false,
                    children: {},
                    videos: []
                };
            }
            current = current[part].children;
        });
        
        // Return the folder node
        let node = tree;
        parts.forEach(part => {
            if (node[part]) {
                node = node[part];
            }
        });
        return node;
    }
    
    // Add ALL folders to tree (even empty ones)
    folders.forEach(folder => {
        getOrCreateFolder(folder.path);
    });
    
    // Add videos to tree
    videos.forEach(video => {
        if (video.folder_path) {
            const folderNode = getOrCreateFolder(video.folder_path);
            if (folderNode && folderNode.videos) {
                folderNode.videos.push(video);
            }
        } else {
            rootVideos.push(video);
        }
    });
    
    return { tree, rootVideos };
}

function renderFolder(folderData, container, level = 0) {
    const folderDiv = document.createElement("div");
    folderDiv.className = "folder-item";
    folderDiv.style.marginLeft = `${level * 20}px`;
    folderDiv.style.marginBottom = "10px";
    
    const folderHeader = document.createElement("div");
    folderHeader.style.cursor = "pointer";
    folderHeader.style.padding = "5px";
    folderHeader.style.backgroundColor = "#f0f0f0";
    folderHeader.style.border = "1px solid #ccc";
    folderHeader.style.borderRadius = "3px";
    folderHeader.style.display = "flex";
    folderHeader.style.justifyContent = "space-between";
    folderHeader.style.alignItems = "center";
    
    const folderLeft = document.createElement("div");
    folderLeft.style.display = "flex";
    folderLeft.style.alignItems = "center";
    
    const folderIcon = document.createElement("span");
    folderIcon.textContent = "📁 ";
    folderIcon.style.marginRight = "5px";
    folderLeft.appendChild(folderIcon);
    
    const folderName = document.createElement("span");
    folderName.textContent = folderData.name;
    if (folderData.isPrivate) {
        folderName.innerHTML += " <span style='color: #ffc107;'>[PRIVÉ]</span>";
    }
    folderLeft.appendChild(folderName);
    folderHeader.appendChild(folderLeft);
    
    // Editor controls for folder
    if (isEditor) {
        const folderControls = document.createElement("div");
        folderControls.style.display = "flex";
        folderControls.style.gap = "5px";
        folderControls.style.marginLeft = "10px";
        
        // Privacy toggle
        const privacyBtn = document.createElement("button");
        privacyBtn.textContent = folderData.isPrivate ? "Rendre public" : "Rendre privé";
        privacyBtn.style.fontSize = "0.8em";
        privacyBtn.style.padding = "3px 8px";
        privacyBtn.onclick = (e) => {
            e.stopPropagation();
            toggleFolderPrivacy(folderData.path, !folderData.isPrivate);
        };
        folderControls.appendChild(privacyBtn);
        
        // Upload video button
        const uploadBtn = document.createElement("button");
        uploadBtn.textContent = "Télécharger vidéo";
        uploadBtn.style.fontSize = "0.8em";
        uploadBtn.style.padding = "3px 8px";
        uploadBtn.onclick = (e) => {
            e.stopPropagation();
            uploadVideoToFolder(folderData.path);
        };
        folderControls.appendChild(uploadBtn);
        
        folderHeader.appendChild(folderControls);
    }
    
    const contentDiv = document.createElement("div");
    contentDiv.className = "folder-content";
    contentDiv.style.display = "none";
    contentDiv.style.marginLeft = "20px";
    contentDiv.style.marginTop = "5px";
    
    let isExpanded = false;
    folderLeft.addEventListener("click", () => {
        isExpanded = !isExpanded;
        contentDiv.style.display = isExpanded ? "block" : "none";
        folderIcon.textContent = isExpanded ? "📂 " : "📁 ";
    });
    
    // Render subfolders
    Object.values(folderData.children).forEach(child => {
        if (child.type === 'folder') {
            renderFolder(child, contentDiv, level + 1);
        }
    });
    
    // Render videos in this folder
    folderData.videos.forEach(video => {
        const videoDiv = createVideoItem(video);
        contentDiv.appendChild(videoDiv);
    });
    
    // Show message if folder is empty
    if (Object.keys(folderData.children).length === 0 && folderData.videos.length === 0) {
        const emptyMsg = document.createElement("div");
        emptyMsg.textContent = "Dossier vide";
        emptyMsg.style.color = "#999";
        emptyMsg.style.fontStyle = "italic";
        emptyMsg.style.padding = "10px";
        contentDiv.appendChild(emptyMsg);
    }
    
    folderDiv.appendChild(folderHeader);
    folderDiv.appendChild(contentDiv);
    container.appendChild(folderDiv);
}

async function fetchVideos() {
    const container = document.getElementById("video-list");
    container.innerHTML = "";
    
    let videos = [];
    let folders = [];
    
    try {
        const videosRes = await fetch("/api/videos");
        if (!videosRes.ok) {
            throw new Error(`Request failed with status ${videosRes.status}`);
        }
        videos = await videosRes.json();
        
        if (isEditor) {
            const foldersRes = await fetch("/api/folders");
            if (foldersRes.ok) {
                folders = await foldersRes.json();
            }
        }
    } catch (error) {
        console.error("Failed to load processed videos", error);
        container.textContent = "Impossible de charger la liste des vidéos pour le moment.";
        return;
    }

    if (!Array.isArray(videos) || videos.length === 0) {
        container.textContent = "Aucune vidéo n'a encore été traitée.";
        return;
    }

    // Build folder tree
    const { tree, rootVideos } = buildFolderTree(videos, folders);
    
    // Render root folders
    Object.values(tree).forEach(folderData => {
        if (folderData.type === 'folder') {
            renderFolder(folderData, container, 0);
        }
    });
    
    // Render root videos (videos without folder)
    rootVideos.forEach(video => {
        const videoDiv = createVideoItem(video);
        container.appendChild(videoDiv);
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
const fullscreenButton = document.getElementById("fullscreen-button")

function clearSubtitleOverlay() {
    const overlay = document.getElementById("subtitle-overlay");
    overlay.innerHTML = "";
    overlay.classList.add("hidden");
}

function requestContainerFullscreen() {
    if (!videoContainer || !videoContainer.requestFullscreen) {
        return Promise.reject(new Error("Fullscreen API non disponible"));
    }
    return videoContainer.requestFullscreen();
}

function handleFullscreenChange() {
    const fullscreenElement = document.fullscreenElement;
    if (fullscreenElement === videoEl && document.exitFullscreen) {
        document
            .exitFullscreen()
            .then(() => requestContainerFullscreen())
            .catch(() => {});
    }
}

document.addEventListener("fullscreenchange", handleFullscreenChange);

if (fullscreenButton) {
    fullscreenButton.addEventListener("click", () => {
        requestContainerFullscreen().catch(() => {});
    });
}

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
            infoEl.textContent = "Aucun sous-titre disponible.";
        } else {
            try {
                // If specific lang is provided (for viewers), only load that one
                const langsToLoad = options.lang 
                    ? [options.lang] 
                    : video.available_subtitles;
                
                const subtitles = await Promise.all(
                    langsToLoad.map(async (lang) => {
                        const res = await fetch(`/videos/${video.id}/subs/${lang}`);
                        if (!res.ok) {
                            throw new Error(`Impossible de charger les sous-titres pour ${lang}`);
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

                infoEl.textContent = `Lecture avec sous-titres (${video.available_subtitles
                    .map((code) => code.toUpperCase())
                    .join(", ")})`;
            } catch (err) {
                console.error(err);
                infoEl.textContent = "Impossible de charger les sous-titres.";
            }
        }
    } else if (mode === "dub") {
        const selectedLang = options.lang || (video.available_dubs ? video.available_dubs[0] : null);
        if (!selectedLang) {
            infoEl.textContent = "Aucun doublage disponible.";
        } else {
            videoEl.src = `/videos/${video.id}/dub/${selectedLang}`;
            infoEl.textContent = `Lecture avec doublage (${selectedLang.toUpperCase()})`;
        }
    } else {
        infoEl.textContent = "Lecture de la piste originale.";
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
const fileInput = document.getElementById("video-file");
const fileNameLabel = document.getElementById("selected-file-name");
if (fileInput && fileNameLabel) {
    const defaultLabel = fileNameLabel.textContent || "Aucun fichier sélectionné";
    const updateFileLabel = () => {
        if (fileInput.files && fileInput.files.length > 0) {
            fileNameLabel.textContent = fileInput.files[0].name;
        } else {
            fileNameLabel.textContent = defaultLabel;
        }
    };
    fileInput.addEventListener("change", updateFileLabel);
}

// Editor functions
async function renameVideo(videoId) {
    const newName = prompt("Entrez le nouveau nom de la vidéo:");
    if (!newName) return;
    
    try {
        const formData = new FormData();
        formData.append("new_filename", newName);
        
        const res = await fetch(`/api/videos/${videoId}/rename`, {
            method: "PUT",
            body: formData,
        });
        
        if (!res.ok) {
            const err = await res.json();
            alert("Erreur : " + (err.error || res.statusText));
            return;
        }
        
        alert("Vidéo renommée avec succès!");
        fetchVideos();
    } catch (err) {
        console.error(err);
        alert("Erreur lors du renommage.");
    }
}

async function togglePrivacy(videoId, isPrivate) {
    try {
        const formData = new FormData();
        formData.append("is_private", isPrivate);
        
        const res = await fetch(`/api/videos/${videoId}/privacy`, {
            method: "PUT",
            body: formData,
        });
        
        if (!res.ok) {
            const err = await res.json();
            alert("Erreur : " + (err.error || res.statusText));
            return;
        }
        
        alert("Confidentialité mise à jour!");
        fetchVideos();
    } catch (err) {
        console.error(err);
        alert("Erreur lors de la mise à jour de la confidentialité.");
    }
}

async function deleteVideo(videoId) {
    if (!confirm("Êtes-vous sûr de vouloir supprimer cette vidéo? Cette action est irréversible.")) {
        return;
    }
    
    try {
        const res = await fetch(`/api/videos/${videoId}`, {
            method: "DELETE",
        });
        
        if (!res.ok) {
            const err = await res.json();
            alert("Erreur : " + (err.error || res.statusText));
            return;
        }
        
        alert("Vidéo supprimée avec succès!");
        fetchVideos();
    } catch (err) {
        console.error(err);
        alert("Erreur lors de la suppression.");
    }
}

async function editSubtitle(videoId, lang) {
    try {
        const res = await fetch(`/api/videos/${videoId}/subs/${lang}/edit`);
        if (!res.ok) {
            const err = await res.json();
            alert("Erreur : " + (err.error || res.statusText));
            return;
        }
        
        const data = await res.json();
        const content = prompt("Modifiez le contenu des sous-titres (format VTT):", data.content);
        if (content === null) return;
        
        const formData = new FormData();
        formData.append("content", content);
        
        const saveRes = await fetch(`/api/videos/${videoId}/subs/${lang}/edit`, {
            method: "PUT",
            body: formData,
        });
        
        if (!saveRes.ok) {
            const err = await saveRes.json();
            alert("Erreur : " + (err.error || saveRes.statusText));
            return;
        }
        
        alert("Sous-titres mis à jour avec succès!");
        fetchVideos();
    } catch (err) {
        console.error(err);
        alert("Erreur lors de l'édition des sous-titres.");
    }
}

async function toggleFolderPrivacy(folderPath, isPrivate) {
    try {
        const formData = new FormData();
        formData.append("is_private", isPrivate);
        
        const res = await fetch(`/api/folders/${encodeURIComponent(folderPath)}/privacy`, {
            method: "PUT",
            body: formData,
        });
        
        if (!res.ok) {
            const err = await res.json();
            alert("Erreur : " + (err.error || res.statusText));
            return;
        }
        
        alert("Confidentialité du dossier mise à jour!");
        fetchVideos();
    } catch (err) {
        console.error(err);
        alert("Erreur lors de la mise à jour de la confidentialité du dossier.");
    }
}

async function uploadVideoToFolder(folderPath) {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "video/*";
    input.onchange = async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        
        try {
            const formData = new FormData();
            formData.append("file", file);
            
            const res = await fetch(`/api/folders/${encodeURIComponent(folderPath)}/upload`, {
                method: "POST",
                body: formData,
            });
            
            if (!res.ok) {
                const err = await res.json();
                alert("Erreur : " + (err.error || res.statusText));
                return;
            }
            
            alert("Vidéo téléchargée avec succès!");
            fetchVideos();
        } catch (err) {
            console.error(err);
            alert("Erreur lors du téléchargement de la vidéo.");
        }
    };
    input.click();
}

// Upload form handler (only for editors)
const uploadForm = document.getElementById("upload-form");
if (uploadForm && isEditor) {
    uploadForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const form = e.target;
        const statusEl = document.getElementById("upload-status");
        statusEl.textContent = "Téléversement et traitement en cours... Cela peut prendre un moment.";

        const formData = new FormData(form);
        const checkedLangs = [...form.querySelectorAll("input[name='languages']:checked")];
        const checkedOptions = [...form.querySelectorAll("input[name='process_options']:checked")];
        
        // Add folder path and privacy
        const folderPath = document.getElementById("folder-path-input")?.value || "";
        const isPrivate = document.getElementById("is-private-checkbox")?.checked || false;
        if (folderPath) {
            formData.append("folder_path", folderPath);
        }
        formData.append("is_private", isPrivate);

        if (checkedLangs.length === 0 || checkedLangs.length > 2) {
            alert("Veuillez sélectionner une ou deux langues cibles.");
            return;
        }

        if (checkedOptions.length === 0) {
            alert("Veuillez sélectionner au moins une option de traitement.");
            return;
        }

        try {
            const res = await fetch("/api/upload", {
                method: "POST",
                body: formData,
            });

            if (!res.ok) {
                const err = await res.json();
                statusEl.textContent = "Erreur : " + (err.error || res.statusText);
                return;
            }

            const data = await res.json();
            statusEl.innerHTML = "";
            const queuedMsg = document.createElement("div");
            queuedMsg.textContent = `Téléversement réussi. La vidéo ${data.id} est en cours de traitement...`;
            statusEl.appendChild(queuedMsg);

            await pollJobStatus(data.id, statusEl);
        } catch (err) {
            console.error(err);
            statusEl.textContent = "Erreur inconnue.";
        }
    });
}

// Folder management (editors only)
if (isEditor) {
    const createFolderBtn = document.getElementById("create-folder-btn-top") || document.getElementById("create-folder-btn");
    if (createFolderBtn) {
        createFolderBtn.addEventListener("click", async () => {
            const folderPath = prompt("Entrez le chemin du dossier (ex: projets/2024):");
            if (!folderPath) return;
            
            const isPrivate = confirm("Voulez-vous rendre ce dossier privé?");
            
            try {
                const formData = new FormData();
                formData.append("folder_path", folderPath);
                formData.append("is_private", isPrivate);
                
                const res = await fetch("/api/folders", {
                    method: "POST",
                    body: formData,
                });
                
                if (!res.ok) {
                    const err = await res.json();
                    alert("Erreur : " + (err.error || res.statusText));
                    return;
                }
                
                alert("Dossier créé avec succès!");
                fetchVideos();
            } catch (err) {
                console.error(err);
                alert("Erreur lors de la création du dossier.");
            }
        });
    }
}

window.addEventListener("load", fetchVideos);
