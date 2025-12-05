// Check if user is editor
const isEditor = document.getElementById("role-indicator")?.textContent.includes("I-tech privé") || false;
// Check if user is admin
const isAdmin = document.getElementById("role-indicator")?.textContent.includes("admin") || false;

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

        // Create a flex container for thumbnail and title
        const headerContainer = document.createElement("div");
        headerContainer.style.display = "flex";
        headerContainer.style.alignItems = "flex-start";
        headerContainer.style.gap = "15px";
        headerContainer.style.marginBottom = "10px";
        
        // Thumbnail image (if available) - left side
        if (isVideo) {
            const thumbnailContainer = document.createElement("div");
            thumbnailContainer.style.flexShrink = "0";
            thumbnailContainer.style.display = "block"; // Make sure it's visible by default
            thumbnailContainer.style.minWidth = "150px"; // Ensure container has minimum width
            thumbnailContainer.style.minHeight = "100px"; // Ensure container has minimum height
            
            const thumbnailImg = document.createElement("img");
            thumbnailImg.src = `/videos/${video.id}/thumbnail?t=${Date.now()}`; // Add cache busting
            thumbnailImg.alt = "Thumbnail";
            thumbnailImg.style.width = "150px";
            thumbnailImg.style.height = "100px";
            thumbnailImg.style.objectFit = "cover";
            thumbnailImg.style.border = "1px solid #ddd";
            thumbnailImg.style.borderRadius = "5px";
            thumbnailImg.style.cursor = "pointer";
            thumbnailImg.style.display = "block"; // Make sure it's visible by default
            thumbnailImg.style.backgroundColor = "#f0f0f0"; // Background color while loading
            
            // Use a more robust error handler
            thumbnailImg.onerror = function(e) {
                // Hide thumbnail if it doesn't exist (404 or other error)
                console.error("Thumbnail not found or failed to load for video:", video.id, "Error:", e);
                console.error("Thumbnail URL was:", this.src);
                this.style.display = "none";
                thumbnailContainer.style.display = "none";
            };
            
            thumbnailImg.onload = function() {
                // Check if image is actually a valid thumbnail (not the transparent placeholder)
                // The transparent placeholder is 1x1 pixel, so if the natural width/height is 1, hide it
                if (this.naturalWidth === 1 && this.naturalHeight === 1) {
                    console.log("Thumbnail is transparent placeholder, hiding for video:", video.id);
                    this.style.display = "none";
                    thumbnailContainer.style.display = "none";
                } else {
                    console.log("Thumbnail loaded successfully for video:", video.id, "size:", this.naturalWidth, "x", this.naturalHeight);
                    // Make sure it's visible when loaded successfully
                    this.style.display = "block";
                    thumbnailContainer.style.display = "block";
                    this.style.backgroundColor = "transparent"; // Remove background when loaded
                }
            };
            
            thumbnailImg.onclick = () => {
                // Click to play video
                playVideo(video, {});
            };
            
            thumbnailContainer.appendChild(thumbnailImg);
            headerContainer.appendChild(thumbnailContainer);
        }
        
        // Title - right side of thumbnail
        const titleContainer = document.createElement("div");
        titleContainer.style.flex = "1";
        const title = document.createElement("h3");
        title.textContent = video.filename;
        if (video.is_private) {
            const privateBadge = document.createElement("span");
            privateBadge.textContent = " [PRIVÉ]";
            privateBadge.style.color = "#ffc107";
            title.appendChild(privateBadge);
        }
        titleContainer.appendChild(title);
        headerContainer.appendChild(titleContainer);
        div.appendChild(headerContainer);
        
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

        // Download transcription if available
        if (video.has_transcription) {
            const downloadTranscriptionBtn = document.createElement("button");
            downloadTranscriptionBtn.textContent = "📄 Télécharger la transcription (TXT)";
            downloadTranscriptionBtn.style.marginBottom = "5px";
            downloadTranscriptionBtn.style.padding = "8px";
            downloadTranscriptionBtn.style.backgroundColor = "#28a745";
            downloadTranscriptionBtn.style.color = "white";
            downloadTranscriptionBtn.style.border = "none";
            downloadTranscriptionBtn.style.borderRadius = "3px";
            downloadTranscriptionBtn.style.cursor = "pointer";
            downloadTranscriptionBtn.onclick = () => {
                // Cross-platform download: works on Windows, Linux, macOS, and iOS
                const url = `/files/${encodeURIComponent(video.id)}/transcribed.txt`;
                const link = document.createElement("a");
                link.href = url;
                link.download = `${baseName}_transcribed.txt`;
                // For iOS Safari compatibility: append to body, click, then remove
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                // Fallback: if download attribute doesn't work (iOS), open in new tab
                // The browser will handle it appropriately
            };
            downloads.appendChild(downloadTranscriptionBtn);
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

// New library functions with tabs
let allLibraryItems = [];

async function fetchVideos() {
    let items = [];
    
    try {
        const videosRes = await fetch("/api/videos");
        if (!videosRes.ok) {
            throw new Error(`Request failed with status ${videosRes.status}`);
        }
        items = await videosRes.json();
        allLibraryItems = items;
        
        // Always fetch folders (for both editors and viewers, but viewers only see public ones)
        const foldersRes = await fetch("/api/folders");
        if (foldersRes.ok) {
            const folders = await foldersRes.json();
            // Store folders for later use if needed
            window.libraryFolders = folders;
        }
    } catch (error) {
        console.error("Failed to load library items", error);
        showLibraryError();
        return;
    }
    
    // Filter items by type and render
    const videos = items.filter(item => (item.file_type || "video") === "video");
    const audios = items.filter(item => item.file_type === "audio");
    const texts = items.filter(item => item.file_type === "text");
    
    renderVideosGrid(videos);
    renderAudiosList(audios);
    renderTextsList(texts);
    
    // Refresh folder dropdown after fetching items
    if (typeof populateFolderDropdown === 'function') {
        populateFolderDropdown();
    }
}

function showLibraryError() {
    document.getElementById("videos-empty").textContent = "Impossible de charger la bibliothèque pour le moment.";
    document.getElementById("videos-empty").style.display = "block";
    document.getElementById("audios-empty").textContent = "Impossible de charger la bibliothèque pour le moment.";
    document.getElementById("texts-empty").textContent = "Impossible de charger la bibliothèque pour le moment.";
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
    
    // Ensure subtitle overlay is visible in fullscreen
    const overlay = document.getElementById("subtitle-overlay");
    if (overlay && fullscreenElement === videoContainer) {
        // Force overlay to be visible and update its position
        overlay.style.zIndex = '2147483647';
        overlay.style.display = overlay.classList.contains('hidden') ? 'none' : 'flex';
    }
    
    // Also handle inline subtitle overlay in modal
    const inlineOverlay = document.querySelector('.subtitle-overlay-inline');
    if (inlineOverlay) {
        const detailContainer = document.getElementById('detail-video-player-container');
        const detailPlayer = document.getElementById('detail-video-player');
        if (detailContainer && detailPlayer && (fullscreenElement === detailContainer || fullscreenElement === detailPlayer)) {
            // In fullscreen, update overlay positioning to fixed
            inlineOverlay.style.zIndex = '2147483647';
            inlineOverlay.style.position = 'fixed';
            inlineOverlay.style.left = '50%';
            inlineOverlay.style.bottom = '8%';
            inlineOverlay.style.transform = 'translateX(-50%)';
            inlineOverlay.style.fontSize = 'clamp(18px, 2.8vw, 34px)';
            inlineOverlay.style.maxWidth = '80%';
            inlineOverlay.style.display = inlineOverlay.children.length === 0 ? 'none' : 'flex';
            console.log("Fullscreen: subtitle overlay updated for inline player");
        } else if (detailContainer && !fullscreenElement) {
            // Not in fullscreen, restore normal positioning
            inlineOverlay.style.position = 'absolute';
            inlineOverlay.style.left = '50%';
            inlineOverlay.style.bottom = '12%';
            inlineOverlay.style.transform = 'translateX(-50%)';
            inlineOverlay.style.fontSize = '18px';
            inlineOverlay.style.maxWidth = '90%';
            inlineOverlay.style.zIndex = '1000';
            console.log("Exited fullscreen: subtitle overlay restored for inline player");
        }
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
        
        // Cache video - include credentials to send session cookie
        const response = await fetch(url, {
            credentials: "include"  // Include cookies (session_id) in request
        });
        if (response.ok) {
            await cache.put(url, response.clone());
            
            // Cache all available subtitles for this video
            if (availableSubtitles && availableSubtitles.length > 0) {
                progressMsg.textContent = "Mise en cache de la vidéo et des sous-titres...";
                for (const subLang of availableSubtitles) {
                    try {
                        const subResponse = await fetch(`/videos/${encodeURIComponent(videoId)}/subs/${encodeURIComponent(subLang)}`, {
                            credentials: "include"  // Include cookies (session_id) in request
                        });
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
            credentials: "include",
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

// Function to update source language fieldset visibility for video
function updateSourceLanguageVisibility() {
    if (!sourceLanguageFieldset) return;
    
    const fileType = document.querySelector('input[name="file_type"]:checked')?.value;
    if (fileType === "video") {
        const transcribeCheckbox = document.querySelector('input[name="process_options"][value="transcribe"]');
        const isTranscribeChecked = transcribeCheckbox?.checked || false;
        sourceLanguageFieldset.style.display = isTranscribeChecked ? "block" : "none";
    }
}

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
                    updateSourceLanguageVisibility(); // Check if transcribe is selected
                    ttsSpeedFieldset.style.display = "block";
                    // Show thumbnail fieldset for videos
                    const thumbnailFieldsetEl = document.getElementById("thumbnail-fieldset");
                    if (thumbnailFieldsetEl) thumbnailFieldsetEl.style.display = "block";
                } else if (fileType === "audio") {
                    fileInputForType.accept = "audio/*";
                    if (fileTypeLabel) fileTypeLabel.textContent = "Fichier audio :";
                    videoOptions.style.display = "none";
                    audioOptions.style.display = "block";
                    textOptions.style.display = "none";
                    languagesFieldset.style.display = "block";
                    sourceLanguageFieldset.style.display = "block";
                    ttsSpeedFieldset.style.display = "none";
                    // Hide thumbnail fieldset for non-videos
                    const thumbnailFieldsetEl = document.getElementById("thumbnail-fieldset");
                    if (thumbnailFieldsetEl) thumbnailFieldsetEl.style.display = "none";
                } else if (fileType === "text") {
                    fileInputForType.accept = ".txt";
                    if (fileTypeLabel) fileTypeLabel.textContent = "Fichier texte :";
                    videoOptions.style.display = "none";
                    audioOptions.style.display = "none";
                    textOptions.style.display = "block";
                    languagesFieldset.style.display = "block";
                    sourceLanguageFieldset.style.display = "block";
                    ttsSpeedFieldset.style.display = "none";
                    // Hide thumbnail fieldset for non-videos
                    const thumbnailFieldsetEl = document.getElementById("thumbnail-fieldset");
                    if (thumbnailFieldsetEl) thumbnailFieldsetEl.style.display = "none";
                }
            }
        });
    });
}

// Listen for changes to process_options checkboxes to show/hide source language for video
const processOptionsCheckboxes = document.querySelectorAll('input[name="process_options"]');
processOptionsCheckboxes.forEach(checkbox => {
    checkbox.addEventListener("change", () => {
        const fileType = document.querySelector('input[name="file_type"]:checked')?.value;
        if (fileType === "video") {
            updateSourceLanguageVisibility();
        }
    });
});

// Thumbnail selection functionality
const thumbnailFieldset = document.getElementById("thumbnail-fieldset");
const thumbnailVideoFrame = document.getElementById("thumbnail-video-frame");
const thumbnailUpload = document.getElementById("thumbnail-upload");
const videoFrameSelector = document.getElementById("video-frame-selector");
const thumbnailUploadContainer = document.getElementById("thumbnail-upload-container");
const thumbnailFileInput = document.getElementById("thumbnail-file");
const previewFrameBtn = document.getElementById("preview-frame-btn");
const framePreview = document.getElementById("frame-preview");
const framePreviewImg = document.getElementById("frame-preview-img");
const thumbnailPreview = document.getElementById("thumbnail-preview");
const thumbnailPreviewImg = document.getElementById("thumbnail-preview-img");
const videoFileInput = document.getElementById("video-file");

// Show/hide thumbnail fieldset based on file type
// Initialize when DOM is ready
function initializeThumbnailFieldset() {
    const thumbnailFieldsetEl = document.getElementById("thumbnail-fieldset");
    if (!thumbnailFieldsetEl) return;
    
    // Function to update thumbnail visibility
    function updateThumbnailVisibility() {
        const selectedFileType = document.querySelector('input[name="file_type"]:checked')?.value;
        if (selectedFileType === "video") {
            thumbnailFieldsetEl.style.display = "block";
        } else {
            thumbnailFieldsetEl.style.display = "none";
        }
    }
    
    const fileTypeRadios = document.querySelectorAll('input[name="file_type"]');
    fileTypeRadios.forEach(radio => {
        radio.addEventListener("change", updateThumbnailVisibility);
    });
    
    // Check initial state on page load
    updateThumbnailVisibility();
    
    // Also check after a short delay to ensure everything is loaded
    setTimeout(updateThumbnailVisibility, 200);
    setTimeout(updateThumbnailVisibility, 500);
}

// Initialize thumbnail fieldset when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        initializeThumbnailFieldset();
        // Also check after DOM is fully loaded
        setTimeout(initializeThumbnailFieldset, 100);
    });
} else {
    initializeThumbnailFieldset();
    // Also check after a short delay
    setTimeout(initializeThumbnailFieldset, 100);
}

// Toggle thumbnail source options - initialize when elements are available
function initializeThumbnailToggles() {
    const thumbnailVideoFrameEl = document.getElementById("thumbnail-video-frame");
    const thumbnailUploadEl = document.getElementById("thumbnail-upload");
    const videoFrameSelectorEl = document.getElementById("video-frame-selector");
    const thumbnailUploadContainerEl = document.getElementById("thumbnail-upload-container");
    
    if (!thumbnailVideoFrameEl || !thumbnailUploadEl) {
        return; // Elements not found yet
    }
    
    // Function to update visibility based on selected option
    function updateThumbnailSourceVisibility() {
        console.log("Updating thumbnail source visibility", {
            videoFrameChecked: thumbnailVideoFrameEl.checked,
            uploadChecked: thumbnailUploadEl.checked
        });
        if (thumbnailVideoFrameEl.checked) {
            if (videoFrameSelectorEl) videoFrameSelectorEl.style.display = "block";
            if (thumbnailUploadContainerEl) {
                thumbnailUploadContainerEl.style.display = "none";
                console.log("Hiding upload container");
            }
        } else if (thumbnailUploadEl.checked) {
            if (videoFrameSelectorEl) videoFrameSelectorEl.style.display = "none";
            if (thumbnailUploadContainerEl) {
                thumbnailUploadContainerEl.style.display = "block";
                console.log("Showing upload container");
            }
        }
    }
    
    // Add event listeners (use capture to ensure they fire)
    thumbnailVideoFrameEl.addEventListener("change", updateThumbnailSourceVisibility, true);
    thumbnailUploadEl.addEventListener("change", updateThumbnailSourceVisibility, true);
    
    // Also add click listeners as backup
    thumbnailVideoFrameEl.addEventListener("click", () => {
        setTimeout(updateThumbnailSourceVisibility, 10);
    });
    thumbnailUploadEl.addEventListener("click", () => {
        setTimeout(updateThumbnailSourceVisibility, 10);
    });
    
    // Check initial state
    updateThumbnailSourceVisibility();
    
    // Also check after a delay to ensure it works
    setTimeout(updateThumbnailSourceVisibility, 200);
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        initializeThumbnailToggles();
        setTimeout(initializeThumbnailToggles, 100);
    });
} else {
    initializeThumbnailToggles();
    setTimeout(initializeThumbnailToggles, 100);
}

// Preview frame from video - initialize when elements are available
function initializeFramePreview() {
    const previewFrameBtnEl = document.getElementById("preview-frame-btn");
    const videoFileInputEl = document.getElementById("video-file");
    const framePreviewEl = document.getElementById("frame-preview");
    const framePreviewImgEl = document.getElementById("frame-preview-img");
    
    if (previewFrameBtnEl && videoFileInputEl) {
        previewFrameBtnEl.addEventListener("click", async () => {
            const file = videoFileInputEl.files?.[0];
            if (!file) {
                alert("Veuillez d'abord sélectionner une vidéo.");
                return;
            }
            
            const time = parseFloat(document.getElementById("thumbnail-time")?.value || 0);
            
            try {
                // Create a video element to extract frame
                const video = document.createElement("video");
                video.preload = "metadata";
                video.src = URL.createObjectURL(file);
                
                video.onloadedmetadata = () => {
                    video.currentTime = Math.min(time, video.duration || 0);
                };
                
                video.onseeked = () => {
                    const canvas = document.createElement("canvas");
                    canvas.width = video.videoWidth;
                    canvas.height = video.videoHeight;
                    const ctx = canvas.getContext("2d");
                    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                    
                    if (framePreviewImgEl) {
                        framePreviewImgEl.src = canvas.toDataURL("image/png");
                        if (framePreviewEl) framePreviewEl.style.display = "block";
                    }
                    
                    URL.revokeObjectURL(video.src);
                };
                
                video.onerror = () => {
                    alert("Erreur lors de la lecture de la vidéo.");
                    URL.revokeObjectURL(video.src);
                };
            } catch (err) {
                console.error("Error previewing frame:", err);
                alert("Erreur lors de l'extraction du frame.");
            }
        });
    }
}

// Preview uploaded thumbnail - initialize when elements are available
function initializeThumbnailPreview() {
    const thumbnailFileInputEl = document.getElementById("thumbnail-file");
    const thumbnailPreviewEl = document.getElementById("thumbnail-preview");
    const thumbnailPreviewImgEl = document.getElementById("thumbnail-preview-img");
    
    if (thumbnailFileInputEl) {
        thumbnailFileInputEl.addEventListener("change", (e) => {
            const file = e.target.files?.[0];
            if (file && file.type.startsWith("image/")) {
                const reader = new FileReader();
                reader.onload = (event) => {
                    if (thumbnailPreviewImgEl) {
                        thumbnailPreviewImgEl.src = event.target.result;
                        if (thumbnailPreviewEl) thumbnailPreviewEl.style.display = "block";
                    }
                };
                reader.readAsDataURL(file);
            } else {
                if (thumbnailPreviewEl) thumbnailPreviewEl.style.display = "none";
            }
        });
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        initializeFramePreview();
        initializeThumbnailPreview();
    });
} else {
    initializeFramePreview();
    initializeThumbnailPreview();
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
        const needsTranscribe = checkedOptions.some(opt => opt.value === "transcribe");
        
        // Add source language if transcribe is selected
        if (needsTranscribe) {
            const sourceLang = form.querySelector('select[name="source_language"]')?.value;
            if (sourceLang) {
                formData.append("source_language", sourceLang);
                console.log("Added source language for transcription:", sourceLang);
            }
        }
        
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
    
    // Add thumbnail data if video and thumbnail is selected
    if (fileType === "video") {
        const thumbnailSource = form.querySelector('input[name="thumbnail_source"]:checked')?.value;
        if (thumbnailSource) {
            formData.append("thumbnail_source", thumbnailSource);
            
            if (thumbnailSource === "video_frame") {
                const thumbnailTime = document.getElementById("thumbnail-time")?.value || "0";
                formData.append("thumbnail_time", thumbnailTime);
            } else if (thumbnailSource === "upload") {
                const thumbnailFile = document.getElementById("thumbnail-file")?.files?.[0];
                if (thumbnailFile) {
                    formData.append("thumbnail_file", thumbnailFile);
                }
            }
        }
    }

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
                        
                        // If it's a permission error, suggest re-authenticating
                        if (res.status === 403 && (errorMessage.includes("éditeurs") || errorMessage.includes("editor"))) {
                            const shouldReload = confirm("Votre session a peut-être expiré. Voulez-vous recharger la page pour vous reconnecter?");
                            if (shouldReload) {
                                window.location.href = "/";
                                return;
                            }
                        }
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

// Text-to-Video Generation (Hidden Feature)
// Only show if feature is enabled (check via API)
async function checkTextToVideoEnabled() {
    try {
        // Try to fetch a test endpoint or check config
        // For now, we'll check if the form exists and show it conditionally
        const textToVideoSection = document.getElementById("text-to-video-section");
        if (textToVideoSection) {
            // Initially hidden - can be enabled via environment variable
            // For now, keep it hidden until explicitly enabled
            textToVideoSection.style.display = "none";
        }
    } catch (err) {
        console.error("Error checking text-to-video feature", err);
    }
}

// Handle text-to-video form submission
const textToVideoForm = document.getElementById("text-to-video-form");
if (textToVideoForm) {
    textToVideoForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        
        const submitBtn = document.getElementById("text-to-video-submit");
        const statusDiv = document.getElementById("text-to-video-status");
        const textInput = document.getElementById("text-to-video-input");
        const modelInput = document.getElementById("text-to-video-model");
        const sentenceCheckbox = document.getElementById("text-to-video-sentence");
        const folderSelect = document.getElementById("text-to-video-folder");
        const privateCheckbox = document.getElementById("text-to-video-private");
        
        const text = textInput.value.trim();
        if (!text) {
            statusDiv.textContent = "Erreur: Veuillez entrer du texte.";
            statusDiv.style.display = "block";
            statusDiv.style.color = "#dc3545";
            return;
        }
        
        // Disable submit button
        submitBtn.disabled = true;
        submitBtn.textContent = "Génération en cours...";
        
        // Show status
        statusDiv.textContent = "Démarrage de la génération...";
        statusDiv.style.display = "block";
        statusDiv.style.color = "#007bff";
        
        try {
            const formData = new FormData();
            formData.append("text", text);
            if (characterSelect && characterSelect.value) {
                formData.append("character_id", characterSelect.value);
            } else if (modelInput.value.trim()) {
                formData.append("model_name", modelInput.value.trim());
            }
            formData.append("image_per_sentence", sentenceCheckbox.checked ? "true" : "false");
            if (folderSelect.value) {
                formData.append("folder_path", folderSelect.value);
            }
            formData.append("is_private", privateCheckbox.checked ? "true" : "false");
            
            const response = await fetch("/api/text-to-video", {
                method: "POST",
                body: formData,
                credentials: "include"
            });
            
            const result = await response.json();
            
            if (!response.ok) {
                throw new Error(result.error || "Erreur lors de la génération");
            }
            
            const jobId = result.job_id;
            statusDiv.textContent = `Génération démarrée (ID: ${jobId}). Suivi du statut...`;
            
            // Poll job status
            const pollInterval = setInterval(async () => {
                try {
                    const statusResponse = await fetch(`/api/jobs/${jobId}`, {
                        credentials: "include"
                    });
                    
                    if (statusResponse.ok) {
                        const statusData = await statusResponse.json();
                        const status = statusData.status;
                        
                        if (status === "completed") {
                            clearInterval(pollInterval);
                            statusDiv.textContent = "✅ Vidéo générée avec succès!";
                            statusDiv.style.color = "#28a745";
                            submitBtn.disabled = false;
                            submitBtn.textContent = "Générer la vidéo";
                            
                            // Refresh video list
                            await fetchVideos();
                            
                            // Clear form
                            textInput.value = "";
                            modelInput.value = "";
                        } else if (status === "failed") {
                            clearInterval(pollInterval);
                            statusDiv.textContent = `❌ Erreur: ${statusData.error || "Échec de la génération"}`;
                            statusDiv.style.color = "#dc3545";
                            submitBtn.disabled = false;
                            submitBtn.textContent = "Générer la vidéo";
                        } else {
                            // Still processing
                            statusDiv.textContent = `⏳ Génération en cours... (${status})`;
                        }
                    }
                } catch (err) {
                    console.error("Error polling job status", err);
                }
            }, 2000); // Poll every 2 seconds
            
            // Stop polling after 10 minutes
            setTimeout(() => {
                clearInterval(pollInterval);
                if (submitBtn.disabled) {
                    statusDiv.textContent = "⚠️ Le processus prend plus de temps que prévu. Vérifiez la bibliothèque pour voir si la vidéo a été générée.";
                    statusDiv.style.color = "#ffc107";
                    submitBtn.disabled = false;
                    submitBtn.textContent = "Générer la vidéo";
                }
            }, 600000); // 10 minutes
            
        } catch (err) {
            statusDiv.textContent = `❌ Erreur: ${err.message}`;
            statusDiv.style.color = "#dc3545";
            submitBtn.disabled = false;
            submitBtn.textContent = "Générer la vidéo";
        }
    });
    
    // Populate folder dropdown for text-to-video form
    const textToVideoFolderSelect = document.getElementById("text-to-video-folder");
    if (textToVideoFolderSelect) {
        // Reuse the same populateFolderDropdown function
        // But we need to also populate this dropdown
        async function populateTextToVideoFolderDropdown() {
            try {
                const response = await fetch("/api/folders", {
                    credentials: "include"
                });
                if (!response.ok) return;
                
                const folders = await response.json();
                const currentValue = textToVideoFolderSelect.value;
                
                // Clear existing options (keep first "Aucun dossier" option)
                while (textToVideoFolderSelect.children.length > 1) {
                    textToVideoFolderSelect.remove(1);
                }
                
                const sortedFolders = [...folders].sort((a, b) => a.path.localeCompare(b.path));
                sortedFolders.forEach(folder => {
                    const option = document.createElement("option");
                    option.value = folder.path;
                    option.textContent = folder.path + (folder.is_private ? " [PRIVÉ]" : "");
                    textToVideoFolderSelect.appendChild(option);
                });
                
                if (currentValue && Array.from(textToVideoFolderSelect.options).some(opt => opt.value === currentValue)) {
                    textToVideoFolderSelect.value = currentValue;
                }
            } catch (err) {
                console.error("Failed to load folders for text-to-video dropdown", err);
            }
        }
        
        // Populate on load and refresh periodically
        populateTextToVideoFolderDropdown();
        setInterval(populateTextToVideoFolderDropdown, 5000);
    }
    
    // Populate character dropdown for text-to-video (only for admins)
    const textToVideoCharacterSelect = document.getElementById("text-to-video-character");
    if (textToVideoCharacterSelect) {
        // Check if user is admin by checking if the video generation section exists (only shown to admins)
        const videoGenSection = document.getElementById("video-generation-section");
        const isAdmin = videoGenSection !== null;
        
        if (isAdmin) {
            async function populateTextToVideoCharacterDropdown() {
                try {
                    const response = await fetch("/api/characters", {
                        credentials: "include"
                    });
                    // Silently ignore 403 (forbidden) - user is not admin
                    if (!response.ok) {
                        if (response.status === 403) {
                            return; // User is not admin, silently ignore
                        }
                        return;
                    }
                    
                    const chars = await response.json();
                    const currentValue = textToVideoCharacterSelect.value;
                    
                    // Clear existing options (keep first "Aucun personnage" option)
                    while (textToVideoCharacterSelect.children.length > 1) {
                        textToVideoCharacterSelect.remove(1);
                    }
                    
                    // Add only completed characters
                    chars.filter(char => char.status === "completed").forEach(char => {
                        const option = document.createElement("option");
                        option.value = char.id;
                        option.textContent = `${char.name} (${char.token})`;
                        textToVideoCharacterSelect.appendChild(option);
                    });
                    
                    if (currentValue && Array.from(textToVideoCharacterSelect.options).some(opt => opt.value === currentValue)) {
                        textToVideoCharacterSelect.value = currentValue;
                    }
                } catch (err) {
                    // Silently ignore errors - user might not have access
                    if (err.message && !err.message.includes("403")) {
                        console.error("Failed to load characters for text-to-video dropdown", err);
                    }
                }
            }
            
            // Only start polling if user is admin
            populateTextToVideoCharacterDropdown();
            const characterInterval = setInterval(() => {
                // Re-check if still admin before polling
                const stillAdmin = document.getElementById("video-generation-section") !== null;
                if (stillAdmin) {
                    populateTextToVideoCharacterDropdown();
                } else {
                    clearInterval(characterInterval);
                }
            }, 10000);
        }
    }
}

// Character Management
let characters = [];

async function fetchCharacters() {
    try {
        const response = await fetch("/api/characters", {
            credentials: "include"
        });
        if (!response.ok) return;
        
        characters = await response.json();
        renderCharacters();
    } catch (err) {
        console.error("Error fetching characters", err);
    }
}

function renderCharacters() {
    const listDiv = document.getElementById("characters-list");
    if (!listDiv) return;
    
    if (characters.length === 0) {
        listDiv.innerHTML = "<p>Aucun personnage créé. Cliquez sur '+ Personnage' pour en créer un.</p>";
        return;
    }
    
    listDiv.innerHTML = "";
    
    characters.forEach(char => {
        const charDiv = document.createElement("div");
        charDiv.style.cssText = "border: 1px solid #ddd; padding: 15px; margin-bottom: 10px; border-radius: 5px; background: white;";
        
        const statusColors = {
            "pending": "#ffc107",
            "training": "#007bff",
            "completed": "#28a745",
            "failed": "#dc3545"
        };
        
        const statusLabels = {
            "pending": "En attente",
            "training": "Entraînement en cours",
            "completed": "Terminé",
            "failed": "Échec"
        };
        
        charDiv.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: start;">
                <div style="flex: 1;">
                    <h4 style="margin: 0 0 5px 0;">${escapeHtml(char.name)}</h4>
                    <p style="margin: 5px 0; color: #666;">
                        <strong>Token:</strong> <code>${escapeHtml(char.token)}</code> ${char.class_word ? `<code>${escapeHtml(char.class_word)}</code>` : ''}
                    </p>
                    ${char.description ? `<p style="margin: 5px 0; color: #666;"><strong>Description:</strong> ${escapeHtml(char.description)}</p>` : ''}
                    <p style="margin: 5px 0;">
                        <span style="background: ${statusColors[char.status] || '#6c757d'}; color: white; padding: 3px 8px; border-radius: 3px; font-size: 0.9em;">
                            ${statusLabels[char.status] || char.status}
                        </span>
                    </p>
                    <p style="margin: 5px 0; color: #666; font-size: 0.9em;">
                        Images d'entraînement: ${char.training_images_count}
                    </p>
                    ${char.error ? `<p style="margin: 5px 0; color: #dc3545; font-size: 0.9em;">Erreur: ${escapeHtml(char.error)}</p>` : ''}
                </div>
                <div style="margin-left: 15px;">
                    <button type="button" class="view-character-btn" data-id="${char.id}" style="padding: 5px 10px; background: #007bff; color: white; border: none; border-radius: 3px; cursor: pointer; margin-bottom: 5px; display: block; width: 100%;">
                        Voir détails
                    </button>
                    ${char.status === "completed" ? `
                        <button type="button" class="use-character-btn" data-id="${char.id}" data-token="${char.token}" style="padding: 5px 10px; background: #28a745; color: white; border: none; border-radius: 3px; cursor: pointer; margin-bottom: 5px; display: block; width: 100%;">
                            Utiliser
                        </button>
                    ` : ''}
                    <button type="button" class="delete-character-btn" data-id="${char.id}" style="padding: 5px 10px; background: #dc3545; color: white; border: none; border-radius: 3px; cursor: pointer; display: block; width: 100%;">
                        Supprimer
                    </button>
                </div>
            </div>
        `;
        
        listDiv.appendChild(charDiv);
    });
    
    // Add event listeners
    document.querySelectorAll(".view-character-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const charId = btn.getAttribute("data-id");
            showCharacterDetail(charId);
        });
    });
    
    document.querySelectorAll(".use-character-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const charId = btn.getAttribute("data-id");
            const token = btn.getAttribute("data-token");
            useCharacterInVideo(token);
        });
    });
    
    document.querySelectorAll(".delete-character-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const charId = btn.getAttribute("data-id");
            if (confirm("Êtes-vous sûr de vouloir supprimer ce personnage ?")) {
                deleteCharacter(charId);
            }
        });
    });
}

async function showCharacterDetail(characterId) {
    try {
        const response = await fetch(`/api/characters/${characterId}`, {
            credentials: "include"
        });
        if (!response.ok) {
            alert("Erreur lors du chargement du personnage");
            return;
        }
        
        const char = await response.json();
        const modal = document.getElementById("character-detail-modal");
        const nameDiv = document.getElementById("character-detail-name");
        const contentDiv = document.getElementById("character-detail-content");
        
        nameDiv.textContent = char.name;
        
        contentDiv.innerHTML = `
            <div style="margin-bottom: 15px;">
                <strong>Token:</strong> <code>${escapeHtml(char.token)}</code> <code>${escapeHtml(char.class_word)}</code>
            </div>
            <div style="margin-bottom: 15px;">
                <strong>Description:</strong> ${escapeHtml(char.description || "Aucune")}
            </div>
            <div style="margin-bottom: 15px;">
                <strong>Statut:</strong> ${char.status}
            </div>
            <div style="margin-bottom: 15px;">
                <strong>Images d'entraînement:</strong> ${char.training_images_count}
            </div>
            ${char.model_path ? `<div style="margin-bottom: 15px;"><strong>Modèle:</strong> ${escapeHtml(char.model_path)}</div>` : ''}
            ${char.error ? `<div style="margin-bottom: 15px; color: #dc3545;"><strong>Erreur:</strong> ${escapeHtml(char.error)}</div>` : ''}
            <div style="margin-top: 20px;">
                <h4>Ajouter des images d'entraînement</h4>
                <input type="file" id="character-images-input" multiple accept="image/*" style="margin-bottom: 10px;">
                <button type="button" id="upload-character-images-btn" data-id="${char.id}" style="padding: 8px 15px; background: #007bff; color: white; border: none; border-radius: 5px; cursor: pointer;">
                    Télécharger les images
                </button>
            </div>
            ${char.status === "pending" && char.training_images_count > 0 ? `
                <div style="margin-top: 20px;">
                    <button type="button" id="train-character-btn" data-id="${char.id}" style="padding: 10px 20px; background: #28a745; color: white; border: none; border-radius: 5px; cursor: pointer;">
                        Démarrer l'entraînement
                    </button>
                </div>
            ` : ''}
        `;
        
        modal.style.display = "block";
        
        // Add event listeners
        const uploadBtn = document.getElementById("upload-character-images-btn");
        if (uploadBtn) {
            uploadBtn.addEventListener("click", async () => {
                const input = document.getElementById("character-images-input");
                if (!input.files || input.files.length === 0) {
                    alert("Veuillez sélectionner des images");
                    return;
                }
                
                await uploadCharacterImages(char.id, input.files);
            });
        }
        
        const trainBtn = document.getElementById("train-character-btn");
        if (trainBtn) {
            trainBtn.addEventListener("click", async () => {
                await trainCharacter(char.id);
            });
        }
        
    } catch (err) {
        console.error("Error showing character detail", err);
        alert("Erreur lors du chargement du personnage");
    }
}

async function uploadCharacterImages(characterId, files) {
    try {
        const formData = new FormData();
        for (let i = 0; i < files.length; i++) {
            formData.append("files", files[i]);
        }
        
        const response = await fetch(`/api/characters/${characterId}/images`, {
            method: "POST",
            body: formData,
            credentials: "include"
        });
        
        if (!response.ok) {
            const error = await response.json();
            alert(error.error || "Erreur lors du téléchargement");
            return;
        }
        
        const result = await response.json();
        alert(result.message);
        await fetchCharacters();
        await showCharacterDetail(characterId); // Refresh detail view
    } catch (err) {
        console.error("Error uploading images", err);
        alert("Erreur lors du téléchargement des images");
    }
}

async function trainCharacter(characterId) {
    if (!confirm("Démarrer l'entraînement ? Cela peut prendre beaucoup de temps.")) {
        return;
    }
    
    try {
        const response = await fetch(`/api/characters/${characterId}/train`, {
            method: "POST",
            credentials: "include"
        });
        
        if (!response.ok) {
            const error = await response.json();
            alert(error.error || "Erreur lors du démarrage de l'entraînement");
            return;
        }
        
        alert("Entraînement démarré. Vous pouvez fermer cette fenêtre et vérifier le statut plus tard.");
        await fetchCharacters();
        await showCharacterDetail(characterId);
    } catch (err) {
        console.error("Error training character", err);
        alert("Erreur lors du démarrage de l'entraînement");
    }
}

async function deleteCharacter(characterId) {
    try {
        const response = await fetch(`/api/characters/${characterId}`, {
            method: "DELETE",
            credentials: "include"
        });
        
        if (!response.ok) {
            const error = await response.json();
            alert(error.error || "Erreur lors de la suppression");
            return;
        }
        
        await fetchCharacters();
    } catch (err) {
        console.error("Error deleting character", err);
        alert("Erreur lors de la suppression");
    }
}

function useCharacterInVideo(token) {
    // Set the character token in the text-to-video form
    const textToVideoInput = document.getElementById("text-to-video-input");
    if (textToVideoInput) {
        // Show text-to-video section if hidden
        const textToVideoSection = document.getElementById("text-to-video-section");
        if (textToVideoSection) {
            textToVideoSection.style.display = "block";
        }
        
        // Add token to prompt
        const currentText = textToVideoInput.value.trim();
        if (currentText) {
            textToVideoInput.value = `${currentText}, ${token} person`;
        } else {
            textToVideoInput.value = `${token} person`;
        }
        
        // Scroll to text-to-video section
        textToVideoSection?.scrollIntoView({ behavior: "smooth" });
    }
}

// Character form handling
const createCharacterBtn = document.getElementById("create-character-btn");
const characterModal = document.getElementById("character-modal");
const characterForm = document.getElementById("character-form");
const cancelCharacterBtn = document.getElementById("cancel-character-btn");
const closeCharacterDetailBtn = document.getElementById("close-character-detail-btn");

// Only show create button for admin
if (createCharacterBtn && !isAdmin) {
    createCharacterBtn.style.display = "none";
}

if (createCharacterBtn && isAdmin) {
    createCharacterBtn.addEventListener("click", () => {
        if (characterModal) {
            characterModal.style.display = "block";
            document.getElementById("character-name")?.focus();
        }
    });
}

if (cancelCharacterBtn) {
    cancelCharacterBtn.addEventListener("click", () => {
        if (characterModal) {
            characterModal.style.display = "none";
            characterForm?.reset();
        }
    });
}

if (closeCharacterDetailBtn) {
    closeCharacterDetailBtn.addEventListener("click", () => {
        const modal = document.getElementById("character-detail-modal");
        if (modal) {
            modal.style.display = "none";
        }
    });
}

// Close modals when clicking outside
if (characterModal) {
    characterModal.addEventListener("click", (e) => {
        if (e.target === characterModal) {
            characterModal.style.display = "none";
            characterForm?.reset();
        }
    });
}

const characterDetailModal = document.getElementById("character-detail-modal");
if (characterDetailModal) {
    characterDetailModal.addEventListener("click", (e) => {
        if (e.target === characterDetailModal) {
            characterDetailModal.style.display = "none";
        }
    });
}

if (characterForm) {
    characterForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        
        const formData = new FormData(characterForm);
        
        try {
            const response = await fetch("/api/characters", {
                method: "POST",
                body: formData,
                credentials: "include"
            });
            
            if (!response.ok) {
                const error = await response.json();
                alert(error.error || "Erreur lors de la création");
                return;
            }
            
            const character = await response.json();
            alert(`Personnage "${character.name}" créé avec succès !`);
            characterModal.style.display = "none";
            characterForm.reset();
            await fetchCharacters();
        } catch (err) {
            console.error("Error creating character", err);
            alert("Erreur lors de la création du personnage");
        }
    });
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

// Message to Admin functionality
const messageToAdminForm = document.getElementById("message-to-admin-form");
if (messageToAdminForm) {
    messageToAdminForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        
        const messageInput = document.getElementById("admin-message-input");
        const statusDiv = document.getElementById("message-status");
        const message = messageInput.value.trim();
        
        if (!message) {
            statusDiv.textContent = "Veuillez entrer un message.";
            statusDiv.style.display = "block";
            statusDiv.style.color = "#dc3545";
            return;
        }
        
        try {
            const formData = new FormData();
            formData.append("message", message);
            
            const response = await fetch("/api/messages", {
                method: "POST",
                body: formData,
                credentials: "include"
            });
            
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || "Erreur lors de l'envoi");
            }
            
            statusDiv.textContent = "✅ Message envoyé avec succès !";
            statusDiv.style.display = "block";
            statusDiv.style.color = "#28a745";
            messageInput.value = "";
            
            // Clear status after 3 seconds
            setTimeout(() => {
                statusDiv.style.display = "none";
            }, 3000);
            
        } catch (err) {
            statusDiv.textContent = `❌ Erreur: ${err.message}`;
            statusDiv.style.display = "block";
            statusDiv.style.color = "#dc3545";
        }
    });
}

// Admin Messages functionality
async function fetchAdminMessages() {
    const messagesList = document.getElementById("admin-messages-list");
    if (!messagesList) return;
    
    try {
        const response = await fetch("/api/messages", {
            credentials: "include"
        });
        
        if (!response.ok) {
            // If 404, the endpoint doesn't exist (not implemented yet) - silently ignore
            if (response.status === 404) {
                return;
            }
            // If 403, user is not admin - silently ignore
            if (response.status === 403) {
                return;
            }
            messagesList.innerHTML = "<p style='color: #dc3545;'>Erreur lors du chargement des messages.</p>";
            return;
        }
        
        const messages = await response.json();
        
        if (messages.length === 0) {
            messagesList.innerHTML = "<p>Aucun message reçu.</p>";
            return;
        }
        
        let html = "";
        messages.forEach(msg => {
            const isRead = msg.read || false;
            const date = new Date(msg.created_at).toLocaleString("fr-FR");
            const unreadBadge = !isRead ? '<span style="background: #dc3545; color: white; padding: 2px 8px; border-radius: 3px; font-size: 0.8em; margin-left: 10px;">Nouveau</span>' : '';
            
            html += `
                <div style="border: 1px solid #ddd; padding: 15px; margin-bottom: 10px; border-radius: 5px; background: white; ${!isRead ? 'border-left: 4px solid #dc3545;' : ''}">
                    <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 10px;">
                        <div>
                            <strong>${escapeHtml(msg.sender_name || msg.sender_role)}</strong>
                            <span style="color: #666; font-size: 0.9em;">(${msg.sender_role})</span>
                            ${unreadBadge}
                        </div>
                        <div style="color: #666; font-size: 0.9em;">${date}</div>
                    </div>
                    <p style="margin: 10px 0; white-space: pre-wrap;">${escapeHtml(msg.message)}</p>
                    <div style="margin-top: 10px;">
                        ${!isRead ? `
                            <button type="button" class="mark-read-btn" data-id="${msg.id}" style="padding: 5px 10px; background: #28a745; color: white; border: none; border-radius: 3px; cursor: pointer; margin-right: 5px;">
                                Marquer comme lu
                            </button>
                        ` : ''}
                        <button type="button" class="delete-message-btn" data-id="${msg.id}" style="padding: 5px 10px; background: #dc3545; color: white; border: none; border-radius: 3px; cursor: pointer;">
                            Supprimer
                        </button>
                    </div>
                </div>
            `;
        });
        
        messagesList.innerHTML = html;
        
        // Add event listeners
        document.querySelectorAll(".mark-read-btn").forEach(btn => {
            btn.addEventListener("click", async () => {
                const messageId = btn.getAttribute("data-id");
                await markMessageAsRead(messageId);
            });
        });
        
        document.querySelectorAll(".delete-message-btn").forEach(btn => {
            btn.addEventListener("click", async () => {
                const messageId = btn.getAttribute("data-id");
                if (confirm("Êtes-vous sûr de vouloir supprimer ce message ?")) {
                    await deleteMessage(messageId);
                }
            });
        });
        
    } catch (err) {
        console.error("Error fetching admin messages", err);
        messagesList.innerHTML = "<p style='color: #dc3545;'>Erreur lors du chargement des messages.</p>";
    }
}

async function markMessageAsRead(messageId) {
    try {
        const response = await fetch(`/api/messages/${messageId}/read`, {
            method: "PUT",
            credentials: "include"
        });
        
        if (response.ok) {
            await fetchAdminMessages();
        }
    } catch (err) {
        console.error("Error marking message as read", err);
        alert("Erreur lors du marquage du message");
    }
}

async function deleteMessage(messageId) {
    try {
        const response = await fetch(`/api/messages/${messageId}`, {
            method: "DELETE",
            credentials: "include"
        });
        
        if (response.ok) {
            await fetchAdminMessages();
        }
    } catch (err) {
        console.error("Error deleting message", err);
        alert("Erreur lors de la suppression du message");
    }
}

window.addEventListener("load", () => {
    fetchVideos();
    populateFolderDropdown();
    checkTextToVideoEnabled();
    // Only fetch messages if admin section exists
    const adminMessagesSection = document.getElementById("admin-messages-section");
    if (adminMessagesSection) {
        fetchAdminMessages(); // Load admin messages if admin
        const messagesInterval = setInterval(() => {
            // Re-check if still admin before polling
            const stillAdmin = document.getElementById("admin-messages-section") !== null;
            if (stillAdmin) {
                fetchAdminMessages();
            } else {
                clearInterval(messagesInterval);
            }
        }, 30000); // Refresh admin messages every 30 seconds
    }
    // Refresh folder dropdown when videos are fetched (in case folders changed)
    setInterval(populateFolderDropdown, 5000);
});

// Image Generation with ModelsLab Flux 2 Pro
const imageGenerationForm = document.getElementById("image-generation-form");
if (imageGenerationForm) {
    imageGenerationForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        
        const promptInput = document.getElementById("image-prompt-input");
        const widthInput = document.getElementById("image-width");
        const heightInput = document.getElementById("image-height");
        const generateBtn = document.getElementById("generate-image-btn");
        const statusDiv = document.getElementById("image-generation-status");
        const imageContainer = document.getElementById("generated-image-container");
        const generatedImage = document.getElementById("generated-image");
        const downloadLink = document.getElementById("download-image-link");
        
        const prompt = promptInput.value.trim();
        const width = parseInt(widthInput.value) || 1024;
        const height = parseInt(heightInput.value) || 1024;
        
        if (!prompt) {
            statusDiv.textContent = "❌ Veuillez entrer un prompt.";
            statusDiv.style.display = "block";
            statusDiv.style.color = "#dc3545";
            return;
        }
        
        // Disable button and show loading
        generateBtn.disabled = true;
        generateBtn.textContent = "⏳ Génération en cours...";
        statusDiv.textContent = "⏳ Génération de l'image en cours, veuillez patienter...";
        statusDiv.style.display = "block";
        statusDiv.style.color = "#007bff";
        imageContainer.style.display = "none";
        
        try {
            const formData = new FormData();
            formData.append("prompt", prompt);
            formData.append("width", width);
            formData.append("height", height);
            
            const response = await fetch("/api/generate-image", {
                method: "POST",
                body: formData,
                credentials: "include"
            });
            
            const result = await response.json();
            
            if (!response.ok) {
                throw new Error(result.error || "Erreur lors de la génération");
            }
            
            if (result.success && result.image) {
                // Display the image
                generatedImage.src = result.image;
                imageContainer.style.display = "block";
                
                // Create download link
                const blob = await fetch(result.image).then(r => r.blob());
                const url = URL.createObjectURL(blob);
                downloadLink.href = url;
                downloadLink.download = `generated-image-${Date.now()}.png`;
                
                statusDiv.textContent = "✅ Image générée avec succès!";
                statusDiv.style.color = "#28a745";
            } else {
                throw new Error("Aucune image retournée");
            }
        } catch (err) {
            console.error("Error generating image", err);
            statusDiv.textContent = `❌ Erreur: ${err.message}`;
            statusDiv.style.color = "#dc3545";
            imageContainer.style.display = "none";
        } finally {
            generateBtn.disabled = false;
            generateBtn.textContent = "🎨 Générer l'image";
        }
    });
}

// Video Generation with ModelsLab Video Fusion
const videoGenerationForm = document.getElementById("video-generation-form");
if (videoGenerationForm) {
    // Preview for reference images
    const styleImageInput = document.getElementById("style-image");
    const styleImagePreview = document.getElementById("style-image-preview");
    const styleImagePreviewImg = document.getElementById("style-image-preview-img");
    
    if (styleImageInput) {
        styleImageInput.addEventListener("change", (e) => {
            const file = e.target.files?.[0];
            if (file && file.type.startsWith("image/")) {
                const reader = new FileReader();
                reader.onload = (event) => {
                    styleImagePreviewImg.src = event.target.result;
                    styleImagePreview.style.display = "block";
                };
                reader.readAsDataURL(file);
            } else {
                styleImagePreview.style.display = "none";
            }
        });
    }
    
    const environmentImageInput = document.getElementById("environment-image");
    const environmentImagePreview = document.getElementById("environment-image-preview");
    const environmentImagePreviewImg = document.getElementById("environment-image-preview-img");
    
    if (environmentImageInput) {
        environmentImageInput.addEventListener("change", (e) => {
            const file = e.target.files?.[0];
            if (file && file.type.startsWith("image/")) {
                const reader = new FileReader();
                reader.onload = (event) => {
                    environmentImagePreviewImg.src = event.target.result;
                    environmentImagePreview.style.display = "block";
                };
                reader.readAsDataURL(file);
            } else {
                environmentImagePreview.style.display = "none";
            }
        });
    }
    
    // Add/remove character functionality
    const addCharacterBtn = document.getElementById("add-character-btn");
    const charactersContainer = document.getElementById("characters-container");
    
    if (addCharacterBtn && charactersContainer) {
        addCharacterBtn.addEventListener("click", () => {
            const characterDiv = document.createElement("div");
            characterDiv.className = "character-upload";
            characterDiv.style.marginBottom = "10px";
            characterDiv.innerHTML = `
                <input type="text" name="character_name[]" placeholder="Nom du personnage" style="width: 200px; padding: 5px; margin-right: 10px;">
                <input type="file" name="character_image[]" accept="image/*" style="padding: 5px; width: 300px;">
                <button type="button" class="remove-character" style="padding: 5px 10px; background: #dc3545; color: white; border: none; border-radius: 3px; cursor: pointer; margin-left: 10px;">Supprimer</button>
            `;
            charactersContainer.appendChild(characterDiv);
            
            // Add remove functionality
            const removeBtn = characterDiv.querySelector(".remove-character");
            removeBtn.addEventListener("click", () => {
                characterDiv.remove();
            });
        });
        
        // Add remove functionality to existing character uploads
        document.querySelectorAll(".remove-character").forEach(btn => {
            btn.addEventListener("click", (e) => {
                e.target.closest(".character-upload").remove();
            });
        });
    }
    
    // Form submit handler
    videoGenerationForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        
        const promptInput = document.getElementById("video-prompt-input");
        const durationSelect = document.getElementById("video-duration");
        const generateBtn = document.getElementById("generate-video-btn");
        const statusDiv = document.getElementById("video-generation-status");
        const videoContainer = document.getElementById("generated-video-container");
        const generatedVideo = document.getElementById("generated-video");
        const downloadLink = document.getElementById("download-video-link");
        
        const prompt = promptInput.value.trim();
        const duration = parseInt(durationSelect.value) || 8;
        
        if (!prompt) {
            statusDiv.textContent = "❌ Veuillez entrer une description de scène.";
            statusDiv.style.display = "block";
            statusDiv.style.color = "#dc3545";
            return;
        }
        
        // Disable button and show loading
        generateBtn.disabled = true;
        generateBtn.textContent = "⏳ Génération en cours...";
        statusDiv.textContent = "⏳ Envoi de la requête à l'API ModelsLab...";
        statusDiv.style.display = "block";
        statusDiv.style.color = "#9c27b0";
        videoContainer.style.display = "none";
        
        // Show initial progress indicator
        let startTime = Date.now();
        let progressInterval = setInterval(() => {
            const elapsedSeconds = Math.floor((Date.now() - startTime) / 1000);
            const elapsedMinutes = Math.floor(elapsedSeconds / 60);
            if (elapsedSeconds < 10) {
                statusDiv.textContent = "⏳ Envoi de la requête à l'API ModelsLab...";
            } else if (elapsedSeconds < 30) {
                statusDiv.textContent = `⏳ Génération en cours... (${elapsedSeconds}s) - Cela peut prendre plusieurs minutes.`;
            } else {
                statusDiv.textContent = `⏳ Génération en cours... (${elapsedMinutes} min ${elapsedSeconds % 60}s) - Cela peut prendre 5-10 minutes, veuillez patienter.`;
            }
        }, 1000);
        
        let result;
        
        try {
            const formData = new FormData(videoGenerationForm);
            
            const response = await fetch("/api/generate-video", {
                method: "POST",
                body: formData,
                credentials: "include"
            });
            
            result = await response.json();
            
            // Clear the progress interval once we get a response
            clearInterval(progressInterval);
            
            if (!response.ok) {
                throw new Error(result.error || "Erreur lors de la génération");
            }
            
            if (result.success) {
                if (result.video_url) {
                    // Video URL returned
                    generatedVideo.src = result.video_url;
                    downloadLink.href = result.video_url;
                    downloadLink.download = `generated-video-${Date.now()}.mp4`;
                    videoContainer.style.display = "block";
                    statusDiv.textContent = "✅ Vidéo générée avec succès!";
                    statusDiv.style.color = "#28a745";
                } else if (result.video) {
                    // Base64 video returned
                    generatedVideo.src = result.video;
                    // Create blob for download
                    const videoResponse = await fetch(result.video);
                    const blob = await videoResponse.blob();
                    const url = URL.createObjectURL(blob);
                    downloadLink.href = url;
                    downloadLink.download = `generated-video-${Date.now()}.mp4`;
                    videoContainer.style.display = "block";
                    statusDiv.textContent = "✅ Vidéo générée avec succès!";
                    statusDiv.style.color = "#28a745";
                } else {
                    throw new Error("Aucune vidéo retournée");
                }
            } else if (result.status === "processing" && result.job_id) {
                // Video generation is processing asynchronously - start polling
                const jobId = result.job_id;
                statusDiv.textContent = `⏳ ${result.message || "La génération de la vidéo est en cours. Veuillez patienter..."}`;
                statusDiv.style.color = "#ffc107";
                videoContainer.style.display = "none";
                
                console.log("Video generation is processing. Job ID:", jobId);
                
                // Start polling for job status
                let pollCount = 0;
                const maxPolls = 120; // Poll for up to 10 minutes (120 * 5 seconds)
                const pollInterval = 5000; // Poll every 5 seconds
                
                const pollJobStatus = async () => {
                    pollCount++;
                    const elapsedMinutes = Math.floor((pollCount * pollInterval) / 60000);
                    
                    // Update status with elapsed time
                    statusDiv.textContent = `⏳ Génération en cours... (${elapsedMinutes} min) - Cela peut prendre plusieurs minutes, veuillez patienter.`;
                    
                    if (pollCount >= maxPolls) {
                        statusDiv.textContent = "⚠️ La génération prend plus de temps que prévu. Vérifiez la bibliothèque pour voir si la vidéo a été générée.";
                        statusDiv.style.color = "#ffc107";
                        generateBtn.disabled = false;
                        generateBtn.textContent = "🎬 Générer la vidéo";
                        return;
                    }
                    
                    // Try to check job status (if ModelsLab API supports it)
                    // For now, we'll just show progress and let the user know it's still processing
                    setTimeout(pollJobStatus, pollInterval);
                };
                
                // Start polling
                setTimeout(pollJobStatus, pollInterval);
                
                // Note: We can't actually poll ModelsLab API without their status endpoint
                // So we just show progress and let the user know it's processing
            } else {
                // Check if there's an error message in the response
                const errorMsg = result.error || result.message || "La génération a échoué";
                throw new Error(errorMsg);
            }
        } catch (err) {
            console.error("Error generating video", err);
            statusDiv.textContent = `❌ Erreur: ${err.message}`;
            statusDiv.style.color = "#dc3545";
            videoContainer.style.display = "none";
            if (progressInterval) clearInterval(progressInterval);
            generateBtn.disabled = false;
            generateBtn.textContent = "🎬 Générer la vidéo";
        } finally {
            // Only re-enable button if not in polling mode
            if (result && result.status === "processing") {
                // Keep button disabled during polling - it will be re-enabled when done
            } else if (!result || result.success) {
                // Re-enable button if we got a result (success or error, but not processing)
                if (progressInterval) clearInterval(progressInterval);
                if (!result || result.status !== "processing") {
                    generateBtn.disabled = false;
                    generateBtn.textContent = "🎬 Générer la vidéo";
                }
            }
        }
    });
}

// ============================================================
// NEW SIMPLIFIED ACTIONS (6 BUTTONS)
// ============================================================

// Modal open/close functionality
document.addEventListener('DOMContentLoaded', function() {
    // Close modal on background click
    document.addEventListener('click', function(e) {
        // Check if clicked element is a modal container
        const modal = e.target.closest('[id$="-modal"]');
        if (modal && e.target === modal && modal.id.endsWith('-modal')) {
            modal.style.display = 'none';
        }
    });
    
    // Close modal buttons
    document.querySelectorAll('.close-modal').forEach(btn => {
        btn.addEventListener('click', function() {
            const modalId = this.getAttribute('data-modal');
            const modal = document.getElementById(modalId);
            if (modal) {
                modal.style.display = 'none';
            }
        });
    });
    
    // Open modal buttons
    ['transcribe', 'translate', 'generate-audio', 'upload-video', 'upload-audio', 'upload-text'].forEach(action => {
        const button = document.getElementById(`${action}-button`);
        if (button) {
            button.addEventListener('click', function() {
                const modal = document.getElementById(`${action}-modal`);
                if (modal) {
                    modal.style.display = 'block';
                    // Load folders for upload modals
                    if (action.startsWith('upload-')) {
                        loadFoldersForModal(action);
                    }
                }
            });
        }
    });
    
    // Upload video thumbnail source toggle
    const uploadVideoThumbnailRadios = document.querySelectorAll('input[name="upload-video-thumbnail"]');
    uploadVideoThumbnailRadios.forEach(radio => {
        radio.addEventListener('change', function() {
            const frameSelector = document.getElementById('upload-video-frame-selector');
            const thumbnailUpload = document.getElementById('upload-video-thumbnail-upload');
            if (this.value === 'video_frame') {
                frameSelector.style.display = 'block';
                thumbnailUpload.style.display = 'none';
            } else {
                frameSelector.style.display = 'none';
                thumbnailUpload.style.display = 'block';
            }
        });
    });
});

// Load folders for upload modals
async function loadFoldersForModal(action) {
    const folderSelect = document.getElementById(`${action}-folder`);
    if (!folderSelect) return;
    
    try {
        const res = await fetch('/api/folders');
        if (res.ok) {
            const folders = await res.json();
            folderSelect.innerHTML = '<option value="">Aucun dossier (racine)</option>';
            folders.forEach(folder => {
                const option = document.createElement('option');
                option.value = folder.path;
                option.textContent = folder.path + (folder.is_private ? ' [PRIVÉ]' : '');
                folderSelect.appendChild(option);
            });
        }
    } catch (err) {
        console.error('Failed to load folders', err);
    }
}

// Transcribe form handler
const transcribeForm = document.getElementById('transcribe-form');
if (transcribeForm) {
    transcribeForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        
        const fileInput = document.getElementById('transcribe-file');
        const sourceLang = document.getElementById('transcribe-source-lang');
        const improveAI = document.getElementById('transcribe-improve-ai');
        const statusEl = document.getElementById('transcribe-status');
        
        if (!fileInput.files || fileInput.files.length === 0) {
            statusEl.innerHTML = '<div style="color: red;">Veuillez sélectionner un fichier.</div>';
            return;
        }
        
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        formData.append('source_language', sourceLang.value);
        formData.append('improve_with_ai', improveAI.checked);
        
        statusEl.innerHTML = '<div style="color: blue;">Transcription en cours...</div>';
        
        try {
            const res = await fetch('/api/transcribe', {
                method: 'POST',
                body: formData,
                credentials: 'include'
            });
            
            if (res.ok) {
                // Download the file
                const blob = await res.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = res.headers.get('Content-Disposition')?.split('filename=')[1]?.replace(/"/g, '') || 'transcription.txt';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                window.URL.revokeObjectURL(url);
                
                statusEl.innerHTML = '<div style="color: green;">✅ Transcription terminée et téléchargée!</div>';
                setTimeout(() => {
                    document.getElementById('transcribe-modal').style.display = 'none';
                    statusEl.innerHTML = '';
                }, 2000);
            } else {
                const error = await res.json();
                statusEl.innerHTML = `<div style="color: red;">❌ Erreur: ${error.error || 'Erreur inconnue'}</div>`;
            }
        } catch (err) {
            statusEl.innerHTML = `<div style="color: red;">❌ Erreur: ${err.message}</div>`;
        }
    });
}

// Translate form handler
const translateForm = document.getElementById('translate-form');
if (translateForm) {
    translateForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        
        const fileInput = document.getElementById('translate-file');
        const sourceLang = document.getElementById('translate-source-lang');
        const targetLang = document.getElementById('translate-target-lang');
        const statusEl = document.getElementById('translate-status');
        
        if (!fileInput.files || fileInput.files.length === 0) {
            statusEl.innerHTML = '<div style="color: red;">Veuillez sélectionner un fichier.</div>';
            return;
        }
        
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        formData.append('source_language', sourceLang.value);
        formData.append('target_language', targetLang.value);
        
        statusEl.innerHTML = '<div style="color: blue;">Traduction en cours...</div>';
        
        try {
            const res = await fetch('/api/translate-text', {
                method: 'POST',
                body: formData,
                credentials: 'include'
            });
            
            if (res.ok) {
                const blob = await res.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = res.headers.get('Content-Disposition')?.split('filename=')[1]?.replace(/"/g, '') || 'traduction.txt';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                window.URL.revokeObjectURL(url);
                
                statusEl.innerHTML = '<div style="color: green;">✅ Traduction terminée et téléchargée!</div>';
                setTimeout(() => {
                    document.getElementById('translate-modal').style.display = 'none';
                    statusEl.innerHTML = '';
                }, 2000);
            } else {
                const error = await res.json();
                statusEl.innerHTML = `<div style="color: red;">❌ Erreur: ${error.error || 'Erreur inconnue'}</div>`;
            }
        } catch (err) {
            statusEl.innerHTML = `<div style="color: red;">❌ Erreur: ${err.message}</div>`;
        }
    });
}

// Generate audio form handler
const generateAudioForm = document.getElementById('generate-audio-form');
if (generateAudioForm) {
    generateAudioForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        
        const fileInput = document.getElementById('generate-audio-file');
        const lang = document.getElementById('generate-audio-lang');
        const statusEl = document.getElementById('generate-audio-status');
        
        if (!fileInput.files || fileInput.files.length === 0) {
            statusEl.innerHTML = '<div style="color: red;">Veuillez sélectionner un fichier.</div>';
            return;
        }
        
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        formData.append('language', lang.value);
        
        statusEl.innerHTML = '<div style="color: blue;">Génération audio en cours...</div>';
        
        try {
            const res = await fetch('/api/generate-audio', {
                method: 'POST',
                body: formData,
                credentials: 'include'
            });
            
            if (res.ok) {
                const blob = await res.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = res.headers.get('Content-Disposition')?.split('filename=')[1]?.replace(/"/g, '') || 'audio.mp3';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                window.URL.revokeObjectURL(url);
                
                statusEl.innerHTML = '<div style="color: green;">✅ Audio généré et téléchargé!</div>';
                setTimeout(() => {
                    document.getElementById('generate-audio-modal').style.display = 'none';
                    statusEl.innerHTML = '';
                }, 2000);
            } else {
                const error = await res.json();
                statusEl.innerHTML = `<div style="color: red;">❌ Erreur: ${error.error || 'Erreur inconnue'}</div>`;
            }
        } catch (err) {
            statusEl.innerHTML = `<div style="color: red;">❌ Erreur: ${err.message}</div>`;
        }
    });
}

// Upload video to library form handler
const uploadVideoForm = document.getElementById('upload-video-form');
if (uploadVideoForm) {
    uploadVideoForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        
        const fileInput = document.getElementById('upload-video-file');
        const folder = document.getElementById('upload-video-folder');
        const sourceLang = document.getElementById('upload-video-source-lang');
        const thumbnailSource = document.querySelector('input[name="upload-video-thumbnail"]:checked');
        const thumbnailTime = document.getElementById('upload-video-thumbnail-time');
        const thumbnailFile = document.getElementById('upload-video-thumbnail-file');
        const statusEl = document.getElementById('upload-video-status');
        
        if (!fileInput.files || fileInput.files.length === 0) {
            statusEl.innerHTML = '<div style="color: red;">Veuillez sélectionner un fichier vidéo.</div>';
            return;
        }
        
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        if (folder.value) formData.append('folder_path', folder.value);
        formData.append('source_language', sourceLang.value);
        if (thumbnailSource) {
            formData.append('thumbnail_source', thumbnailSource.value);
            if (thumbnailSource.value === 'video_frame' && thumbnailTime.value) {
                formData.append('thumbnail_time', thumbnailTime.value);
            } else if (thumbnailSource.value === 'upload' && thumbnailFile.files[0]) {
                formData.append('thumbnail_file', thumbnailFile.files[0]);
            }
        }
        
        statusEl.innerHTML = '<div style="color: blue;">⏳ Upload en cours... (cela peut prendre du temps pour les grandes vidéos)</div>';
        
        // Disable form during upload
        const submitBtn = uploadVideoForm.querySelector('button[type="submit"]');
        const originalBtnText = submitBtn ? submitBtn.textContent : '';
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.textContent = '⏳ Upload en cours...';
        }
        
        try {
            // Create AbortController for timeout
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 600000); // 10 minutes timeout
            
            const res = await fetch('/api/upload-video-to-library', {
                method: 'POST',
                body: formData,
                credentials: 'include',
                signal: controller.signal
            });
            
            clearTimeout(timeoutId);
            
            let data;
            try {
                data = await res.json();
            } catch (e) {
                // If response is not JSON, try to get text
                const text = await res.text();
                throw new Error(`Réponse invalide du serveur: ${text || res.statusText}`);
            }
            
            if (res.ok) {
                statusEl.innerHTML = '<div style="color: green;">✅ Vidéo uploadée avec succès!</div>';
                console.log('Video uploaded successfully, ID:', data.id);
                
                // Reload library items immediately
                if (typeof fetchVideos === 'function') {
                    try {
                        await fetchVideos();
                        console.log('Library refreshed after video upload');
                    } catch (fetchErr) {
                        console.error('Error refreshing library:', fetchErr);
                        statusEl.innerHTML += '<div style="color: orange;">⚠️ Vidéo uploadée mais erreur lors du rafraîchissement. Veuillez recharger la page.</div>';
                    }
                }
                
                setTimeout(() => {
                    document.getElementById('upload-video-modal').style.display = 'none';
                    statusEl.innerHTML = '';
                    uploadVideoForm.reset();
                }, 2000);
            } else {
                let errorMsg = data.error || 'Erreur inconnue';
                console.error('Upload error:', res.status, errorMsg);
                
                // If it's a permission error, suggest re-authenticating
                if (res.status === 403 && (errorMsg.includes("éditeurs") || errorMsg.includes("editor"))) {
                    errorMsg += "\n\nVotre session a peut-être expiré. Veuillez recharger la page et vous reconnecter.";
                    const shouldReload = confirm(errorMsg + "\n\nVoulez-vous recharger la page maintenant?");
                    if (shouldReload) {
                        window.location.href = "/";
                        return;
                    }
                }
                statusEl.innerHTML = `<div style="color: red;">❌ Erreur: ${errorMsg}</div>`;
            }
        } catch (err) {
            console.error('Upload exception:', err);
            let errorMsg = err.message || 'Erreur inconnue';
            
            // Handle timeout
            if (err.name === 'AbortError') {
                errorMsg = 'Upload timeout: la vidéo est peut-être trop grande ou la connexion est trop lente. Veuillez réessayer avec une vidéo plus petite ou vérifier votre connexion.';
            } else if (err.message.includes('Failed to fetch') || err.message.includes('NetworkError')) {
                errorMsg = 'Erreur de connexion: impossible de contacter le serveur. Vérifiez votre connexion internet.';
            }
            
            statusEl.innerHTML = `<div style="color: red;">❌ Erreur: ${errorMsg}</div>`;
        } finally {
            // Re-enable form
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.textContent = originalBtnText;
            }
        }
    });
}

// Upload audio to library form handler
const uploadAudioForm = document.getElementById('upload-audio-form');
if (uploadAudioForm) {
    // Dynamisch extra taal/audio-rijen toevoegen (delegatie zodat het altijd werkt)
    document.addEventListener('click', (event) => {
        const addBtn = event.target.closest('#add-audio-track-btn');
        if (!addBtn) return;

        const container = document.getElementById('upload-audio-tracks');
        if (!container) return;
        
        const row = document.createElement('div');
        row.className = 'audio-track-row';
        row.style.display = 'flex';
        row.style.gap = '8px';
        row.style.marginBottom = '8px';
        row.style.alignItems = 'center';
        
        row.innerHTML = `
            <select class="audio-track-lang" style="flex: 0 0 40%; padding: 8px;">
                <option value="fr">Français</option>
                <option value="nl">Néerlandais</option>
                <option value="en">Anglais</option>
                <option value="es">Espagnol</option>
                <option value="pt-pt">Portugais (Angola/Portugal)</option>
                <option value="pt-br">Portugais (Brésil)</option>
                <option value="ln">Lingala</option>
                <option value="lua">Tshiluba</option>
                <option value="kg">Kikongo (Kituba)</option>
                <option value="mg">Malagasy</option>
                <option value="yo">Yoruba</option>
            </select>
            <input type="file" class="audio-track-file" accept="audio/*" style="flex: 1; padding: 8px;">
        `;
        
        container.appendChild(row);
    });

    uploadAudioForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        
        const titleInput = document.getElementById('upload-audio-title');
        const folder = document.getElementById('upload-audio-folder');
        const statusEl = document.getElementById('upload-audio-status');
        
        const title = titleInput ? titleInput.value.trim() : '';
        if (!title) {
            statusEl.innerHTML = '<div style="color: red;">Veuillez saisir un titre pour cet élément audio.</div>';
            return;
        }

        const rows = Array.from(document.querySelectorAll('#upload-audio-tracks .audio-track-row'));
        const files = [];
        const languages = [];
        rows.forEach(row => {
            const fileInput = row.querySelector('.audio-track-file');
            const langSelect = row.querySelector('.audio-track-lang');
            if (fileInput && fileInput.files && fileInput.files.length > 0) {
                files.push(fileInput.files[0]);
                languages.push(langSelect ? langSelect.value : 'fr');
            }
        });

        if (files.length === 0) {
            statusEl.innerHTML = '<div style="color: red;">Veuillez ajouter au moins un fichier audio.</div>';
            return;
        }
        
        const formData = new FormData();
        formData.append('title', title);
        if (folder.value) formData.append('folder_path', folder.value);
        files.forEach(file => formData.append('files', file));
        languages.forEach(lang => formData.append('languages', lang));
        
        statusEl.innerHTML = '<div style="color: blue;">Upload en cours...</div>';
        
        try {
            const res = await fetch('/api/upload-audio-to-library', {
                method: 'POST',
                body: formData,
                credentials: 'include'
            });
            
            const data = await res.json();
            
            if (res.ok) {
                statusEl.innerHTML = '<div style="color: green;">✅ Fichier audio uploadé avec succès!</div>';
                setTimeout(() => {
                    document.getElementById('upload-audio-modal').style.display = 'none';
                    statusEl.innerHTML = '';
                    uploadAudioForm.reset();
                    if (typeof fetchVideos === 'function') {
                        fetchVideos();
                    }
                }, 2000);
            } else {
                let errorMsg = data.error || 'Erreur inconnue';
                // If it's a permission error, suggest re-authenticating
                if (res.status === 403 && (errorMsg.includes("éditeurs") || errorMsg.includes("editor"))) {
                    errorMsg += "\n\nVotre session a peut-être expiré. Veuillez recharger la page et vous reconnecter.";
                    const shouldReload = confirm(errorMsg + "\n\nVoulez-vous recharger la page maintenant?");
                    if (shouldReload) {
                        window.location.href = "/";
                        return;
                    }
                }
                statusEl.innerHTML = `<div style="color: red;">❌ Erreur: ${errorMsg}</div>`;
            }
        } catch (err) {
            statusEl.innerHTML = `<div style="color: red;">❌ Erreur: ${err.message}</div>`;
        }
    });
}

// Upload text to library form handler
const uploadTextForm = document.getElementById('upload-text-form');
if (uploadTextForm) {
    uploadTextForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        
        const fileInput = document.getElementById('upload-text-file');
        const folder = document.getElementById('upload-text-folder');
        const sourceLang = document.getElementById('upload-text-source-lang');
        const statusEl = document.getElementById('upload-text-status');
        
        if (!fileInput.files || fileInput.files.length === 0) {
            statusEl.innerHTML = '<div style="color: red;">Veuillez sélectionner un fichier texte.</div>';
            return;
        }
        
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        if (folder.value) formData.append('folder_path', folder.value);
        formData.append('source_language', sourceLang.value);
        
        statusEl.innerHTML = '<div style="color: blue;">Upload en cours...</div>';
        
        try {
            const res = await fetch('/api/upload-text-to-library', {
                method: 'POST',
                body: formData,
                credentials: 'include'
            });
            
            const data = await res.json();
            
            if (res.ok) {
                statusEl.innerHTML = '<div style="color: green;">✅ Fichier texte uploadé avec succès!</div>';
                setTimeout(() => {
                    document.getElementById('upload-text-modal').style.display = 'none';
                    statusEl.innerHTML = '';
                    uploadTextForm.reset();
                    if (typeof fetchVideos === 'function') {
                        fetchVideos();
                    }
                }, 2000);
            } else {
                let errorMsg = data.error || 'Erreur inconnue';
                // If it's a permission error, suggest re-authenticating
                if (res.status === 403 && (errorMsg.includes("éditeurs") || errorMsg.includes("editor"))) {
                    errorMsg += "\n\nVotre session a peut-être expiré. Veuillez recharger la page et vous reconnecter.";
                    const shouldReload = confirm(errorMsg + "\n\nVoulez-vous recharger la page maintenant?");
                    if (shouldReload) {
                        window.location.href = "/";
                        return;
                    }
                }
                statusEl.innerHTML = `<div style="color: red;">❌ Erreur: ${errorMsg}</div>`;
            }
        } catch (err) {
            statusEl.innerHTML = `<div style="color: red;">❌ Erreur: ${err.message}</div>`;
        }
    });
}

// ============================================================
// NEW LIBRARY STRUCTURE (NETFLIX/SPOTIFY/WATTPAD STYLE)
// ============================================================

// Render videos in Netflix-style grid
function renderVideosGrid(videos) {
    const grid = document.getElementById("videos-grid");
    const empty = document.getElementById("videos-empty");
    
    if (!grid || !empty) return;
    
    grid.innerHTML = "";
    
    if (videos.length === 0) {
        empty.style.display = "block";
        grid.style.display = "none";
        return;
    }
    
    empty.style.display = "none";
    grid.style.display = "grid";
    
    videos.forEach(video => {
        const card = document.createElement("div");
        card.className = "video-card";
        card.style.cursor = "pointer";
        card.style.position = "relative";
        card.style.borderRadius = "8px";
        card.style.overflow = "hidden";
        card.style.transition = "transform 0.2s";
        card.style.boxShadow = "0 2px 8px rgba(0,0,0,0.1)";
        
        card.onmouseenter = () => {
            card.style.transform = "scale(1.05)";
            card.style.zIndex = "10";
        };
        card.onmouseleave = () => {
            card.style.transform = "scale(1)";
            card.style.zIndex = "1";
        };
        
        // Thumbnail
        const thumbnailContainer = document.createElement("div");
        thumbnailContainer.style.width = "100%";
        thumbnailContainer.style.height = "280px";
        thumbnailContainer.style.backgroundColor = "#000";
        thumbnailContainer.style.position = "relative";
        thumbnailContainer.style.overflow = "hidden";
        
        const thumbnailImg = document.createElement("img");
        thumbnailImg.src = `/videos/${video.id}/thumbnail?t=${Date.now()}`;
        thumbnailImg.alt = video.filename;
        thumbnailImg.style.width = "100%";
        thumbnailImg.style.height = "100%";
        thumbnailImg.style.objectFit = "cover";
        thumbnailImg.onerror = () => {
            thumbnailContainer.style.backgroundColor = "#333";
            thumbnailContainer.innerHTML = '<div style="display: flex; align-items: center; justify-content: center; height: 100%; color: white; font-size: 48px;">🎬</div>';
        };
        
        thumbnailContainer.appendChild(thumbnailImg);
        
        // Private badge
        if (video.is_private) {
            const privateBadge = document.createElement("div");
            privateBadge.textContent = "PRIVÉ";
            privateBadge.style.position = "absolute";
            privateBadge.style.top = "10px";
            privateBadge.style.right = "10px";
            privateBadge.style.backgroundColor = "#ffc107";
            privateBadge.style.color = "#000";
            privateBadge.style.padding = "4px 8px";
            privateBadge.style.borderRadius = "4px";
            privateBadge.style.fontSize = "12px";
            privateBadge.style.fontWeight = "bold";
            thumbnailContainer.appendChild(privateBadge);
        }
        
        card.appendChild(thumbnailContainer);
        
        // Title
        const titleDiv = document.createElement("div");
        titleDiv.style.padding = "12px";
        titleDiv.style.backgroundColor = "#fff";
        
        const title = document.createElement("div");
        title.textContent = video.filename;
        title.style.fontWeight = "bold";
        title.style.fontSize = "14px";
        title.style.overflow = "hidden";
        title.style.textOverflow = "ellipsis";
        title.style.whiteSpace = "nowrap";
        
        titleDiv.appendChild(title);
        card.appendChild(titleDiv);
        
        // Click to open detail modal
        card.onclick = () => openItemDetailModal(video);
        
        grid.appendChild(card);
    });
}

// Render audios in Spotify-style list
function renderAudiosList(audios) {
    const list = document.getElementById("audios-list");
    const empty = document.getElementById("audios-empty");
    
    if (!list || !empty) return;
    
    list.innerHTML = "";
    
    if (audios.length === 0) {
        empty.style.display = "block";
        list.style.display = "none";
        return;
    }
    
    empty.style.display = "none";
    list.style.display = "flex";
    
    audios.forEach(audio => {
        const item = document.createElement("div");
        item.className = "audio-item";
        item.style.display = "flex";
        item.style.alignItems = "center";
        item.style.padding = "12px 16px";
        item.style.border = "1px solid #ddd";
        item.style.borderRadius = "8px";
        item.style.cursor = "pointer";
        item.style.backgroundColor = "#fff";
        item.style.transition = "background-color 0.2s";
        
        item.onmouseenter = () => {
            item.style.backgroundColor = "#f5f5f5";
        };
        item.onmouseleave = () => {
            item.style.backgroundColor = "#fff";
        };
        
        // Icon
        const icon = document.createElement("div");
        icon.textContent = "🎵";
        icon.style.fontSize = "24px";
        icon.style.marginRight = "16px";
        icon.style.width = "40px";
        icon.style.textAlign = "center";
        item.appendChild(icon);
        
        // Title
        const title = document.createElement("div");
        title.style.flex = "1";
        title.style.fontWeight = "500";
        title.style.fontSize = "16px";
        
        const titleText = document.createElement("div");
        titleText.textContent = audio.filename;
        title.appendChild(titleText);
        
        if (audio.is_private) {
            const privateBadge = document.createElement("span");
            privateBadge.textContent = " [PRIVÉ]";
            privateBadge.style.color = "#ffc107";
            privateBadge.style.fontSize = "12px";
            titleText.appendChild(privateBadge);
        }
        
        item.appendChild(title);
        
        // Click to open detail modal
        item.onclick = () => openItemDetailModal(audio);
        
        list.appendChild(item);
    });
}

// Render texts in Wattpad-style list
function renderTextsList(texts) {
    const list = document.getElementById("texts-list");
    const empty = document.getElementById("texts-empty");
    
    if (!list || !empty) return;
    
    list.innerHTML = "";
    
    if (texts.length === 0) {
        empty.style.display = "block";
        list.style.display = "none";
        return;
    }
    
    empty.style.display = "none";
    list.style.display = "flex";
    
    texts.forEach(text => {
        const item = document.createElement("div");
        item.className = "text-item";
        item.style.display = "flex";
        item.style.alignItems = "center";
        item.style.padding = "16px";
        item.style.border = "1px solid #e0e0e0";
        item.style.borderRadius = "8px";
        item.style.cursor = "pointer";
        item.style.backgroundColor = "#fff";
        item.style.boxShadow = "0 1px 3px rgba(0,0,0,0.1)";
        item.style.transition = "box-shadow 0.2s";
        
        item.onmouseenter = () => {
            item.style.boxShadow = "0 2px 8px rgba(0,0,0,0.15)";
        };
        item.onmouseleave = () => {
            item.style.boxShadow = "0 1px 3px rgba(0,0,0,0.1)";
        };
        
        // Icon
        const icon = document.createElement("div");
        icon.textContent = "📄";
        icon.style.fontSize = "28px";
        icon.style.marginRight = "16px";
        icon.style.width = "48px";
        icon.style.textAlign = "center";
        item.appendChild(icon);
        
        // Title
        const title = document.createElement("div");
        title.style.flex = "1";
        
        const titleText = document.createElement("div");
        titleText.textContent = text.filename;
        titleText.style.fontWeight = "600";
        titleText.style.fontSize = "18px";
        titleText.style.marginBottom = "4px";
        title.appendChild(titleText);
        
        if (text.is_private) {
            const privateBadge = document.createElement("span");
            privateBadge.textContent = " [PRIVÉ]";
            privateBadge.style.color = "#ffc107";
            privateBadge.style.fontSize = "14px";
            titleText.appendChild(privateBadge);
        }
        
        // Additional info
        const info = document.createElement("div");
        info.style.fontSize = "14px";
        info.style.color = "#666";
        info.textContent = "Document texte";
        title.appendChild(info);
        
        item.appendChild(title);
        
        // Click to open detail modal
        item.onclick = () => openItemDetailModal(text);
        
        list.appendChild(item);
    });
}

// Tab switching functionality
function initLibraryTabs() {
    const tabs = document.querySelectorAll('.library-tab');
    const tabContents = document.querySelectorAll('.library-tab-content');
    
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const targetTab = tab.getAttribute('data-tab');
            
            // Update tab styles
            tabs.forEach(t => {
                t.classList.remove('active');
                t.style.borderBottomColor = 'transparent';
                t.style.color = '#666';
            });
            tab.classList.add('active');
            tab.style.borderBottomColor = '#007bff';
            tab.style.color = '#007bff';
            
            // Show/hide tab contents
            tabContents.forEach(content => {
                content.style.display = 'none';
            });
            const targetContent = document.getElementById(`${targetTab}-tab`);
            if (targetContent) {
                targetContent.style.display = 'block';
            }
        });
    });
}

// Open detail modal for an item
function openItemDetailModal(item) {
    const modal = document.getElementById("item-detail-modal");
    const content = document.getElementById("item-detail-content");
    
    if (!modal || !content) return;
    
    const fileType = item.file_type || "video";
    
    // Build content based on item type
    let html = `<h2 style="margin-top: 0;">${item.filename}</h2>`;
    
    if (fileType === "video") {
        html += renderVideoDetail(item);
    } else if (fileType === "audio") {
        html += renderAudioDetail(item);
    } else if (fileType === "text") {
        html += renderTextDetail(item);
    }
    
    content.innerHTML = html;
    modal.style.display = "block";
    
    // Close modal handlers
    const closeBtn = document.getElementById("close-detail-modal");
    if (closeBtn) {
        closeBtn.onclick = () => {
            modal.style.display = "none";
        };
    }
    
    modal.onclick = (e) => {
        if (e.target === modal) {
            modal.style.display = "none";
        }
    };
}

// Render video detail content
function renderVideoDetail(video) {
    let html = `<div style="margin-bottom: 20px;">`;
    
    // Thumbnail met play-knop overlay
    html += `<div style="position: relative; display: inline-block; margin-bottom: 20px; min-width: 300px; min-height: 200px; background: #000; border-radius: 8px; overflow: hidden;">`;
    html += `<img src="/videos/${video.id}/thumbnail?t=${Date.now()}" alt="${video.filename}" style="width: 100%; max-width: 100%; height: auto; border-radius: 8px; display: block; min-height: 200px; object-fit: cover;" onerror="this.style.display='none'; this.parentElement.style.background='#1a1a1a';">`;
    // Transparante play-knop over de thumbnail (altijd gecentreerd)
    html += `
        <button
            type="button"
            onclick="playVideoWithSubtitles('${video.id}')"
            style="
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                width: 80px;
                height: 80px;
                border-radius: 50%;
                border: 4px solid rgba(255, 255, 255, 0.9);
                background: rgba(0, 0, 0, 0.35);
                display: flex;
                align-items: center;
                justify-content: center;
                cursor: pointer;
                transition: background 0.15s ease, transform 0.15s ease;
                padding: 0;
                z-index: 10;
            "
            onmouseenter="this.style.background='rgba(0,0,0,0.55)'; this.style.transform='translate(-50%, -50%) scale(1.05)';"
            onmouseleave="this.style.background='rgba(0,0,0,0.35)'; this.style.transform='translate(-50%, -50%) scale(1)';"
        >
            <div style="
                width: 0;
                height: 0;
                border-top: 16px solid transparent;
                border-bottom: 16px solid transparent;
                border-left: 26px solid rgba(255,255,255,0.95);
                margin-left: 4px;
            "></div>
        </button>
    `;
    html += `</div>`;
    
    // Inline video player container (hidden until play is pressed)
    html += `
      <div id="detail-video-player-container" style="margin-top: 16px; display: none;">
        <video id="detail-video-player" controls style="width: 100%; border-radius: 8px; background: #000;">
          Votre navigateur ne supporte pas la balise vidéo.
        </video>
      </div>
    `;
    
    // Subtitle language selection
    html += `<div style="margin-bottom: 15px;">`;
    html += `<label style="display: block; margin-bottom: 5px; font-weight: bold;">Sous-titres (cocher pour activer):</label>`;
    html += `<div id="video-subtitle-checkboxes" style="display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 10px;">`;
    if (video.available_subtitles && video.available_subtitles.length > 0) {
        video.available_subtitles.forEach(lang => {
            const langLabels = {
                'en': 'Anglais', 'nl': 'Néerlandais', 'fr': 'Français', 'es': 'Espagnol',
                'sv': 'Suédois', 'fi': 'Finnois', 'pt-br': 'Portugais (Brésil)', 'pt-pt': 'Portugais (Angola/Portugal)',
                'ln': 'Lingala', 'lua': 'Tshiluba', 'kg': 'Kikongo (Kituba)', 'mg': 'Malagasy', 'yo': 'Yoruba'
            };
            html += `<label style="display: flex; align-items: center; cursor: pointer;"><input type="checkbox" value="${lang}" class="subtitle-lang-checkbox" style="margin-right: 5px;">${langLabels[lang] || lang.toUpperCase()}</label>`;
        });
    } else {
        html += `<p style="color: #666; font-size: 0.9em;">Aucune sous-titre disponible. Utilisez "Générer sous-titres" pour en créer.</p>`;
    }
    html += `</div>`;
    html += `</div>`;
    
    // Editor controls if applicable
    if (isEditor) {
        html += `<button onclick="showGenerateSubtitles('${video.id}')" style="padding: 12px 24px; background: #9c27b0; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; margin-right: 10px;">📝 Générer sous-titres</button>`;
        if (video.available_subtitles && video.available_subtitles.length > 0) {
            // Use a closure to avoid JSON.stringify issues
            html += `<button onclick="(function() { const videoData = ${JSON.stringify(video)}; showEditSubtitles('${video.id}', videoData); })()" style="padding: 12px 24px; background: #ff9800; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; margin-right: 10px;">✏️ Éditer sous-titres</button>`;
        }
        html += `<button onclick="renameVideo('${video.id}'); document.getElementById('item-detail-modal').style.display='none';" style="padding: 12px 24px; background: #6c757d; color: white; border: none; border-radius: 5px; cursor: pointer; margin-right: 10px;">✏️ Renommer</button>`;
        html += `<button onclick="if(confirm('Êtes-vous sûr de vouloir supprimer ce fichier?')) { deleteVideo('${video.id}'); document.getElementById('item-detail-modal').style.display='none'; }" style="padding: 12px 24px; background: #dc3545; color: white; border: none; border-radius: 5px; cursor: pointer;">🗑️ Supprimer</button>`;
    }
    
    html += `</div>`;
    
    return html;
}

// Render audio detail content
function renderAudioDetail(audio) {
    // Get source language from audio info
    const sourceLang = audio.source_language || 'fr';
    
    let html = `<div style="margin-bottom: 20px;">`;
    html += `<p><strong>Fichier audio:</strong> ${audio.filename}</p>`;
    
    // Language selection for playback/download
    html += `<div style="margin-bottom: 15px;">`;
    html += `<label style="display: block; margin-bottom: 5px; font-weight: bold;">Langue pour écouter/télécharger:</label>`;
    html += `<select id="audio-language-select" onchange="updateAudioLanguage('${audio.id}')" style="width: 100%; padding: 8px; margin-bottom: 10px;">`;
    html += `<option value="${sourceLang}">Original (${sourceLang.toUpperCase()})</option>`;
    // Add translated versions if they exist
    if (audio.available_translations && audio.available_translations.length > 0) {
        audio.available_translations.forEach(lang => {
            const langLabels = {
                'en': 'Anglais', 'nl': 'Néerlandais', 'fr': 'Français', 'es': 'Espagnol',
                'sv': 'Suédois', 'fi': 'Finnois', 'pt-br': 'Portugais (Brésil)', 'pt-pt': 'Portugais (Angola/Portugal)',
                'ln': 'Lingala', 'lua': 'Tshiluba', 'kg': 'Kikongo (Kituba)', 'mg': 'Malagasy', 'yo': 'Yoruba'
            };
            html += `<option value="${lang}">${langLabels[lang] || lang.toUpperCase()}</option>`;
        });
    }
    html += `</select>`;
    html += `</div>`;
    
    // Audio player container (hidden until play is pressed)
    html += `
      <div id="audio-player-container" style="margin-top: 16px; margin-bottom: 16px; display: none;">
        <audio id="audio-player" controls style="width: 100%;">
          Votre navigateur ne supporte pas la balise audio.
        </audio>
      </div>
    `;
    
    // Listen button - use a wrapper function to ensure we get the current language value
    html += `<button onclick="(function() { const select = document.getElementById('audio-language-select'); const lang = select ? select.value : '${sourceLang}'; playAudio('${audio.id}', lang); })()" style="padding: 12px 24px; background: #007bff; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; margin-right: 10px;">▶️ Écouter</button>`;
    
    // Download button
    html += `<button onclick="downloadAudio('${audio.id}', document.getElementById('audio-language-select').value)" style="padding: 12px 24px; background: #28a745; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; margin-right: 10px;">📥 Télécharger</button>`;
    
    if (isEditor) {
        // Add translation button
        html += `<button onclick="showAddAudioTranslation('${audio.id}', '${sourceLang}')" style="padding: 12px 24px; background: #9c27b0; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; margin-right: 10px;">🌍 Ajouter traduction</button>`;
        html += `<button onclick="renameVideo('${audio.id}'); document.getElementById('item-detail-modal').style.display='none';" style="padding: 12px 24px; background: #6c757d; color: white; border: none; border-radius: 5px; cursor: pointer; margin-right: 10px;">✏️ Renommer</button>`;
        html += `<button onclick="if(confirm('Êtes-vous sûr de vouloir supprimer ce fichier?')) { deleteVideo('${audio.id}'); document.getElementById('item-detail-modal').style.display='none'; }" style="padding: 12px 24px; background: #dc3545; color: white; border: none; border-radius: 5px; cursor: pointer;">🗑️ Supprimer</button>`;
    }
    
    html += `</div>`;
    return html;
}

// Render text detail content
function renderTextDetail(text) {
    let html = `<div style="margin-bottom: 20px;">`;
    html += `<p><strong>Document texte:</strong> ${text.filename}</p>`;
    
    // Read button - opens text editor
    html += `<button onclick="readText('${text.id}')" style="padding: 12px 24px; background: #007bff; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; margin-right: 10px;">📖 Lire</button>`;
    
    // Download button
    html += `<button onclick="downloadText('${text.id}')" style="padding: 12px 24px; background: #28a745; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; margin-right: 10px;">📥 Télécharger</button>`;
    
    if (isEditor) {
        // Edit button
        html += `<button onclick="editText('${text.id}')" style="padding: 12px 24px; background: #ff9800; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; margin-right: 10px;">✏️ Éditer</button>`;
        html += `<button onclick="renameVideo('${text.id}'); document.getElementById('item-detail-modal').style.display='none';" style="padding: 12px 24px; background: #6c757d; color: white; border: none; border-radius: 5px; cursor: pointer; margin-right: 10px;">✏️ Renommer</button>`;
        html += `<button onclick="if(confirm('Êtes-vous sûr de vouloir supprimer ce fichier?')) { deleteVideo('${text.id}'); document.getElementById('item-detail-modal').style.display='none'; }" style="padding: 12px 24px; background: #dc3545; color: white; border: none; border-radius: 5px; cursor: pointer;">🗑️ Supprimer</button>`;
    }
    
    html += `</div>`;
    return html;
}

// Initialize tabs and load library when DOM is ready
// ============================================================
// HELPER FUNCTIONS FOR DETAIL MODALS
// ============================================================

// Audio functions
async function playAudio(audioId, lang) {
    console.log("playAudio called", { audioId, lang });
    
    // Wait a bit to ensure DOM is ready
    await new Promise(resolve => setTimeout(resolve, 200));
    
    const container = document.getElementById('audio-player-container');
    const player = document.getElementById('audio-player');
    
    if (!container || !player) {
        console.error("Audio player container not found", {
            containerExists: !!container,
            playerExists: !!player,
            modalContent: document.getElementById('item-detail-content')?.innerHTML?.substring(0, 500),
        });
        alert("Erreur: lecteur audio non trouvé. Veuillez réessayer.");
        return;
    }
    
    console.log("Audio player elements found, showing container");
    // Show audio player
    container.style.display = 'block';
    
    // Update audio source (async)
    try {
        const audioUrl = await updateAudioSource(audioId, lang, player);
        console.log("Audio source updated, waiting for load...");
        
        // Wait for audio to load with better error handling
        await new Promise((resolve, reject) => {
            const timeout = setTimeout(() => {
                console.error("Timeout waiting for audio to load");
                reject(new Error('Timeout: le fichier audio prend trop de temps à charger. Vérifiez votre connexion.'));
            }, 15000);
            
            const cleanup = () => {
                clearTimeout(timeout);
                player.removeEventListener('canplay', onCanPlay);
                player.removeEventListener('error', onError);
                player.removeEventListener('loadeddata', onLoadedData);
            };
            
            const onCanPlay = () => {
                console.log("Audio can play");
                cleanup();
                resolve();
            };
            
            const onLoadedData = () => {
                console.log("Audio data loaded");
                cleanup();
                resolve();
            };
            
            const onError = (e) => {
                console.error("Audio load error:", player.error);
                cleanup();
                const errorCode = player.error?.code;
                let errorMsg = 'Erreur inconnue lors du chargement audio';
                if (errorCode === 4) {
                    errorMsg = 'Fichier audio non trouvé (404). Vérifiez que le fichier existe.';
                } else if (errorCode === 3) {
                    errorMsg = 'Format audio non supporté ou fichier corrompu.';
                } else if (errorCode === 2) {
                    errorMsg = 'Erreur réseau lors du chargement audio.';
                }
                reject(new Error(errorMsg));
            };
            
            player.addEventListener('canplay', onCanPlay, { once: true });
            player.addEventListener('loadeddata', onLoadedData, { once: true });
            player.addEventListener('error', onError, { once: true });
            
            // If already loaded
            if (player.readyState >= 2) {
                console.log("Audio already loaded, readyState:", player.readyState);
                cleanup();
                resolve();
            }
        });
        
        console.log("Audio loaded, starting playback...");
        // Start playback
        await player.play();
        console.log("Audio playback started successfully");
    } catch (err) {
        console.error("Error starting audio playback:", err);
        const errorMsg = err.message || 'Fichier audio non trouvé ou format non supporté';
        alert(`Erreur lors de la lecture audio: ${errorMsg}`);
    }
}

async function updateAudioLanguage(audioId) {
    const select = document.getElementById('audio-language-select');
    const player = document.getElementById('audio-player');
    const container = document.getElementById('audio-player-container');
    
    if (!select || !player) {
        return;
    }
    
    const lang = select.value;
    
    // If player is visible, update the source
    if (container && container.style.display !== 'none') {
        try {
            await updateAudioSource(audioId, lang, player);
            // Restart playback with new source
            player.load();
            await player.play();
        } catch (err) {
            console.error("Error updating audio playback:", err);
        }
    }
}

async function updateAudioSource(audioId, lang, player) {
    // Build audio URL
    let audioUrl;
    if (lang && lang !== 'original' && lang !== 'fr' && lang !== 'nl' && lang !== 'en') {
        // Translated version - always .mp3
        audioUrl = `/files/${encodeURIComponent(audioId)}/audio_${encodeURIComponent(lang)}.mp3`;
    } else {
        // Original - try to find the actual file extension
        // First try common audio formats
        const audioExtensions = ['.mp3', '.wav', '.m4a', '.ogg', '.aac', '.flac'];
        let foundUrl = null;
        
        // Try each extension
        for (const ext of audioExtensions) {
            const testUrl = `/files/${encodeURIComponent(audioId)}/original${ext}`;
            try {
                const response = await fetch(testUrl, { method: 'HEAD' });
                if (response.ok) {
                    foundUrl = testUrl;
                    console.log(`Found audio file: ${testUrl}`);
                    break;
                }
            } catch (e) {
                // Continue to next extension
                console.log(`Tried ${testUrl}, not found: ${e.message}`);
            }
        }
        
        if (foundUrl) {
            audioUrl = foundUrl;
        } else {
            // Fallback to .mp3
            audioUrl = `/files/${encodeURIComponent(audioId)}/original.mp3`;
            console.log(`Using fallback URL: ${audioUrl}`);
        }
    }
    
    // Set audio source
    console.log("Setting audio source to:", audioUrl);
    player.src = audioUrl;
    
    // Load the audio
    player.load();
    
    // Return the URL for debugging
    return audioUrl;
}

function downloadAudio(audioId, lang) {
    const link = document.createElement('a');
    if (lang && lang !== 'original') {
        // Download translated version
        link.href = `/files/${encodeURIComponent(audioId)}/audio_${lang}.mp3`;
    } else {
        // Download original
        link.href = `/files/${encodeURIComponent(audioId)}/original.mp3`;
    }
    link.download = '';
    link.click();
}

function showAddAudioTranslation(audioId, sourceLang) {
    const modal = document.getElementById('item-detail-modal');
    const content = document.getElementById('item-detail-content');
    
    let html = `<h2 style="margin-top: 0;">Ajouter une traduction audio</h2>`;
    html += `<p>Téléversez une version audio existante dans une autre langue pour: ${audioId}</p>`;
    html += `<div style="margin-bottom: 15px;">`;
    html += `<label style="display: block; margin-bottom: 5px; font-weight: bold;">Langue cible:</label>`;
    html += `<select id="audio-translation-target-lang" style="width: 100%; padding: 8px; margin-bottom: 10px;">`;
    const languages = [
        {code: 'en', label: 'Anglais'}, {code: 'nl', label: 'Néerlandais'}, {code: 'fr', label: 'Français'},
        {code: 'es', label: 'Espagnol'}, {code: 'sv', label: 'Suédois'}, {code: 'fi', label: 'Finnois'},
        {code: 'pt-br', label: 'Portugais (Brésil)'}, {code: 'pt-pt', label: 'Portugais (Angola/Portugal)'},
        {code: 'ln', label: 'Lingala'}, {code: 'lua', label: 'Tshiluba'}, {code: 'kg', label: 'Kikongo (Kituba)'},
        {code: 'mg', label: 'Malagasy'}, {code: 'yo', label: 'Yoruba'}
    ];
    languages.forEach(lang => {
        if (lang.code !== sourceLang) {
            html += `<option value="${lang.code}">${lang.label}</option>`;
        }
    });
    html += `</select>`;
    html += `</div>`;
    html += `<div style="margin-bottom: 15px;">`;
    html += `<label style="display: block; margin-bottom: 5px; font-weight: bold;">Fichier audio (traduction):</label>`;
    html += `<input type="file" id="audio-translation-file" accept="audio/*" style="width: 100%; padding: 6px; background: #111827; color: #e5e7eb; border-radius: 4px; border: 1px solid #4b5563;">`;
    html += `</div>`;
    html += `<div style="display: flex; gap: 10px;">`;
    html += `<button onclick="addAudioTranslation('${audioId}', '${sourceLang}')" style="flex: 1; padding: 12px 24px; background: #9c27b0; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold;">Téléverser</button>`;
    html += `<button onclick="openItemDetailModal(${JSON.stringify({id: audioId, file_type: 'audio', filename: 'Audio'})})" style="flex: 1; padding: 12px 24px; background: #6c757d; color: white; border: none; border-radius: 5px; cursor: pointer;">Annuler</button>`;
    html += `</div>`;
    html += `<div id="audio-translation-status" style="margin-top: 15px;"></div>`;
    
    content.innerHTML = html;
}

async function addAudioTranslation(audioId, sourceLang) {
    const targetLang = document.getElementById('audio-translation-target-lang').value;
    const fileInput = document.getElementById('audio-translation-file');
    const statusEl = document.getElementById('audio-translation-status');

    if (!fileInput.files || fileInput.files.length === 0) {
        alert("Veuillez sélectionner un fichier audio à téléverser.");
        return;
    }

    const formData = new FormData();
    formData.append('audio_id', audioId);
    formData.append('target_language', targetLang);
    formData.append('file', fileInput.files[0]);

    statusEl.innerHTML = '<div style="color: blue;">Téléversement de la traduction audio en cours...</div>';

    try {
        const res = await fetch('/api/audio/upload-translation', {
            method: 'POST',
            body: formData,
            credentials: 'include'
        });

        const data = await res.json();

        if (res.ok) {
            statusEl.innerHTML = '<div style="color: green;">✅ Traduction audio ajoutée avec succès!</div>';
            setTimeout(() => {
                if (typeof fetchVideos === 'function') {
                    fetchVideos();
                }
                document.getElementById('item-detail-modal').style.display = 'none';
            }, 2000);
        } else {
            statusEl.innerHTML = `<div style="color: red;">❌ Erreur: ${data.error || 'Erreur inconnue'}</div>`;
        }
    } catch (err) {
        statusEl.innerHTML = `<div style="color: red;">❌ Erreur: ${err.message}</div>`;
    }
}

// Video functions
async function playVideoWithSubtitles(videoId) {
    console.log("playVideoWithSubtitles called", { videoId });
    const checkboxes = document.querySelectorAll('.subtitle-lang-checkbox:checked');
    const selectedLangs = Array.from(checkboxes).map(cb => cb.value);
    
    // Inline video player inside the detail modal (do not close the modal)
    const container = document.getElementById('detail-video-player-container');
    const player = document.getElementById('detail-video-player');
    if (!container || !player) {
        console.error("Inline video player container not found", {
            containerExists: !!container,
            playerExists: !!player,
        });
        return;
    }

    container.style.display = 'block';

    // Set video source (original video)
    const baseUrl = `/videos/${encodeURIComponent(videoId)}/original`;
    // Remove existing tracks
    Array.from(player.querySelectorAll('track')).forEach(t => t.remove());
    // Reset source to force reload
    player.src = baseUrl;

    // If no languages selected, just play the video without subtitles
    if (selectedLangs.length === 0) {
        console.log("Playing video without subtitles");
        // Start playback
        player.play().catch(err => {
            console.error("Error starting inline video playback:", err);
        });
        return;
    }

    // Create or get subtitle overlay for custom rendering
    let subtitleOverlay = container.querySelector('.subtitle-overlay-inline');
    if (!subtitleOverlay) {
        subtitleOverlay = document.createElement('div');
        subtitleOverlay.className = 'subtitle-overlay-inline';
        subtitleOverlay.style.cssText = 'position: absolute; left: 50%; bottom: 12%; transform: translateX(-50%); display: flex; flex-direction: column; align-items: center; gap: 6px; max-width: 90%; text-align: center; pointer-events: none; z-index: 1000;';
        container.style.position = 'relative';
        container.appendChild(subtitleOverlay);
    }
    subtitleOverlay.innerHTML = '';

    // Load subtitles for all selected languages
    try {
        const cache = "caches" in window ? await caches.open("video-cache") : null;
        
        const subtitles = await Promise.all(
            selectedLangs.map(async (lang) => {
                const subUrl = `/videos/${encodeURIComponent(videoId)}/subs/${encodeURIComponent(lang)}`;
                
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

        // Update subtitles on timeupdate
        const updateSubtitles = () => {
            const currentTime = player.currentTime;
            subtitleOverlay.innerHTML = "";

            subtitles.forEach(({ lang, cues }) => {
                const cue = cues.find(
                    (item) => currentTime >= item.start && currentTime <= item.end
                );
                if (cue) {
                    const line = document.createElement("div");
                    line.className = "subtitle-line";
                    line.style.cssText = 'background: rgba(0, 0, 0, 0.75); color: #fff; padding: 6px 10px; border-radius: 6px; font-size: 18px; line-height: 1.4; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.4); margin-bottom: 4px;';
                    
                    const label = document.createElement("span");
                    label.className = "subtitle-lang";
                    label.style.cssText = 'display: block; font-size: 12px; text-transform: uppercase; color: #ffd24c; margin-bottom: 2px;';
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

                    subtitleOverlay.appendChild(line);
                }
            });

            subtitleOverlay.style.display = subtitleOverlay.children.length === 0 ? 'none' : 'flex';
        };

        // Store update function so we can reattach it after fullscreen changes
        const attachSubtitleListeners = () => {
            player.addEventListener("timeupdate", updateSubtitles);
            player.addEventListener("seeked", updateSubtitles);
            player.addEventListener("ended", () => {
                subtitleOverlay.innerHTML = '';
                subtitleOverlay.style.display = 'none';
            });
        };
        
        attachSubtitleListeners();
        
        // Handle fullscreen changes for inline player
        const handleInlineFullscreenChange = () => {
            const fullscreenElement = document.fullscreenElement;
            if (fullscreenElement === container || fullscreenElement === player) {
                // In fullscreen, ensure overlay is visible and properly positioned
                subtitleOverlay.style.zIndex = '2147483647';
                subtitleOverlay.style.position = 'fixed';
                subtitleOverlay.style.left = '50%';
                subtitleOverlay.style.bottom = '8%';
                subtitleOverlay.style.transform = 'translateX(-50%)';
                subtitleOverlay.style.fontSize = 'clamp(18px, 2.8vw, 34px)';
                subtitleOverlay.style.maxWidth = '80%';
                // Force update to show current subtitles
                updateSubtitles();
            } else {
                // Not in fullscreen, restore normal positioning
                subtitleOverlay.style.position = 'absolute';
                subtitleOverlay.style.left = '50%';
                subtitleOverlay.style.bottom = '12%';
                subtitleOverlay.style.transform = 'translateX(-50%)';
                subtitleOverlay.style.fontSize = '18px';
                subtitleOverlay.style.maxWidth = '90%';
                subtitleOverlay.style.zIndex = '1000';
            }
        };
        
        document.addEventListener("fullscreenchange", handleInlineFullscreenChange);
        
        // Cleanup function
        const cleanup = () => {
            document.removeEventListener("fullscreenchange", handleInlineFullscreenChange);
            player.removeEventListener("timeupdate", updateSubtitles);
            player.removeEventListener("seeked", updateSubtitles);
            subtitleOverlay.innerHTML = '';
            subtitleOverlay.style.display = 'none';
        };
        
        player.addEventListener("ended", cleanup);

    } catch (err) {
        console.error("Error loading subtitles:", err);
        alert(`Erreur lors du chargement des sous-titres: ${err.message}`);
    }

    // Start playback
    player.play().catch(err => {
        console.error("Error starting inline video playback:", err);
    });
}

function showGenerateSubtitles(videoId) {
    const modal = document.getElementById('item-detail-modal');
    const content = document.getElementById('item-detail-content');
    
    let html = `<h2 style="margin-top: 0;">Générer des sous-titres</h2>`;
    html += `<p>Sélectionnez les langues pour lesquelles vous voulez générer des sous-titres:</p>`;
    html += `<div style="margin-bottom: 15px;">`;
    html += `<div id="subtitle-lang-checkboxes" style="display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 10px;">`;
    const languages = [
        {code: 'en', label: 'Anglais'}, {code: 'nl', label: 'Néerlandais'}, {code: 'fr', label: 'Français'},
        {code: 'es', label: 'Espagnol'}, {code: 'sv', label: 'Suédois'}, {code: 'fi', label: 'Finnois'},
        {code: 'pt-br', label: 'Portugais (Brésil)'}, {code: 'pt-pt', label: 'Portugais (Angola/Portugal)'},
        {code: 'ln', label: 'Lingala'}, {code: 'lua', label: 'Tshiluba'}, {code: 'kg', label: 'Kikongo (Kituba)'},
        {code: 'mg', label: 'Malagasy'}, {code: 'yo', label: 'Yoruba'}
    ];
    languages.forEach(lang => {
        html += `<label style="display: flex; align-items: center; cursor: pointer;"><input type="checkbox" value="${lang.code}" class="generate-subtitle-lang" style="margin-right: 5px;">${lang.label}</label>`;
    });
    html += `</div>`;
    html += `</div>`;
    html += `<div style="display: flex; gap: 10px;">`;
    html += `<button onclick="generateSubtitles('${videoId}')" style="flex: 1; padding: 12px 24px; background: #9c27b0; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold;">Générer</button>`;
    html += `<button onclick="openItemDetailModal(${JSON.stringify({id: videoId, file_type: 'video', filename: 'Video'})})" style="flex: 1; padding: 12px 24px; background: #6c757d; color: white; border: none; border-radius: 5px; cursor: pointer;">Annuler</button>`;
    html += `</div>`;
    html += `<div id="subtitle-generation-status" style="margin-top: 15px;"></div>`;
    
    content.innerHTML = html;
}

async function generateSubtitles(videoId) {
    const checkboxes = document.querySelectorAll('.generate-subtitle-lang:checked');
    const selectedLangs = Array.from(checkboxes).map(cb => cb.value);
    
    if (selectedLangs.length === 0) {
        alert('Veuillez sélectionner au moins une langue');
        return;
    }
    
    const statusEl = document.getElementById('subtitle-generation-status');
    statusEl.innerHTML = '<div style="color: blue;">Génération des sous-titres en cours...</div>';
    
    try {
        const res = await fetch('/api/videos/generate-subtitles', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                video_id: videoId,
                languages: selectedLangs
            }),
            credentials: 'include'
        });
        
        const data = await res.json();
        
        if (res.ok) {
            statusEl.innerHTML = '<div style="color: green;">✅ Sous-titres générés avec succès!</div>';
            setTimeout(() => {
                if (typeof fetchVideos === 'function') {
                    fetchVideos();
                }
                document.getElementById('item-detail-modal').style.display = 'none';
            }, 2000);
        } else {
            statusEl.innerHTML = `<div style="color: red;">❌ Erreur: ${data.error || 'Erreur inconnue'}</div>`;
        }
    } catch (err) {
        statusEl.innerHTML = `<div style="color: red;">❌ Erreur: ${err.message}</div>`;
    }
}

function showEditSubtitles(videoId, video) {
    // Use existing editSubtitle function if available
    if (typeof editSubtitle === 'function') {
        // Show language selection if multiple languages available
        if (video && video.available_subtitles && video.available_subtitles.length > 0) {
            if (video.available_subtitles.length === 1) {
                // Only one language, edit directly
                editSubtitle(videoId, video.available_subtitles[0]);
                document.getElementById('item-detail-modal').style.display = 'none';
            } else {
                // Multiple languages, show selection
                const modal = document.getElementById('item-detail-modal');
                const content = document.getElementById('item-detail-content');
                
                let html = `<h2 style="margin-top: 0;">Éditer les sous-titres</h2>`;
                html += `<p>Sélectionnez la langue à éditer:</p>`;
                html += `<div style="margin-bottom: 15px;">`;
                video.available_subtitles.forEach(lang => {
                    const langLabels = {
                        'en': 'Anglais', 'nl': 'Néerlandais', 'fr': 'Français', 'es': 'Espagnol',
                        'sv': 'Suédois', 'fi': 'Finnois', 'pt-br': 'Portugais (Brésil)', 'pt-pt': 'Portugais (Angola/Portugal)',
                        'ln': 'Lingala', 'lua': 'Tshiluba', 'kg': 'Kikongo (Kituba)', 'mg': 'Malagasy', 'yo': 'Yoruba'
                    };
                    html += `<button onclick="editSubtitle('${videoId}', '${lang}'); document.getElementById('item-detail-modal').style.display='none';" style="display: block; width: 100%; padding: 12px; margin-bottom: 10px; background: #ff9800; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold;">${langLabels[lang] || lang.toUpperCase()}</button>`;
                });
                html += `</div>`;
                // Store video data in a way that can be retrieved without JSON issues
                html += `<button onclick="(function() { const videoData = ${JSON.stringify(video)}; openItemDetailModal(videoData); })()" style="padding: 12px 24px; background: #6c757d; color: white; border: none; border-radius: 5px; cursor: pointer;">Retour</button>`;
                
                content.innerHTML = html;
            }
        } else {
            alert('Aucune sous-titre disponible pour cette vidéo');
        }
    } else {
        alert('Fonction d\'édition de sous-titres non disponible');
    }
}

// Text functions
function readText(textId) {
    // Open text in a new modal or page
    window.open(`/files/${encodeURIComponent(textId)}/original.txt`, '_blank');
}

function downloadText(textId) {
    const link = document.createElement('a');
    link.href = `/files/${encodeURIComponent(textId)}/original.txt`;
    link.download = '';
    link.click();
}

function editText(textId) {
    const modal = document.getElementById('item-detail-modal');
    const content = document.getElementById('item-detail-content');
    
    // Load text content
    fetch(`/files/${encodeURIComponent(textId)}/original.txt`)
        .then(res => res.text())
        .then(textContent => {
            let html = `<h2 style="margin-top: 0;">Éditer le texte</h2>`;
            html += `<div style="margin-bottom: 15px;">`;
            html += `<textarea id="text-edit-content" style="width: 100%; height: 400px; padding: 10px; font-family: monospace; border: 1px solid #ddd; border-radius: 5px;">${textContent.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</textarea>`;
            html += `</div>`;
            html += `<div style="display: flex; gap: 10px;">`;
            html += `<button onclick="saveText('${textId}')" style="flex: 1; padding: 12px 24px; background: #28a745; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold;">Enregistrer</button>`;
            html += `<button onclick="document.getElementById('item-detail-modal').style.display='none'" style="flex: 1; padding: 12px 24px; background: #6c757d; color: white; border: none; border-radius: 5px; cursor: pointer;">Annuler</button>`;
            html += `</div>`;
            html += `<div id="text-save-status" style="margin-top: 15px;"></div>`;
            
            content.innerHTML = html;
        })
        .catch(err => {
            alert('Erreur lors du chargement du texte: ' + err.message);
        });
}

async function saveText(textId) {
    const content = document.getElementById('text-edit-content').value;
    const statusEl = document.getElementById('text-save-status');
    
    statusEl.innerHTML = '<div style="color: blue;">Enregistrement en cours...</div>';
    
    try {
        const res = await fetch('/api/texts/save', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                text_id: textId,
                content: content
            }),
            credentials: 'include'
        });
        
        const data = await res.json();
        
        if (res.ok) {
            statusEl.innerHTML = '<div style="color: green;">✅ Texte enregistré avec succès!</div>';
            setTimeout(() => {
                document.getElementById('item-detail-modal').style.display = 'none';
            }, 1500);
        } else {
            statusEl.innerHTML = `<div style="color: red;">❌ Erreur: ${data.error || 'Erreur inconnue'}</div>`;
        }
    } catch (err) {
        statusEl.innerHTML = `<div style="color: red;">❌ Erreur: ${err.message}</div>`;
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        initLibraryTabs();
        // Load library items when page loads
        if (typeof fetchVideos === 'function') {
            fetchVideos();
        }
    });
} else {
    initLibraryTabs();
    // Load library items when page loads
    if (typeof fetchVideos === 'function') {
        fetchVideos();
    }
}

