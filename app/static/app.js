// Check if user is editor
const isEditor = document.getElementById("role-indicator")?.textContent.includes("I-tech privé") || false;

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

// Progress bar and time estimation functions
function createProgressBar(container) {
    const progressContainer = document.createElement("div");
    progressContainer.className = "progress-container";
    
    const progressText = document.createElement("div");
    progressText.className = "progress-text";
    progressText.textContent = "0%";
    
    const progressBarWrapper = document.createElement("div");
    progressBarWrapper.className = "progress-bar-wrapper";
    
    const progressBar = document.createElement("div");
    progressBar.className = "progress-bar";
    progressBar.style.width = "0%";
    
    const timeRemaining = document.createElement("div");
    timeRemaining.className = "time-remaining";
    timeRemaining.textContent = "Calcul en cours...";
    
    progressBarWrapper.appendChild(progressBar);
    progressContainer.appendChild(progressText);
    progressContainer.appendChild(progressBarWrapper);
    progressContainer.appendChild(timeRemaining);
    
    container.appendChild(progressContainer);
    
    return {
        container: progressContainer,
        text: progressText,
        bar: progressBar,
        timeRemaining: timeRemaining
    };
}

function updateProgress(progressObj, percentage, elapsedSeconds = 0) {
    const clampedPercentage = Math.max(0, Math.min(100, percentage));
    progressObj.bar.style.width = clampedPercentage + "%";
    progressObj.text.textContent = Math.round(clampedPercentage) + "%";
    
    // Calculate estimated time remaining
    if (elapsedSeconds > 0 && clampedPercentage > 0 && clampedPercentage < 100) {
        const estimatedTotalSeconds = (elapsedSeconds / clampedPercentage) * 100;
        const remainingSeconds = estimatedTotalSeconds - elapsedSeconds;
        
        if (remainingSeconds > 0) {
            const hours = Math.floor(remainingSeconds / 3600);
            const minutes = Math.floor((remainingSeconds % 3600) / 60);
            const seconds = Math.floor(remainingSeconds % 60);
            
            let timeString = "";
            if (hours > 0) {
                timeString = `${hours}h ${minutes}m`;
            } else if (minutes > 0) {
                timeString = `${minutes}m ${seconds}s`;
            } else {
                timeString = `${seconds}s`;
            }
            
            progressObj.timeRemaining.textContent = `Temps restant estimé: ${timeString}`;
        } else {
            progressObj.timeRemaining.textContent = "Presque terminé...";
        }
    } else if (clampedPercentage >= 100) {
        progressObj.timeRemaining.textContent = "Terminé!";
    } else {
        progressObj.timeRemaining.textContent = "Calcul en cours...";
    }
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

async function pollJobStatus(jobId, statusEl, progressObj = null, startTime = null) {
    const pollIntervalMs = 3000;
    const timeoutMs = 10 * 60 * 1000; // 10 minuten
    const start = startTime || Date.now();
    let slowProcessingWarned = false;
    
    // Use existing progress bar or create message
    if (!progressObj) {
        const progressEl = document.createElement("div");
        progressEl.textContent = `La tâche ${jobId} est en attente...`;
        statusEl.appendChild(progressEl);
    }
    
    // Progress starts at 20% (upload complete), goes to 100% during processing
    let currentProgress = 20;

    while (true) {
        let job;
        try {
            const res = await fetch(`/api/jobs/${jobId}`);
            if (!res.ok) {
                throw new Error(`status ${res.status}`);
            }
            job = await res.json();
        } catch (err) {
            // Clear progress interval
            if (statusEl.dataset.progressInterval) {
                clearInterval(parseInt(statusEl.dataset.progressInterval));
            }
            
            if (progressObj) {
                statusEl.innerHTML = "";
            } else {
                const progressEl = statusEl.querySelector("div") || document.createElement("div");
                progressEl.className = "status-error";
                progressEl.textContent = `Impossible de vérifier l'état ${err.message}`;
                if (!statusEl.contains(progressEl)) {
                    statusEl.appendChild(progressEl);
                }
            }
            return;
        }

        if (job.status === "completed") {
            // Clear progress interval
            if (statusEl.dataset.progressInterval) {
                clearInterval(parseInt(statusEl.dataset.progressInterval));
            }
            
            // Show 100% complete
            if (progressObj) {
                updateProgress(progressObj, 100, (Date.now() - start) / 1000);
                await sleep(500); // Brief pause to show 100%
            }
            
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
            // Clear progress interval
            if (statusEl.dataset.progressInterval) {
                clearInterval(parseInt(statusEl.dataset.progressInterval));
            }
            
            statusEl.innerHTML = "";
            const errorMsg = document.createElement("div");
            errorMsg.className = "status-error";
            errorMsg.textContent = job.error || "Le traitement a échoué.";
            statusEl.appendChild(errorMsg);
            renderWarnings(statusEl, job.warnings);
            return;
        }

        // Update progress: gradually increase from 20% to 95% during processing
        const elapsedSeconds = (Date.now() - start) / 1000;
        // Estimate progress based on time (rough estimate)
        // Start at 20%, gradually increase, but cap at 95% until completed
        currentProgress = Math.min(95, 20 + (elapsedSeconds / 120) * 75); // Rough estimate: 2 minutes to reach 95%
        
        if (progressObj) {
            statusEl.dataset.currentProgress = currentProgress.toString();
            updateProgress(progressObj, currentProgress, elapsedSeconds);
        } else {
            const progressEl = statusEl.querySelector("div") || document.createElement("div");
            progressEl.textContent = `Tâche ${job.id} :${job.status}... (${Math.round(currentProgress)}%)`;
            if (!statusEl.contains(progressEl)) {
                statusEl.appendChild(progressEl);
            }
        }

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

        const fileType = video.file_type || "video";
        const isVideo = fileType === "video";

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
            
            // Edit subtitle buttons (only for videos)
            if (isVideo && video.available_subtitles && video.available_subtitles.length > 0) {
                video.available_subtitles.forEach((lang) => {
                    const editSubBtn = document.createElement("button");
                    editSubBtn.textContent = `Éditer sous-titres (${lang.toUpperCase()})`;
                    editSubBtn.onclick = () => editSubtitle(video.id, lang);
                    editorControls.appendChild(editSubBtn);
                });
            }
            
            div.appendChild(editorControls);
        }

        // Controls (only for videos)
        if (isVideo) {
            const controls = document.createElement("div");
            controls.className = "video-controls";

            // Subtitle selection with multiple checkboxes
            const selectedLangs = new Set();
            let selectedAudioMode = "original"; // "original" or "dub"
            let selectedDubLang = null;
            
            if (video.available_subtitles && video.available_subtitles.length > 0) {
                const subtitleWrapper = document.createElement("div");
                subtitleWrapper.style.marginTop = "10px";
                subtitleWrapper.style.marginBottom = "10px";
                subtitleWrapper.style.display = "block";
                
                const subtitleLabel = document.createElement("label");
                subtitleLabel.textContent = "Sous-titres: ";
                subtitleLabel.style.marginRight = "10px";
                subtitleLabel.style.display = "inline-block";
                subtitleWrapper.appendChild(subtitleLabel);
                
                video.available_subtitles.forEach((lang) => {
                    const checkbox = document.createElement("input");
                    checkbox.type = "checkbox";
                    checkbox.value = lang;
                    checkbox.id = `subtitle-${video.id}-${lang}`;
                    checkbox.style.marginRight = "5px";
                    checkbox.style.marginLeft = "10px";
                    
                    const langLabel = document.createElement("label");
                    langLabel.htmlFor = checkbox.id;
                    langLabel.textContent = lang.toUpperCase();
                    langLabel.style.marginRight = "10px";
                    langLabel.style.cursor = "pointer";
                    
                    checkbox.addEventListener("change", () => {
                        if (checkbox.checked) {
                            selectedLangs.add(lang);
                        } else {
                            selectedLangs.delete(lang);
                        }
                    });
                    
                    subtitleWrapper.appendChild(checkbox);
                    subtitleWrapper.appendChild(langLabel);
                });
                
                // Voeg eerst subtitleWrapper toe
                controls.appendChild(subtitleWrapper);
            }

            // Audio selection - komt ONDER ondertiteling
            const audioWrapper = document.createElement("div");
            audioWrapper.style.marginTop = "10px";
            audioWrapper.style.marginBottom = "10px";
            audioWrapper.style.display = "block";
            
            const audioLabel = document.createElement("label");
            audioLabel.textContent = "Audio: ";
            audioLabel.style.marginRight = "10px";
            audioLabel.style.display = "inline-block";
            audioWrapper.appendChild(audioLabel);
            
            // Original audio option
            const originalRadio = document.createElement("input");
            originalRadio.type = "radio";
            originalRadio.name = `audio-${video.id}`;
            originalRadio.id = `audio-original-${video.id}`;
            originalRadio.value = "original";
            originalRadio.checked = true;
            originalRadio.style.marginRight = "5px";
            originalRadio.style.marginLeft = "10px";
            originalRadio.addEventListener("change", () => {
                if (originalRadio.checked) {
                    selectedAudioMode = "original";
                    selectedDubLang = null;
                }
            });
            audioWrapper.appendChild(originalRadio);
            
            const originalLabel = document.createElement("label");
            originalLabel.htmlFor = originalRadio.id;
            originalLabel.textContent = "Originale";
            originalLabel.style.marginRight = "15px";
            originalLabel.style.cursor = "pointer";
            audioWrapper.appendChild(originalLabel);
            
            // Dub audio options - combine available_dubs and available_dub_audios
            const allDubLangs = new Set();
            if (video.available_dubs && video.available_dubs.length > 0) {
                video.available_dubs.forEach(lang => allDubLangs.add(lang));
            }
            if (video.available_dub_audios && video.available_dub_audios.length > 0) {
                video.available_dub_audios.forEach(lang => allDubLangs.add(lang));
            }
            
            if (allDubLangs.size > 0) {
                Array.from(allDubLangs).sort().forEach((lang) => {
                    const dubRadio = document.createElement("input");
                    dubRadio.type = "radio";
                    dubRadio.name = `audio-${video.id}`;
                    dubRadio.id = `audio-dub-${video.id}-${lang}`;
                    dubRadio.value = `dub-${lang}`;
                    dubRadio.style.marginRight = "5px";
                    dubRadio.style.marginLeft = "10px";
                    dubRadio.addEventListener("change", () => {
                        if (dubRadio.checked) {
                            selectedAudioMode = "dub";
                            selectedDubLang = lang;
                        }
                    });
                    audioWrapper.appendChild(dubRadio);
                    
                    const dubLabel = document.createElement("label");
                    dubLabel.htmlFor = dubRadio.id;
                    dubLabel.textContent = `Lecture doublée (${lang.toUpperCase()})`;
                    dubLabel.style.marginRight = "15px";
                    dubLabel.style.cursor = "pointer";
                    audioWrapper.appendChild(dubLabel);
                });
            }
            
            // Voeg audioWrapper toe (onder ondertiteling)
            controls.appendChild(audioWrapper);

            // Play button - komt ONDER Audio
            const playOriginalBtn = document.createElement("button");
            playOriginalBtn.textContent = "Lire";
            playOriginalBtn.style.marginTop = "10px";
            playOriginalBtn.style.display = "block";
            playOriginalBtn.onclick = () => {
                if (selectedAudioMode === "dub" && selectedDubLang) {
                    // Check if this language has a full video dub available (not just audio)
                    if (!video.available_dubs || !video.available_dubs.includes(selectedDubLang)) {
                        alert(`Le doublage vidéo n'est pas disponible pour ${selectedDubLang.toUpperCase()}. Seul l'audio doublé (MP3) est disponible pour cette langue.`);
                        return;
                    }
                    // Play with dubbing
                    if (selectedLangs.size > 0) {
                        playVideo(video, { mode: "dub", lang: selectedDubLang, langs: Array.from(selectedLangs) });
                    } else {
                        playVideo(video, { mode: "dub", lang: selectedDubLang });
                    }
                } else {
                    // Play original
                    if (selectedLangs.size > 0) {
                        playVideo(video, { mode: "subs", langs: Array.from(selectedLangs) });
                    } else {
                        playVideo(video, { mode: "original" });
                    }
                }
            };
            controls.appendChild(playOriginalBtn);

            div.appendChild(controls);
        }

        const downloads = document.createElement("div");
        downloads.className = "download-links";

        const baseName = filenameWithoutExtension(video.filename);
        
        // Cache video for offline playback in the app (with subtitles) - only for videos
        if (isVideo && "caches" in window) {
            const cacheVideoBtn = document.createElement("button");
            cacheVideoBtn.textContent = "💾 Mettre en cache pour lecture hors ligne (dans l'app)";
            cacheVideoBtn.style.marginBottom = "10px";
            cacheVideoBtn.style.padding = "8px";
            cacheVideoBtn.style.backgroundColor = "#17a2b8";
            cacheVideoBtn.style.color = "white";
            cacheVideoBtn.style.border = "none";
            cacheVideoBtn.style.borderRadius = "3px";
            cacheVideoBtn.style.cursor = "pointer";
            cacheVideoBtn.onclick = async () => {
                if (confirm("Mettre cette vidéo en cache pour lecture hors ligne dans l'application? Les sous-titres fonctionneront normalement. Cela utilisera de l'espace de stockage.")) {
                    await preloadVideoForOffline(video.id, "original", null, video.available_subtitles || []);
                    fetchVideos(); // Refresh to show cache status
                }
            };
            downloads.appendChild(cacheVideoBtn);
            
            // Show cache status
            isVideoCached(video.id, "original").then(cached => {
                if (cached) {
                    const cacheStatus = document.createElement("div");
                    cacheStatus.textContent = "✓ Vidéo mise en cache - disponible hors ligne";
                    cacheStatus.style.color = "#28a745";
                    cacheStatus.style.fontSize = "0.9em";
                    cacheStatus.style.marginTop = "5px";
                    downloads.appendChild(cacheStatus);
                }
            });
        }
        
        // Download file (video, audio, or text) as file (for external use)
        const downloadFileBtn = document.createElement("button");
        downloadFileBtn.textContent = "📥 Télécharger";
        downloadFileBtn.style.marginBottom = "5px";
        downloadFileBtn.style.padding = "8px";
        downloadFileBtn.style.backgroundColor = "#6c757d";
        downloadFileBtn.style.color = "white";
        downloadFileBtn.style.border = "none";
        downloadFileBtn.style.borderRadius = "3px";
        downloadFileBtn.style.cursor = "pointer";
        downloadFileBtn.onclick = () => {
            const link = document.createElement("a");
            if (fileType === "video") {
                link.href = `/videos/${encodeURIComponent(video.id)}/original`;
                link.download = `${baseName}_original.mp4`;
            } else {
                // For audio/text files, use the /files endpoint
                // Check if this is a loose file (ID contains underscores from path) or processed file
                // For loose files, use the filename directly
                if (video.id.includes("_") && !video.id.match(/^[a-f0-9-]{36}$/)) {
                    // This is likely a loose file - use filename directly
                    link.href = `/files/${encodeURIComponent(video.id)}/${encodeURIComponent(video.filename)}`;
                } else {
                    // This is a processed file - use original extension
                    link.href = `/files/${encodeURIComponent(video.id)}/original${fileType === "audio" ? ".mp3" : ".txt"}`;
                }
                link.download = video.filename;
            }
            link.click();
        };
        downloads.appendChild(downloadFileBtn);
        
        // Cache dubbed videos for offline playback
        if (video.available_dubs && video.available_dubs.length > 0 && "caches" in window) {
            video.available_dubs.forEach((lang) => {
                const cacheDubBtn = document.createElement("button");
                cacheDubBtn.textContent = `💾 Mettre en cache doublage (${lang.toUpperCase()})`;
                cacheDubBtn.style.marginBottom = "5px";
                cacheDubBtn.style.marginRight = "5px";
                cacheDubBtn.style.padding = "8px";
                cacheDubBtn.style.backgroundColor = "#17a2b8";
                cacheDubBtn.style.color = "white";
                cacheDubBtn.style.border = "none";
                cacheDubBtn.style.borderRadius = "3px";
                cacheDubBtn.style.cursor = "pointer";
                cacheDubBtn.onclick = async () => {
                    if (confirm(`Mettre la vidéo doublée (${lang.toUpperCase()}) en cache pour lecture hors ligne?`)) {
                        await preloadVideoForOffline(video.id, "dub", lang, video.available_subtitles || []);
                        fetchVideos();
                    }
                };
                downloads.appendChild(cacheDubBtn);
            });
        }

        if (video.available_subtitles && video.available_subtitles.length > 0) {
            // If 2 languages available, download combined, otherwise download first language
            const downloadSubtitlesBtn = document.createElement("button");
            if (video.available_subtitles.length >= 2) {
                const langParam = video.available_subtitles.slice(0, 2).join(",");
                downloadSubtitlesBtn.textContent = `📥 Télécharger les sous-titres (${video.available_subtitles.slice(0, 2).map(l => l.toUpperCase()).join(" + ")})`;
                downloadSubtitlesBtn.onclick = () => {
                    const link = document.createElement("a");
                    link.href = `/videos/${encodeURIComponent(video.id)}/subs/combined?langs=${encodeURIComponent(langParam)}`;
                    link.download = `${baseName}_${video.available_subtitles.slice(0, 2).join("_")}.vtt`;
                    link.click();
                };
            } else {
                downloadSubtitlesBtn.textContent = `📥 Télécharger les sous-titres (${video.available_subtitles[0].toUpperCase()})`;
                downloadSubtitlesBtn.onclick = () => {
                    const link = document.createElement("a");
                    link.href = `/videos/${encodeURIComponent(video.id)}/subs/${encodeURIComponent(video.available_subtitles[0])}`;
                    link.download = `${baseName}_${video.available_subtitles[0]}.vtt`;
                    link.click();
                };
            }
            downloadSubtitlesBtn.style.marginBottom = "5px";
            downloadSubtitlesBtn.style.padding = "8px";
            downloadSubtitlesBtn.style.backgroundColor = "#6c757d";
            downloadSubtitlesBtn.style.color = "white";
            downloadSubtitlesBtn.style.border = "none";
            downloadSubtitlesBtn.style.borderRadius = "3px";
            downloadSubtitlesBtn.style.cursor = "pointer";
            downloads.appendChild(downloadSubtitlesBtn);
        }

        if (video.available_dub_audios && video.available_dub_audios.length > 0) {
            video.available_dub_audios.forEach((lang) => {
                downloads.appendChild(
                    createDownloadLink(
                        `Télécharger l'audio doublé (${lang.toUpperCase()})`,
                        `/videos/${encodeURIComponent(video.id)}/dub-audio/${encodeURIComponent(lang)}`,
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
                    color: folderInfo ? (folderInfo.color || "#f0f0f0") : "#f0f0f0",
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
    folderHeader.style.backgroundColor = folderData.color || "#f0f0f0";
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
        
        // Upload file button (video, audio, or text)
        const uploadBtn = document.createElement("button");
        uploadBtn.textContent = "Télécharger";
        uploadBtn.style.fontSize = "0.8em";
        uploadBtn.style.padding = "3px 8px";
        uploadBtn.onclick = (e) => {
            e.stopPropagation();
            uploadFileToFolder(folderData.path);
        };
        folderControls.appendChild(uploadBtn);
        
        // Change color button
        const colorBtn = document.createElement("button");
        colorBtn.textContent = "Changer couleur";
        colorBtn.style.fontSize = "0.8em";
        colorBtn.style.padding = "3px 8px";
        colorBtn.onclick = (e) => {
            e.stopPropagation();
            changeFolderColor(folderData.path, folderData.color || "#f0f0f0");
        };
        folderControls.appendChild(colorBtn);
        
        folderHeader.appendChild(folderControls);
    }
    
    const contentDiv = document.createElement("div");
    contentDiv.className = "folder-content";
    contentDiv.style.display = "none";
    contentDiv.style.marginLeft = "20px";
    contentDiv.style.marginTop = "5px";
    
    let isExpanded = false;
    // Make entire folder header clickable
    folderHeader.addEventListener("click", (e) => {
        // Don't expand if clicking on buttons
        if (e.target.tagName === "BUTTON" || e.target.closest("button")) {
            return;
        }
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
    
    // Always show content div when expanded, even if empty
    // Show message if folder is empty
    if (Object.keys(folderData.children).length === 0 && folderData.videos.length === 0) {
        const emptyMsg = document.createElement("div");
        emptyMsg.textContent = "Dossier vide";
        emptyMsg.style.color = "#999";
        emptyMsg.style.fontStyle = "italic";
        emptyMsg.style.padding = "10px";
        contentDiv.appendChild(emptyMsg);
    }
    
    // Auto-expand if folder is empty (so it's visible)
    if (Object.keys(folderData.children).length === 0 && folderData.videos.length === 0) {
        isExpanded = true;
        contentDiv.style.display = "block";
        folderIcon.textContent = "📂 ";
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
        
        // Always fetch folders (for both editors and viewers, but viewers only see public ones)
        const foldersRes = await fetch("/api/folders");
        if (foldersRes.ok) {
            folders = await foldersRes.json();
        }
    } catch (error) {
        console.error("Failed to load processed videos", error);
        container.textContent = "Impossible de charger la liste des vidéos pour le moment.";
        return;
    }

    // Build folder tree (even if no videos exist, show folders)
    const { tree, rootVideos } = buildFolderTree(videos, folders);
    
    // Render root folders (always show, even if empty)
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
    
    // Show message if no videos and no folders
    if (rootVideos.length === 0 && Object.keys(tree).length === 0) {
        container.textContent = "Aucune vidéo n'a encore été traitée.";
    }
    
    // Refresh folder dropdown after fetching videos
    populateFolderDropdown();
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

// Check if video is available offline (cached)
async function isVideoCached(videoId, mode = "original", lang = null) {
    if (!("caches" in window)) {
        return false;
    }
    
    try {
        const cache = await caches.open("video-cache");
        let url;
        if (mode === "dub" && lang) {
            url = `/videos/${encodeURIComponent(videoId)}/dub/${encodeURIComponent(lang)}`;
        } else {
            url = `/videos/${encodeURIComponent(videoId)}/original`;
        }
        const cached = await cache.match(url);
        return cached !== undefined;
    } catch (err) {
        return false;
    }
}

// Preload video and subtitles for offline playback in the app
async function preloadVideoForOffline(videoId, mode = "original", lang = null, availableSubtitles = []) {
    if (!("caches" in window)) {
        alert("Votre navigateur ne supporte pas le cache. La vidéo ne pourra pas être lue hors ligne.");
        return;
    }
    
    try {
        const cache = await caches.open("video-cache");
        let url;
        if (mode === "dub" && lang) {
            url = `/videos/${encodeURIComponent(videoId)}/dub/${encodeURIComponent(lang)}`;
        } else {
            url = `/videos/${encodeURIComponent(videoId)}/original`;
        }
        
        // Check cache size limit
        const estimate = await navigator.storage.estimate();
        const available = estimate.quota - estimate.usage;
        const minRequired = 100 * 1024 * 1024; // 100MB minimum
        
        if (available < minRequired) {
            if (!confirm("Peu d'espace de stockage disponible. Voulez-vous continuer quand même?")) {
                return;
            }
        }
        
        // Show progress
        const progressMsg = document.createElement("div");
        progressMsg.textContent = "Mise en cache en cours...";
        progressMsg.style.padding = "10px";
        progressMsg.style.backgroundColor = "#f0f0f0";
        progressMsg.style.marginTop = "10px";
        const infoEl = document.getElementById("player-info");
        if (infoEl) {
            infoEl.appendChild(progressMsg);
        }
        
        // Cache video
        const response = await fetch(url);
        if (response.ok) {
            await cache.put(url, response.clone());
            
            // Cache all available subtitles for this video
            if (availableSubtitles && availableSubtitles.length > 0) {
                progressMsg.textContent = "Mise en cache de la vidéo et des sous-titres...";
                for (const subLang of availableSubtitles) {
                    try {
                        const subResponse = await fetch(`/videos/${encodeURIComponent(videoId)}/subs/${encodeURIComponent(subLang)}`);
                        if (subResponse.ok) {
                            await cache.put(`/videos/${encodeURIComponent(videoId)}/subs/${encodeURIComponent(subLang)}`, subResponse.clone());
                        }
                    } catch (err) {
                        console.warn(`Failed to cache subtitles for ${subLang}:`, err);
                    }
                }
            }
            
            if (infoEl && progressMsg.parentNode) {
                progressMsg.remove();
            }
            alert("Vidéo et sous-titres mis en cache! Vous pouvez maintenant les lire hors ligne dans l'application.");
        } else {
            if (infoEl && progressMsg.parentNode) {
                progressMsg.remove();
            }
            alert("Impossible de mettre en cache la vidéo.");
        }
    } catch (err) {
        console.error("Cache error:", err);
        alert("Erreur lors de la mise en cache: " + err.message);
    }
}

async function playVideo(video, options = {}) {
    const infoEl = document.getElementById("player-info");
    const overlay = document.getElementById("subtitle-overlay");

    if (currentSubtitleCleanup) {
        currentSubtitleCleanup();
        currentSubtitleCleanup = null;
    }

    const mode = options.mode || "original";
    const lang = options.lang || null;
    
    // Determine video URL
    let videoUrl;
    if (mode === "dub" && lang) {
        videoUrl = `/videos/${encodeURIComponent(video.id)}/dub/${encodeURIComponent(lang)}`;
    } else {
        videoUrl = `/videos/${encodeURIComponent(video.id)}/original`;
    }
    
    // Check if video is cached for offline playback
    const isCached = await isVideoCached(video.id, mode, lang);
    
    // Try to use cached version if available (works offline)
    if (isCached && "caches" in window) {
        try {
            const cache = await caches.open("video-cache");
            const cachedResponse = await cache.match(videoUrl);
            if (cachedResponse) {
                const blob = await cachedResponse.blob();
                const blobUrl = URL.createObjectURL(blob);
                videoEl.src = blobUrl;
                infoEl.textContent = "Lecture hors ligne (mis en cache) - Les sous-titres fonctionnent normalement";
            } else {
                // Fallback to online if cache miss
                videoEl.src = videoUrl;
            }
        } catch (err) {
            console.error("Error loading cached video:", err);
            // Fallback to online on error
            videoEl.src = videoUrl;
        }
    } else {
        // Use online version (will fail if offline and not cached)
        videoEl.src = videoUrl;
        
        // If online fails and we have cache, try to use cache as fallback
        videoEl.addEventListener("error", async () => {
            if ("caches" in window) {
                try {
                    const cache = await caches.open("video-cache");
                    const cachedResponse = await cache.match(videoUrl);
                    if (cachedResponse) {
                        const blob = await cachedResponse.blob();
                        const blobUrl = URL.createObjectURL(blob);
                        videoEl.src = blobUrl;
                        infoEl.textContent = "Lecture hors ligne (mis en cache) - Les sous-titres fonctionnent normalement";
                    }
                } catch (err) {
                    console.error("Error loading cached video as fallback:", err);
                }
            }
        }, { once: true });
    }
    
    clearTracks(videoEl);
    clearSubtitleOverlay();

    if (mode === "subs") {
        if (!video.available_subtitles || video.available_subtitles.length === 0) {
            infoEl.textContent = "Aucun sous-titre disponible.";
        } else {
            try {
                // Support multiple languages selection
                const langsToLoad = options.langs && options.langs.length > 0
                    ? options.langs
                    : (options.lang 
                        ? [options.lang] 
                        : video.available_subtitles);
                
                // Check if subtitles are cached for offline use
                const cache = "caches" in window ? await caches.open("video-cache") : null;
                
                const subtitles = await Promise.all(
                    langsToLoad.map(async (lang) => {
                        const subUrl = `/videos/${encodeURIComponent(video.id)}/subs/${encodeURIComponent(lang)}`;
                        
                        // Try cache first if available
                        let res;
                        if (cache) {
                            const cached = await cache.match(subUrl);
                            if (cached) {
                                res = cached;
                            } else {
                                res = await fetch(subUrl);
                                // Cache for future offline use
                                if (res.ok) {
                                    await cache.put(subUrl, res.clone());
                                    res = await cache.match(subUrl);
                                }
                            }
                        } else {
                            res = await fetch(subUrl);
                        }
                        
                        if (!res || !res.ok) {
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

                infoEl.textContent = `Lecture avec sous-titres (${langsToLoad
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
            videoEl.src = `/videos/${encodeURIComponent(video.id)}/dub/${encodeURIComponent(selectedLang)}`;
            
            // Load subtitles if requested
            if (options.langs && options.langs.length > 0) {
                try {
                    const cache = "caches" in window ? await caches.open("video-cache") : null;
                    
                    const subtitles = await Promise.all(
                        options.langs.map(async (lang) => {
                            const subUrl = `/videos/${encodeURIComponent(video.id)}/subs/${encodeURIComponent(lang)}`;
                            
                            let res;
                            if (cache) {
                                const cached = await cache.match(subUrl);
                                if (cached) {
                                    res = cached;
                                } else {
                                    res = await fetch(subUrl);
                                    if (res.ok) {
                                        await cache.put(subUrl, res.clone());
                                        res = await cache.match(subUrl);
                                    }
                                }
                            } else {
                                res = await fetch(subUrl);
                            }
                            
                            if (!res || !res.ok) {
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

                    infoEl.textContent = `Lecture avec doublage (${selectedLang.toUpperCase()}) et sous-titres (${options.langs.map((code) => code.toUpperCase()).join(", ")})`;
                } catch (err) {
                    console.error(err);
                    infoEl.textContent = `Lecture avec doublage (${selectedLang.toUpperCase()}) - Impossible de charger les sous-titres.`;
                }
            } else {
                infoEl.textContent = `Lecture avec doublage (${selectedLang.toUpperCase()})`;
            }
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
// Initialize file upload functionality when DOM is ready
function initializeFileUpload() {
    const fileInput = document.getElementById("video-file");
    const fileNameLabel = document.getElementById("selected-file-name");
    const fileUploadButton = document.getElementById("file-upload-button");

    if (!fileInput) {
        console.warn("File input not found");
        return;
    }
    
    // Ensure file input has correct accept attribute based on current file type selection
    const selectedFileType = document.querySelector('input[name="file_type"]:checked')?.value || "video";
    if (selectedFileType === "video") {
        fileInput.accept = "video/*";
    } else if (selectedFileType === "audio") {
        fileInput.accept = "audio/*";
    } else if (selectedFileType === "text") {
        fileInput.accept = ".txt";
    }

    // Setup file upload button click handler - DIRECT AND RELIABLE
    if (fileUploadButton) {
        // Remove any existing listeners by cloning
        const newButton = fileUploadButton.cloneNode(true);
        fileUploadButton.parentNode.replaceChild(newButton, fileUploadButton);
        
        // Use addEventListener for better compatibility
        newButton.addEventListener("click", function(e) {
            e.preventDefault();
            e.stopPropagation();
            console.log("File upload button clicked - attempting to open file picker");
            
            // Get fresh reference to file input every time
            const currentFileInput = document.getElementById("video-file");
            if (!currentFileInput) {
                console.error("File input element not found!");
                alert("Erreur: Le champ de fichier n'a pas été trouvé. Veuillez rafraîchir la page.");
                return;
            }
            
            // Ensure input is in the DOM and accessible
            if (!document.body.contains(currentFileInput)) {
                console.error("File input not in DOM!");
                return;
            }
            
            // Try to trigger file picker - simplest method first
            try {
                // Direct click - this should work in most browsers
                currentFileInput.click();
                console.log("File input clicked successfully");
            } catch (err) {
                console.error("Error clicking file input:", err);
                // Fallback: try creating a temporary input
                try {
                    const tempInput = document.createElement("input");
                    tempInput.type = "file";
                    tempInput.accept = currentFileInput.accept || "*/*";
                    tempInput.style.position = "fixed";
                    tempInput.style.left = "-9999px";
                    tempInput.onchange = function(evt) {
                        if (evt.target.files && evt.target.files[0]) {
                            // Transfer file to original input using DataTransfer
                            const dataTransfer = new DataTransfer();
                            dataTransfer.items.add(evt.target.files[0]);
                            currentFileInput.files = dataTransfer.files;
                            // Trigger change event on original input
                            const changeEvent = new Event('change', { bubbles: true });
                            currentFileInput.dispatchEvent(changeEvent);
                        }
                        document.body.removeChild(tempInput);
                    };
                    document.body.appendChild(tempInput);
                    tempInput.click();
                    console.log("Fallback file input created and clicked");
                } catch (err2) {
                    console.error("Fallback method also failed:", err2);
                    alert("Impossible d'ouvrir le sélecteur de fichiers. Veuillez rafraîchir la page.");
                }
            }
        }, { once: false, passive: false });
        
        console.log("File upload button handler attached successfully");
    } else {
        console.error("File upload button element not found!");
    }

    if (fileNameLabel) {
        const defaultLabel = fileNameLabel.textContent || "Aucun fichier sélectionné";
        const updateFileLabel = () => {
            if (fileInput.files && fileInput.files.length > 0) {
                if (fileInput.files.length === 1) {
                    fileNameLabel.textContent = fileInput.files[0].name;
                } else {
                    fileNameLabel.textContent = `${fileInput.files.length} fichiers sélectionnés`;
                }
            } else {
                fileNameLabel.textContent = defaultLabel;
            }
        };
        // Support for both change and input events (iOS compatibility)
        fileInput.addEventListener("change", updateFileLabel);
        fileInput.addEventListener("input", updateFileLabel);
    }
    
    // Also handle click on the label area for better iOS support
    const fileUploadLabel = document.getElementById("file-type-label");
    if (fileUploadLabel) {
        fileUploadLabel.style.cursor = "pointer";
        fileUploadLabel.addEventListener("click", (e) => {
            e.preventDefault();
            try {
                fileInput.click();
            } catch (err) {
                console.error("Error triggering file input from label:", err);
            }
        });
    }
}

// Initialize when DOM is ready - with retry mechanism
function tryInitializeFileUpload() {
    try {
        initializeFileUpload();
    } catch (err) {
        console.error("Error initializing file upload:", err);
        // Retry after a short delay
        setTimeout(tryInitializeFileUpload, 100);
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', tryInitializeFileUpload);
} else {
    // DOM is already loaded
    tryInitializeFileUpload();
}

// Also try after a delay as fallback
setTimeout(tryInitializeFileUpload, 500);

// Editor functions
async function renameVideo(videoId) {
    const newName = prompt("Entrez le nouveau nom de la vidéo:");
    if (!newName) return;
    
    try {
        const formData = new FormData();
        formData.append("new_filename", newName);
        
        const res = await fetch(`/api/videos/${encodeURIComponent(videoId)}/rename`, {
            method: "PUT",
            body: formData,
        });
        
        if (!res.ok) {
            const err = await res.json();
            alert("Erreur : " + (err.error || res.statusText));
            return;
        }
        
        alert("Vidéo renommée avec succès!");
        // Refresh the list to show the renamed file with its new ID
        await fetchVideos();
        // Scroll to top to ensure the refreshed list is visible
        window.scrollTo(0, 0);
    } catch (err) {
        console.error(err);
        alert("Erreur lors du renommage.");
    }
}

async function togglePrivacy(videoId, isPrivate) {
    try {
        const formData = new FormData();
        formData.append("is_private", isPrivate);
        
        const res = await fetch(`/api/videos/${encodeURIComponent(videoId)}/privacy`, {
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
        const res = await fetch(`/api/videos/${encodeURIComponent(videoId)}`, {
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
        const res = await fetch(`/api/videos/${encodeURIComponent(videoId)}/subs/${encodeURIComponent(lang)}/edit`);
        if (!res.ok) {
            const err = await res.json();
            alert("Erreur : " + (err.error || res.statusText));
            return;
        }
        
        const data = await res.json();
        
        // Create dialog with textarea for better editing experience
        const dialog = document.createElement("div");
        dialog.style.position = "fixed";
        dialog.style.top = "50%";
        dialog.style.left = "50%";
        dialog.style.transform = "translate(-50%, -50%)";
        dialog.style.backgroundColor = "white";
        dialog.style.padding = "20px";
        dialog.style.border = "2px solid #ccc";
        dialog.style.borderRadius = "5px";
        dialog.style.zIndex = "10000";
        dialog.style.boxShadow = "0 4px 6px rgba(0,0,0,0.1)";
        dialog.style.minWidth = "600px";
        dialog.style.maxWidth = "90vw";
        dialog.style.maxHeight = "80vh";
        dialog.style.display = "flex";
        dialog.style.flexDirection = "column";
        
        const title = document.createElement("h3");
        title.textContent = `Modifier les sous-titres (${lang.toUpperCase()})`;
        title.style.marginTop = "0";
        dialog.appendChild(title);
        
        const textarea = document.createElement("textarea");
        textarea.value = data.content || "";
        textarea.style.width = "100%";
        textarea.style.height = "400px";
        textarea.style.fontFamily = "monospace";
        textarea.style.fontSize = "12px";
        textarea.style.padding = "10px";
        textarea.style.border = "1px solid #ccc";
        textarea.style.borderRadius = "3px";
        textarea.style.resize = "vertical";
        textarea.style.flex = "1";
        textarea.style.minHeight = "300px";
        dialog.appendChild(textarea);
        
        const buttons = document.createElement("div");
        buttons.style.display = "flex";
        buttons.style.gap = "10px";
        buttons.style.justifyContent = "flex-end";
        buttons.style.marginTop = "15px";
        
        const cancelBtn = document.createElement("button");
        cancelBtn.textContent = "Annuler";
        cancelBtn.onclick = () => {
            document.body.removeChild(dialog);
        };
        buttons.appendChild(cancelBtn);
        
        const saveBtn = document.createElement("button");
        saveBtn.textContent = "Enregistrer";
        saveBtn.style.backgroundColor = "#007bff";
        saveBtn.style.color = "white";
        saveBtn.onclick = async () => {
            const content = textarea.value;
            document.body.removeChild(dialog);
            
            const formData = new FormData();
            formData.append("content", content);
            
            try {
                const saveRes = await fetch(`/api/videos/${encodeURIComponent(videoId)}/subs/${encodeURIComponent(lang)}/edit`, {
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
                alert("Erreur lors de l'enregistrement des sous-titres.");
            }
        };
        buttons.appendChild(saveBtn);
        
        dialog.appendChild(buttons);
        document.body.appendChild(dialog);
        
        // Focus textarea
        textarea.focus();
        textarea.setSelectionRange(0, 0);
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

async function uploadFileToFolder(folderPath) {
    const input = document.createElement("input");
    input.type = "file";
    // Accept all file types: video, audio, and text
    input.accept = "video/*,audio/*,.txt";
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
            
            alert("Fichier téléchargé avec succès!");
            fetchVideos();
        } catch (err) {
            console.error(err);
            alert("Erreur lors du téléchargement du fichier.");
        }
    };
    input.click();
}

async function changeFolderColor(folderPath, currentColor) {
    const colorInput = document.createElement("input");
    colorInput.type = "color";
    colorInput.value = currentColor;
    
    const colorDialog = document.createElement("div");
    colorDialog.style.position = "fixed";
    colorDialog.style.top = "50%";
    colorDialog.style.left = "50%";
    colorDialog.style.transform = "translate(-50%, -50%)";
    colorDialog.style.backgroundColor = "white";
    colorDialog.style.padding = "20px";
    colorDialog.style.border = "2px solid #ccc";
    colorDialog.style.borderRadius = "5px";
    colorDialog.style.zIndex = "10000";
    colorDialog.style.boxShadow = "0 4px 6px rgba(0,0,0,0.1)";
    
    const colorLabel = document.createElement("label");
    colorLabel.textContent = "Choisissez une nouvelle couleur:";
    colorLabel.style.display = "block";
    colorLabel.style.marginBottom = "10px";
    colorDialog.appendChild(colorLabel);
    colorDialog.appendChild(colorInput);
    
    const colorButtons = document.createElement("div");
    colorButtons.style.display = "flex";
    colorButtons.style.gap = "10px";
    colorButtons.style.marginTop = "15px";
    
    const colorOkBtn = document.createElement("button");
    colorOkBtn.textContent = "OK";
    colorOkBtn.onclick = async () => {
        const selectedColor = colorInput.value;
        document.body.removeChild(colorDialog);
        
        try {
            const formData = new FormData();
            formData.append("color", selectedColor);
            
            const res = await fetch(`/api/folders/${encodeURIComponent(folderPath)}/color`, {
                method: "PUT",
                body: formData,
            });
            
            if (!res.ok) {
                const err = await res.json();
                alert("Erreur : " + (err.error || res.statusText));
                return;
            }
            
            alert("Couleur mise à jour avec succès!");
            fetchVideos();
        } catch (err) {
            console.error(err);
            alert("Erreur lors de la mise à jour de la couleur.");
        }
    };
    
    const colorCancelBtn = document.createElement("button");
    colorCancelBtn.textContent = "Annuler";
    colorCancelBtn.onclick = () => {
        document.body.removeChild(colorDialog);
    };
    
    colorButtons.appendChild(colorOkBtn);
    colorButtons.appendChild(colorCancelBtn);
    colorDialog.appendChild(colorButtons);
    document.body.appendChild(colorDialog);
}

// Upload form handler (only for editors)
// Dynamic form handling based on file type
const fileTypeRadios = document.querySelectorAll('input[name="file_type"]');
const fileInputForType = document.getElementById("video-file");
const fileTypeLabel = document.getElementById("file-type-label");
const videoOptions = document.getElementById("video-options");
const audioOptions = document.getElementById("audio-options");
const textOptions = document.getElementById("text-options");
const languagesFieldset = document.getElementById("languages-fieldset");
const sourceLanguageFieldset = document.getElementById("source-language-fieldset");
const ttsSpeedFieldset = document.getElementById("tts-speed-fieldset");

if (fileTypeRadios.length > 0) {
    fileTypeRadios.forEach(radio => {
        radio.addEventListener("change", (e) => {
            const fileType = e.target.value;
            
            // Update file input accept attribute and label
            if (fileInputForType) {
                if (fileType === "video") {
                    fileInputForType.accept = "video/*";
                    if (fileTypeLabel) fileTypeLabel.textContent = "Fichier vidéo :";
                    videoOptions.style.display = "block";
                    audioOptions.style.display = "none";
                    textOptions.style.display = "none";
                    languagesFieldset.style.display = "block";
                    sourceLanguageFieldset.style.display = "none";
                    ttsSpeedFieldset.style.display = "block";
                } else if (fileType === "audio") {
                    fileInputForType.accept = "audio/*";
                    if (fileTypeLabel) fileTypeLabel.textContent = "Fichier audio :";
                    videoOptions.style.display = "none";
                    audioOptions.style.display = "block";
                    textOptions.style.display = "none";
                    languagesFieldset.style.display = "block";
                    sourceLanguageFieldset.style.display = "block";
                    ttsSpeedFieldset.style.display = "none";
                } else if (fileType === "text") {
                    fileInputForType.accept = ".txt";
                    if (fileTypeLabel) fileTypeLabel.textContent = "Fichier texte :";
                    videoOptions.style.display = "none";
                    audioOptions.style.display = "none";
                    textOptions.style.display = "block";
                    languagesFieldset.style.display = "block";
                    sourceLanguageFieldset.style.display = "block";
                    ttsSpeedFieldset.style.display = "none";
                }
            }
        });
    });
}

const uploadForm = document.getElementById("upload-form");
if (uploadForm && isEditor) {
    uploadForm.addEventListener("submit", async (e) => {
    console.log("=== FORM SUBMIT TRIGGERED ===");
    e.preventDefault();
    e.stopPropagation(); // Prevent iOS Safari from handling form differently
    
    try {
        const form = e.target;
        console.log("Form element:", form);
        
        const statusEl = document.getElementById("upload-status");
        if (!statusEl) {
            console.error("Upload status element not found");
            return;
        }
        
        // Check if file is selected
        const fileInputForUpload = document.getElementById("video-file");
        console.log("File input:", fileInputForUpload);
        console.log("File input files:", fileInputForUpload?.files);
        console.log("File input files length:", fileInputForUpload?.files?.length);
        
        if (!fileInputForUpload || !fileInputForUpload.files || fileInputForUpload.files.length === 0) {
            console.error("No file selected!");
            alert("Veuillez sélectionner un fichier.");
            return;
        }
        
        // Create progress bar instead of simple text
        statusEl.innerHTML = "";
        const progressObj = createProgressBar(statusEl);
        const startTime = Date.now();
        
        // Update progress periodically
        const progressInterval = setInterval(() => {
            const elapsedSeconds = (Date.now() - startTime) / 1000;
            // Estimate progress: start at 5%, gradually increase
            // This is a rough estimate - actual progress will be updated during upload/processing
            const estimatedProgress = Math.min(95, 5 + (elapsedSeconds / 60) * 10); // Rough estimate
            updateProgress(progressObj, estimatedProgress, elapsedSeconds);
        }, 1000); // Update every second
        
        // Store progress object and interval for later cleanup
        statusEl.dataset.progressInterval = progressInterval;
        statusEl.dataset.progressStartTime = startTime;

        // Create new FormData instead of using form directly to avoid conflicts
        const formData = new FormData();
        const fileType = form.querySelector('input[name="file_type"]:checked')?.value || "video";
        const checkedLangs = [...form.querySelectorAll("input[name='languages']:checked")];
        const checkedOptions = [...form.querySelectorAll("input[name='process_options']:checked")];
        
        // Debug logging BEFORE any checks
        console.log("=== UPLOAD DEBUG START ===");
    console.log("File type:", fileType);
    console.log("Checked languages (raw):", checkedLangs);
    console.log("Checked languages (values):", checkedLangs.map(l => l.value));
    console.log("Checked languages count:", checkedLangs.length);
    console.log("Checked options:", checkedOptions.map(o => o.value));
    
    // Note: We'll upload files one by one in a loop below
    // Don't add file here yet
    
    // Add file type
    formData.append("file_type", fileType);
    
    // For videos, languages are required only if subs, dub_audio, or dub_video are selected
    if (fileType === "video") {
        console.log("Processing video upload...");
        const needsLanguages = checkedOptions.some(opt => 
            opt.value === "subs" || opt.value === "dub_audio" || opt.value === "dub_video"
        );
        
        if (needsLanguages) {
            if (checkedLangs.length === 0 || checkedLangs.length > 2) {
                console.error("Language validation failed:", {
                    count: checkedLangs.length,
                    languages: checkedLangs.map(l => l.value)
                });
                alert("Veuillez sélectionner une ou deux langues cibles.");
                return;
            }
            // Explicitly add languages to FormData for videos
            console.log("Adding languages to FormData:", checkedLangs.map(l => l.value));
            checkedLangs.forEach(lang => {
                formData.append("languages", lang.value);
                console.log("  ✓ Added language:", lang.value);
            });
        } else {
            console.log("No languages needed (only transcribe selected)");
        }
        
        // Debug: log all FormData entries
        console.log("FormData contents:");
        for (const [key, value] of formData.entries()) {
            if (key === "file") {
                console.log(`  ${key}: [File object]`);
            } else {
                console.log(`  ${key}: ${value}`);
            }
        }
        console.log("=== UPLOAD DEBUG END ===");
    }
    
    // For audio and text files, we need source language
    if (fileType === "audio" || fileType === "text") {
        const sourceLang = form.querySelector('select[name="source_language"]')?.value;
        if (sourceLang) {
            formData.append("source_language", sourceLang);
        }
        
        // Languages are required for translation/generation
        if (checkedOptions.some(opt => opt.value === "translate" || opt.value === "generate_audio")) {
            if (checkedLangs.length === 0 || checkedLangs.length > 2) {
                alert("Veuillez sélectionner une ou deux langues cibles.");
                return;
            }
            // Explicitly add languages to FormData
            checkedLangs.forEach(lang => {
                formData.append("languages", lang.value);
            });
        }
    }

    if (checkedOptions.length === 0) {
        alert("Veuillez sélectionner au moins une option de traitement.");
        return;
    }

    // Add process options
    checkedOptions.forEach(opt => {
        formData.append("process_options", opt.value);
    });
    
    // Add TTS speed multiplier
    const ttsSpeed = form.querySelector('input[name="tts_speed_multiplier"]')?.value || "1.0";
    formData.append("tts_speed_multiplier", ttsSpeed);

    // Add folder path - use folder privacy if folder is selected
    const folderPath = document.getElementById("folder-path-select")?.value || "";
    let isPrivate = false;
    if (folderPath) {
        formData.append("folder_path", folderPath);
        // Get folder privacy from folder list
        try {
            const foldersRes = await fetch("/api/folders");
            if (foldersRes.ok) {
                const folders = await foldersRes.json();
                const folder = folders.find(f => f.path === folderPath);
                if (folder && folder.is_private) {
                    isPrivate = true;
                }
            }
        } catch (err) {
            console.warn("Could not fetch folder privacy:", err);
        }
    }
    formData.append("is_private", isPrivate);

    // Get submit button reference once to avoid redeclaration
    const submitBtn = form.querySelector('button[type="submit"]');

    try {
        // Disable submit button to prevent double submission (iOS issue)
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.textContent = "Téléversement en cours...";
        }
        
        // Upload all selected files sequentially
        const files = Array.from(fileInputForUpload.files);
        const totalFiles = files.length;
        let successCount = 0;
        let errorCount = 0;
        
        // Get or create progress bar
        let progressObj = null;
        let progressInterval = null;
        const startTime = statusEl.dataset.progressStartTime ? parseInt(statusEl.dataset.progressStartTime) : Date.now();
        
        if (statusEl.querySelector(".progress-container")) {
            // Reuse existing progress bar
            const container = statusEl.querySelector(".progress-container");
            progressObj = {
                container: container,
                text: container.querySelector(".progress-text"),
                bar: container.querySelector(".progress-bar"),
                timeRemaining: container.querySelector(".time-remaining")
            };
            // Clear existing interval
            if (statusEl.dataset.progressInterval) {
                clearInterval(parseInt(statusEl.dataset.progressInterval));
            }
        } else {
            statusEl.innerHTML = "";
            progressObj = createProgressBar(statusEl);
        }
        
        // Update progress periodically
        progressInterval = setInterval(() => {
            const elapsedSeconds = (Date.now() - startTime) / 1000;
            // Get current progress from dataset or estimate
            const currentProgress = parseFloat(statusEl.dataset.currentProgress || "5");
            updateProgress(progressObj, currentProgress, elapsedSeconds);
        }, 1000);
        statusEl.dataset.progressInterval = progressInterval;
        statusEl.dataset.progressStartTime = startTime;
        
        for (let i = 0; i < files.length; i++) {
            const file = files[i];
            const fileFormData = new FormData();
            
            // Add the current file
            fileFormData.append("file", file);
            
            // Add all other form fields (same for all files)
            fileFormData.append("file_type", fileType);
            checkedLangs.forEach(lang => {
                fileFormData.append("languages", lang.value);
            });
            checkedOptions.forEach(opt => {
                fileFormData.append("process_options", opt.value);
            });
            
            const ttsSpeed = form.querySelector('input[name="tts_speed_multiplier"]')?.value || "1.0";
            fileFormData.append("tts_speed_multiplier", ttsSpeed);
            
            if (folderPath) {
                fileFormData.append("folder_path", folderPath);
            }
            fileFormData.append("is_private", isPrivate);
            
            if (fileType === "audio" || fileType === "text") {
                const sourceLang = form.querySelector('select[name="source_language"]')?.value;
                if (sourceLang) {
                    fileFormData.append("source_language", sourceLang);
                }
            }
            
            // Update progress: Upload phase (0-20% for videos, 0-30% for audio/text)
            const uploadProgress = fileType === "video" ? 20 : 30;
            // Progress per file: each file gets a portion of the upload progress
            const fileProgress = ((i / totalFiles) * uploadProgress) + ((i + 1) / totalFiles) * uploadProgress * 0.8;
            statusEl.dataset.currentProgress = fileProgress.toString();
            const elapsedSeconds = (Date.now() - startTime) / 1000;
            updateProgress(progressObj, fileProgress, elapsedSeconds);
            
            try {
                const res = await fetch("/api/upload", {
                    method: "POST",
                    body: fileFormData,
                    credentials: "include", // Include cookies in request
                    // Don't set Content-Type header - let browser set it with boundary for multipart/form-data
                });

                if (!res.ok) {
                    let errorMessage = "Erreur inconnue";
                    try {
                        const err = await res.json();
                        errorMessage = err.error || res.statusText || `Status ${res.status}`;
                        console.error(`Upload error for file ${file.name}:`, err);
                    } catch (e) {
                        // If response is not JSON, try to get text
                        try {
                            const text = await res.text();
                            errorMessage = text || `Status ${res.status}: ${res.statusText}`;
                            console.error(`Upload error (text) for file ${file.name}:`, text);
                        } catch (e2) {
                            errorMessage = `Status ${res.status}: ${res.statusText}`;
                            console.error(`Upload error (status only) for file ${file.name}:`, res.status, res.statusText);
                        }
                    }
                    errorCount++;
                    const errorMsg = document.createElement("div");
                    errorMsg.className = "status-error";
                    errorMsg.textContent = `Erreur pour ${file.name}: ${errorMessage}`;
                    statusEl.appendChild(errorMsg);
                    continue; // Continue with next file
                }

                const data = await res.json();
                successCount++;
                
                // Update progress: Upload complete, now processing
                const uploadCompleteProgress = fileType === "video" ? 20 : 30;
                statusEl.dataset.currentProgress = uploadCompleteProgress.toString();
                const elapsedSeconds = (Date.now() - startTime) / 1000;
                updateProgress(progressObj, uploadCompleteProgress, elapsedSeconds);
                
                // Check if this is an audio/text file (no job polling needed)
                if (fileType === "audio" || fileType === "text") {
                    // Simulate processing progress for audio/text (30% to 100%)
                    let processingProgress = uploadCompleteProgress;
                    const processingInterval = setInterval(() => {
                        processingProgress = Math.min(100, processingProgress + 2);
                        statusEl.dataset.currentProgress = processingProgress.toString();
                        const elapsedSeconds = (Date.now() - startTime) / 1000;
                        updateProgress(progressObj, processingProgress, elapsedSeconds);
                        
                        if (processingProgress >= 100) {
                            clearInterval(processingInterval);
                            // Clear progress interval
                            if (statusEl.dataset.progressInterval) {
                                clearInterval(parseInt(statusEl.dataset.progressInterval));
                            }
                        }
                    }, 500);
                    
                    // Wait a bit to show progress, then show results
                    await sleep(1000);
                    clearInterval(processingInterval);
                    if (statusEl.dataset.progressInterval) {
                        clearInterval(parseInt(statusEl.dataset.progressInterval));
                    }
                    
                    // Show 100% complete
                    updateProgress(progressObj, 100, (Date.now() - startTime) / 1000);
                    
                    const successMsg = document.createElement("div");
                    successMsg.className = "status-success";
                    successMsg.style.marginTop = "15px";
                    successMsg.textContent = `${file.name}: ${data.message || "Fichier traité avec succès!"}`;
                    statusEl.appendChild(successMsg);
                    
                    // Show results if available
                    if (data.results) {
                        const resultsDiv = document.createElement("div");
                        resultsDiv.style.marginTop = "10px";
                        resultsDiv.innerHTML = `<strong>Fichiers générés pour ${file.name}:</strong><ul>`;
                        for (const [key, path] of Object.entries(data.results)) {
                            const li = document.createElement("li");
                            const link = document.createElement("a");
                            // Extract filename from path
                            const filename = path.split(/[/\\]/).pop();
                            link.href = `/files/${data.id}/${filename}`;
                            link.textContent = key + " (" + filename + ")";
                            link.target = "_blank";
                            li.appendChild(link);
                            resultsDiv.querySelector("ul").appendChild(li);
                        }
                        resultsDiv.innerHTML += "</ul>";
                        statusEl.appendChild(resultsDiv);
                    }
                } else {
                    // Video processing with job polling - pass progress object
                    await pollJobStatus(data.id, statusEl, progressObj, startTime);
                }
            } catch (err) {
                errorCount++;
                console.error(`=== FORM SUBMIT ERROR for file ${file.name} ===`, err);
                console.error("Error stack:", err.stack);
                const errorMsg = document.createElement("div");
                errorMsg.className = "status-error";
                errorMsg.textContent = `Erreur pour ${file.name}: ${err.message}`;
                statusEl.appendChild(errorMsg);
            }
        }
        
        // Clear progress interval
        if (statusEl.dataset.progressInterval) {
            clearInterval(parseInt(statusEl.dataset.progressInterval));
        }
        
        // Final summary
        if (totalFiles > 1) {
            const summaryMsg = document.createElement("div");
            summaryMsg.style.marginTop = "10px";
            summaryMsg.style.fontWeight = "bold";
            if (successCount === totalFiles) {
                summaryMsg.className = "status-success";
                summaryMsg.textContent = `✓ Tous les fichiers (${successCount}) ont été téléversés avec succès!`;
            } else {
                summaryMsg.className = "status-error";
                summaryMsg.textContent = `⚠ ${successCount} fichier(s) réussi(s), ${errorCount} erreur(s).`;
            }
            statusEl.appendChild(summaryMsg);
        }
        
        // Refresh video list after all uploads
        fetchVideos();
        
        // Re-enable submit button after all uploads complete
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = "Téléverser et traiter";
        }
    } catch (err) {
        console.error("=== INNER FORM SUBMIT ERROR ===", err);
        console.error("Error stack:", err.stack);
        const statusEl = document.getElementById("upload-status");
        if (statusEl) {
            statusEl.textContent = "Erreur inconnue: " + err.message;
        }
        alert("Erreur: " + err.message);
        // Re-enable submit button on error
        const submitBtn = document.querySelector('#upload-form button[type="submit"]');
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = "Téléverser et traiter";
        }
    }
    } catch (outerErr) {
        console.error("=== OUTER FORM SUBMIT ERROR ===", outerErr);
        console.error("Error stack:", outerErr.stack);
        const statusEl = document.getElementById("upload-status");
        if (statusEl) {
            // Clear progress interval
            if (statusEl.dataset.progressInterval) {
                clearInterval(parseInt(statusEl.dataset.progressInterval));
            }
            statusEl.innerHTML = "";
            statusEl.textContent = "Erreur inconnue: " + outerErr.message;
        }
        alert("Erreur: " + outerErr.message);
        const submitBtn = document.querySelector('#upload-form button[type="submit"]');
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = "Téléverser et traiter";
        }
    }
    });
}

// Folder management (editors only) - initialize when ready
function initializeFolderManagement() {
    if (!isEditor) {
        console.log("User is not an editor, skipping folder management initialization");
        return;
    }
    
    const createFolderBtn = document.getElementById("create-folder-btn");
    if (createFolderBtn) {
        console.log("Found create folder button, attaching handler");
        // Remove any existing listeners to prevent duplicates
        const newBtn = createFolderBtn.cloneNode(true);
        createFolderBtn.parentNode.replaceChild(newBtn, createFolderBtn);
        
        // Use onclick for better compatibility
        newBtn.onclick = async function(e) {
            console.log("Create folder button clicked");
            e.preventDefault();
            e.stopPropagation();
            // Get existing folders for path selection
            let existingFolders = [];
            try {
                const foldersRes = await fetch("/api/folders");
                if (foldersRes.ok) {
                    existingFolders = await foldersRes.json();
                }
            } catch (err) {
                console.error("Failed to load folders", err);
            }
            
            // Create dialog for folder creation
            const dialog = document.createElement("div");
            dialog.style.position = "fixed";
            dialog.style.top = "50%";
            dialog.style.left = "50%";
            dialog.style.transform = "translate(-50%, -50%)";
            dialog.style.backgroundColor = "white";
            dialog.style.padding = "20px";
            dialog.style.border = "2px solid #ccc";
            dialog.style.borderRadius = "5px";
            dialog.style.zIndex = "10000";
            dialog.style.boxShadow = "0 4px 6px rgba(0,0,0,0.1)";
            dialog.style.minWidth = "400px";
            dialog.style.maxHeight = "80vh";
            dialog.style.overflow = "auto";
            
            const title = document.createElement("h3");
            title.textContent = "Créer un nouveau dossier";
            title.style.marginTop = "0";
            dialog.appendChild(title);
            
            // Folder path selection
            const pathLabel = document.createElement("label");
            pathLabel.textContent = "Chemin du dossier:";
            pathLabel.style.display = "block";
            pathLabel.style.marginBottom = "5px";
            dialog.appendChild(pathLabel);
            
            const pathInput = document.createElement("input");
            pathInput.type = "text";
            pathInput.placeholder = "ex: projets/2024";
            pathInput.style.width = "100%";
            pathInput.style.padding = "5px";
            pathInput.style.marginBottom = "10px";
            dialog.appendChild(pathInput);
            
            // Existing folders browser
            if (existingFolders.length > 0) {
                const foldersLabel = document.createElement("label");
                foldersLabel.textContent = "Ou cliquer sur un dossier existant pour créer un sous-dossier:";
                foldersLabel.style.display = "block";
                foldersLabel.style.marginTop = "10px";
                foldersLabel.style.marginBottom = "5px";
                dialog.appendChild(foldersLabel);
                
                const foldersList = document.createElement("div");
                foldersList.style.maxHeight = "150px";
                foldersList.style.overflowY = "auto";
                foldersList.style.border = "1px solid #ccc";
                foldersList.style.padding = "5px";
                foldersList.style.marginBottom = "10px";
                
                // Sort folders by path for better organization
                const sortedFolders = [...existingFolders].sort((a, b) => a.path.localeCompare(b.path));
                
                sortedFolders.forEach(folder => {
                    const folderItem = document.createElement("div");
                    folderItem.style.padding = "5px";
                    folderItem.style.cursor = "pointer";
                    folderItem.style.borderBottom = "1px solid #eee";
                    folderItem.textContent = "📁 " + folder.path;
                    if (folder.is_private) {
                        folderItem.textContent += " [PRIVÉ]";
                    }
                    folderItem.onmouseover = () => {
                        folderItem.style.backgroundColor = "#f0f0f0";
                    };
                    folderItem.onmouseout = () => {
                        folderItem.style.backgroundColor = "transparent";
                    };
                    folderItem.onclick = () => {
                        // Set the path to the selected folder (user can add subfolder name after)
                        pathInput.value = folder.path + "/";
                        pathInput.focus();
                    };
                    foldersList.appendChild(folderItem);
                });
                
                dialog.appendChild(foldersList);
            }
            
            // Privacy checkbox
            const privacyLabel = document.createElement("label");
            privacyLabel.style.display = "flex";
            privacyLabel.style.alignItems = "center";
            privacyLabel.style.marginBottom = "10px";
            const privacyCheckbox = document.createElement("input");
            privacyCheckbox.type = "checkbox";
            privacyCheckbox.id = "new-folder-private";
            privacyLabel.appendChild(privacyCheckbox);
            const privacyText = document.createElement("span");
            privacyText.textContent = " Rendre ce dossier privé";
            privacyText.style.marginLeft = "5px";
            privacyLabel.appendChild(privacyText);
            dialog.appendChild(privacyLabel);
            
            // Color picker
            const colorLabel = document.createElement("label");
            colorLabel.textContent = "Couleur du dossier:";
            colorLabel.style.display = "block";
            colorLabel.style.marginBottom = "5px";
            dialog.appendChild(colorLabel);
            
            const colorInput = document.createElement("input");
            colorInput.type = "color";
            colorInput.value = "#f0f0f0";
            colorInput.style.width = "100%";
            colorInput.style.height = "40px";
            colorInput.style.marginBottom = "15px";
            dialog.appendChild(colorInput);
            
            // Buttons
            const buttons = document.createElement("div");
            buttons.style.display = "flex";
            buttons.style.gap = "10px";
            buttons.style.justifyContent = "flex-end";
            
            const cancelBtn = document.createElement("button");
            cancelBtn.textContent = "Annuler";
            cancelBtn.onclick = () => {
                document.body.removeChild(dialog);
            };
            buttons.appendChild(cancelBtn);
            
            const createBtn = document.createElement("button");
            createBtn.textContent = "Créer";
            createBtn.style.backgroundColor = "#007bff";
            createBtn.style.color = "white";
            createBtn.onclick = async () => {
                const folderPath = pathInput.value.trim();
                if (!folderPath) {
                    alert("Veuillez entrer un chemin de dossier.");
                    return;
                }
                
                const isPrivate = privacyCheckbox.checked;
                const selectedColor = colorInput.value;
                
                document.body.removeChild(dialog);
                
                try {
                    const formData = new FormData();
                    formData.append("folder_path", folderPath);
                    formData.append("is_private", isPrivate);
                    formData.append("color", selectedColor);
                    
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
                    populateFolderDropdown(); // Refresh folder dropdown
                } catch (err) {
                    console.error(err);
                    alert("Erreur lors de la création du dossier.");
                }
            };
            buttons.appendChild(createBtn);
            
            dialog.appendChild(buttons);
            document.body.appendChild(dialog);
        };
        
        console.log("Create folder button handler attached successfully");
    } else {
        console.error("Create folder button element not found!");
    }
}

// Initialize folder management when DOM is ready
function tryInitializeFolderManagement() {
    try {
        initializeFolderManagement();
    } catch (err) {
        console.error("Error initializing folder management:", err);
        setTimeout(tryInitializeFolderManagement, 100);
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', tryInitializeFolderManagement);
} else {
    tryInitializeFolderManagement();
}
// Also try after a delay as fallback
setTimeout(tryInitializeFolderManagement, 500);

// Populate folder dropdown in upload form
async function populateFolderDropdown() {
    const folderSelect = document.getElementById("folder-path-select");
    if (!folderSelect || !isEditor) {
        return;
    }
    
    // Don't update if dropdown is currently open (user is selecting)
    if (folderSelect === document.activeElement) {
        return;
    }
    
    // Save current selection
    const currentValue = folderSelect.value;
    
    try {
        const foldersRes = await fetch("/api/folders");
        if (!foldersRes.ok) {
            return;
        }
        const folders = await foldersRes.json();
        
        // Clear existing options except the first one
        while (folderSelect.options.length > 1) {
            folderSelect.remove(1);
        }
        
        // Sort folders by path
        const sortedFolders = [...folders].sort((a, b) => a.path.localeCompare(b.path));
        
        // Add folders to dropdown
        sortedFolders.forEach(folder => {
            const option = document.createElement("option");
            option.value = folder.path;
            option.textContent = folder.path + (folder.is_private ? " [PRIVÉ]" : "");
            folderSelect.appendChild(option);
        });
        
        // Restore previous selection if it still exists
        if (currentValue && Array.from(folderSelect.options).some(opt => opt.value === currentValue)) {
            folderSelect.value = currentValue;
        }
    } catch (err) {
        console.error("Failed to load folders for dropdown", err);
    }
}

window.addEventListener("load", () => {
    fetchVideos();
    populateFolderDropdown();
    // Refresh folder dropdown when videos are fetched (in case folders changed)
    setInterval(populateFolderDropdown, 5000);
});

