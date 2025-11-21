import asyncio
import json
import logging
import shutil
import tempfile
import uuid
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from .auth import get_role_from_request, is_editor, create_session

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import settings
from .services import (
    ensure_dirs,
    extract_audio,
    transcribe_audio_whisper,
    build_sentence_pairs,
    build_sentence_segments,
    pair_translation_segments,
    translate_segments,
    generate_vtt,
    render_vtt_content,
    generate_dub_audio,
    replace_video_audio,
    save_metadata,
    load_metadata,
    get_audio_stream_start_offset,
)
from .audio_text_services import (
    transcribe_long_audio,
    improve_text_with_ai,
    translate_text,
    generate_long_tts_audio,
)

from .languages import (
    get_language_options,
    LANGUAGES_WITHOUT_DUBBING,
    LANGUAGE_LABELS,
)
from .models import VideoListItem, VideoMetadata, TranslationSegment
from .job_store import job_store, JobStatus

app = FastAPI()
ensure_dirs()

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
LANGUAGE_OPTIONS = get_language_options()
ALLOWED_LANGUAGE_CODES = {opt.code for opt in LANGUAGE_OPTIONS}
PROCESS_OPTIONS = {
    # Video processing options
    "subs",
    "dub_audio",
    "dub_video",
    # Audio/Text processing options
    "transcribe",
    "improve_text",
    "translate",
    "generate_audio",
}
DEFAULT_PROCESS_OPTIONS = ["subs", "dub_audio", "dub_video"]  # Default for videos

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")

logger = logging.getLogger(__name__)

def _load_video_metadata(video_dir: Path) -> Optional[VideoMetadata]:
    meta_path = video_dir / "metadata.json"
    if not meta_path.exists():
        return None
    try:
        return load_metadata(meta_path)
    except Exception:
        logger.exception("Failed to load metadata for video %s", video_dir.name)
        return None


def _find_video_directory(video_id: str) -> Optional[Path]:
    """Find video directory by ID, searching recursively through folders."""
    def _search(directory: Path) -> Optional[Path]:
        for item in directory.iterdir():
            if item.is_dir():
                meta = _load_video_metadata(item)
                if meta and meta.id == video_id:
                    return item
                # Recursively search subdirectories
                found = _search(item)
                if found:
                    return found
        return None
    return _search(settings.PROCESSED_DIR)

def _find_original_video(video_dir: Path) -> Optional[Path]:
    for candidate in video_dir.iterdir():
        if candidate.name.startswith("original"):
            return candidate
    return None

def _build_combined_segments(
    translations: Dict[str, List[TranslationSegment]],
    languages: List[str],
) -> List[TranslationSegment]:
    """Combineer twee vertaalde subtitle-sporen tot gedeelde segmenten."""

    if len(languages) < 2:
        raise ValueError("At least two languages are required to build a combined subtitle track")

    cleaned_langs: List[str] = []
    segments_by_lang: List[Tuple[str, List[TranslationSegment]]] = []

    for lang in languages:
        normalized = lang.lower().strip()
        if not normalized or normalized in cleaned_langs:
            continue
        if normalized not in translations:
            raise ValueError(f"Subtitles for '{normalized}' are not available")
        cleaned_langs.append(normalized)
        paired_segments = pair_translation_segments(translations[normalized])
        segments_by_lang.append((normalized, paired_segments))


    if len(cleaned_langs) < 2:
        raise ValueError("Subtitles for two different languages are required")

    max_len = max((len(segs) for _, segs in segments_by_lang), default=0)
    combined: List[TranslationSegment] = []

    for idx in range(max_len):
        start: Optional[float] = None
        end: Optional[float] = None
        text_lines: List[str] = []

        for lang_code, segs in segments_by_lang:
            if idx >= len(segs):
                continue
            seg = segs[idx]
            start = seg.start if start is None else min(start, seg.start)
            end = seg.end if end is None else max(end, seg.end)
            clean_text = " ".join(seg.text.replace("\r", " ").split())
            text_lines.append(f"{lang_code.upper()}: {clean_text}")

        if not text_lines or start is None or end is None:
            continue

        combined.append(
            TranslationSegment(
                start=start,
                end=end,
                text="\n".join(text_lines),
                language="+".join(cleaned_langs),
            )
        )

    return combined

def _combined_subtitle_filename(languages: List[str]) -> str:
    safe = "_".join(lang.lower().strip() for lang in languages if lang.strip())
    return f"subs_combined_{safe}.vtt"


def _combined_subtitle_key(path: Path) -> Optional[str]:
    stem = path.stem
    prefix = "subs_combined_"
    if not stem.startswith(prefix):
        return None
    suffix = stem[len(prefix) :]
    if not suffix:
        return None
    parts = [part for part in suffix.split("_") if part]
    if len(parts) < 2:
        return None
    return "+".join(parts)



# ---------- Frontend ----------

@app.get("/")
async def index(request: Request):
    """Redirect to role selection if no session, otherwise show main page."""
    role = get_role_from_request(request)
    if not role:
        return RedirectResponse(url="/select-role", status_code=302)
    
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "available_languages": LANGUAGE_OPTIONS,
            "is_editor": is_editor(request),
        },
    )

@app.get("/select-role")
async def select_role(request: Request):
    """Role selection page."""
    return templates.TemplateResponse("select_role.html", {"request": request})

@app.post("/api/set-role")
async def set_role(request: Request, role: str = Form(...)):
    """Set the user's role and create a session."""
    if role not in ("viewer", "editor"):
        return JSONResponse({"error": "Rôle invalide"}, status_code=400)
    
    session_id = create_session(role)
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(key="session_id", value=session_id, httponly=True, max_age=86400*30)  # 30 days
    return response


# ---------- API: lijst video's ----------

def _load_video_info(video_dir: Path) -> dict:
    """Load folder and privacy info for a video."""
    info_path = video_dir / "info.json"
    if not info_path.exists():
        return {"folder_path": None, "is_private": False}
    try:
        return json.loads(info_path.read_text(encoding="utf-8"))
    except Exception:
        return {"folder_path": None, "is_private": False}

def _is_folder_private(folder_path: Optional[str]) -> bool:
    """Check if a folder (or any of its parent folders) is private."""
    if not folder_path:
        return False
    
    # Check each level of the path
    parts = folder_path.split("/")
    current_path = ""
    
    for part in parts:
        if current_path:
            current_path = current_path + "/" + part
        else:
            current_path = part
        
        folder_dir = settings.PROCESSED_DIR / current_path
        info_path = folder_dir / ".folder_info.json"
        
        if info_path.exists():
            try:
                info_data = json.loads(info_path.read_text(encoding="utf-8"))
                if info_data.get("is_private", False):
                    return True
            except Exception:
                pass
    
    return False

def _get_relative_path(video_dir: Path) -> Optional[str]:
    """Get relative path from PROCESSED_DIR."""
    try:
        return str(video_dir.relative_to(settings.PROCESSED_DIR).parent)
    except ValueError:
        return None

@app.get("/api/videos", response_model=List[VideoListItem])
async def list_videos(request: Request):
    """List all videos, filtering by privacy based on user role."""
    items: List[VideoListItem] = []
    user_is_editor = is_editor(request)

    def _scan_directory(directory: Path, folder_path: Optional[str] = None):
        """Recursively scan directory for videos, audio, and text files."""
        for item in directory.iterdir():
            if item.is_dir():
                # Check if this is a video directory (has metadata.json)
                meta = _load_video_metadata(item)
                info = _load_video_info(item)
                
                if meta is not None:
                    # This is a video directory
                    is_private = info.get("is_private", False)
                    
                    # Get folder path - use info.json first, then calculate from directory structure
                    video_folder_path = info.get("folder_path")
                    if not video_folder_path and folder_path:
                        video_folder_path = folder_path
                    elif not video_folder_path:
                        # Calculate from directory structure
                        try:
                            rel_path = item.parent.relative_to(settings.PROCESSED_DIR)
                            if str(rel_path) != ".":
                                video_folder_path = str(rel_path).replace("\\", "/")
                        except ValueError:
                            pass
                    
                    # Check if video itself is private OR if it's in a private folder
                    video_is_private = is_private or _is_folder_private(video_folder_path)
                    
                    # Filter: viewers can't see private videos or videos in private folders
                    if not user_is_editor and video_is_private:
                        continue
                    
                    subtitles = [
                        lang
                        for lang in meta.translations.keys()
                        if (item / f"subs_{lang}.vtt").exists()
                    ]

                    dubs = [
                        lang
                        for lang in meta.translations.keys()
                        if (item / f"video_dub_{lang}.mp4").exists()
                    ]

                    dub_audios = [
                        lang
                        for lang in meta.translations.keys()
                        if (item / f"dub_audio_{lang}.mp3").exists()
                    ]

                    combined = []
                    for combined_file in item.glob("subs_combined_*.vtt"):
                        key = _combined_subtitle_key(combined_file)
                        if key:
                            combined.append(key)

                    subtitles.sort()
                    dubs.sort()
                    dub_audios.sort()
                    combined.sort()
                    
                    items.append(
                        VideoListItem(
                            id=meta.id,
                            filename=meta.filename,
                            file_type="video",
                            available_subtitles=subtitles,
                            available_dubs=dubs,
                            available_dub_audios=dub_audios,
                            available_combined_subtitles=combined,
                            folder_path=video_folder_path,
                            is_private=video_is_private,
                        )
                    )
                elif (item / "info.json").exists():
                    # Check if this is an audio or text file directory (has info.json and original file)
                    file_type = info.get("file_type", "video")
                    if file_type in ["audio", "text"]:
                        # Check if original file exists
                        original_exists = any((item / f"original{ext}").exists() for ext in [".mp3", ".wav", ".m4a", ".txt"])
                        if original_exists:
                            is_private = info.get("is_private", False)
                            
                            # Get folder path
                            file_folder_path = info.get("folder_path")
                            if not file_folder_path and folder_path:
                                file_folder_path = folder_path
                            elif not file_folder_path:
                                try:
                                    rel_path = item.parent.relative_to(settings.PROCESSED_DIR)
                                    if str(rel_path) != ".":
                                        file_folder_path = str(rel_path).replace("\\", "/")
                                except ValueError:
                                    pass
                            
                            # Check privacy
                            file_is_private = is_private or _is_folder_private(file_folder_path)
                            
                            # Filter: viewers can't see private files
                            if not user_is_editor and file_is_private:
                                continue
                            
                            # Get original filename from directory
                            original_file = None
                            for ext in [".mp3", ".wav", ".m4a", ".txt"]:
                                candidate = item / f"original{ext}"
                                if candidate.exists():
                                    original_file = candidate
                                    break
                            
                            if original_file:
                                filename = original_file.name
                                # Use directory name as ID (same as video)
                                file_id = item.name
                                
                                items.append(
                                    VideoListItem(
                                        id=file_id,
                                        filename=filename,
                                        file_type=file_type,
                                        available_subtitles=[],
                                        available_dubs=[],
                                        available_dub_audios=[],
                                        available_combined_subtitles=[],
                                        folder_path=file_folder_path,
                                        is_private=file_is_private,
                                    )
                                )
                else:
                    # This might be a folder, scan recursively
                    current_folder = folder_path + "/" + item.name if folder_path else item.name
                    _scan_directory(item, current_folder)

    _scan_directory(settings.PROCESSED_DIR)
    return items


# ---------- API: upload + verwerken ----------

@app.post("/api/upload")
async def upload_video(
    request: Request,
    file_type: str = Form("video"),  # video, audio, or text
    languages: Optional[List[str]] = Form(None),  # max. 2 talen aanvinken in frontend
    process_options: Optional[List[str]] = Form(None),
    tts_speed_multiplier: float = Form(1.0),
    folder_path: Optional[str] = Form(None),  # Optional folder path
    is_private: bool = Form(False),  # Make video private
    source_language: Optional[str] = Form("fr"),  # Source language for audio/text
    file: UploadFile = File(...)
):
    # Only editors can upload
    if not is_editor(request):
        return JSONResponse(
            {"error": "Seuls les éditeurs peuvent télécharger des fichiers."},
            status_code=403,
        )
    
    file_type = file_type.lower()
    if file_type not in ["video", "audio", "text"]:
        return JSONResponse(
            {"error": "Type de fichier invalide. Utilisez 'video', 'audio' ou 'text'."},
            status_code=400,
        )
    
    # For audio/text files, handle differently
    if file_type in ["audio", "text"]:
        return await handle_audio_text_upload(
            request, file_type, file, languages, process_options,
            folder_path, is_private, source_language
        )
    
    # Original video processing logic
    # For videos, languages are always required
    if not languages or len(languages) == 0:
        return JSONResponse(
            {"error": "Please select one or two target languages"},
            status_code=400,
        )
    
    normalized_langs = [lang.lower() for lang in languages]

    if len(normalized_langs) == 0 or len(normalized_langs) > 2:
        return JSONResponse(
            {"error": "Please select one or two target languages"},
            status_code=400,
        )
    invalid = [lang for lang in normalized_langs if lang not in ALLOWED_LANGUAGE_CODES]
    if invalid:
        return JSONResponse(
            {
                "error": (
                    "Unsupported language codes requested: "
                    + ", ".join(sorted(set(invalid)))
                )
            },
            status_code=400,
        )

    if process_options is None:
        normalized_options = list(DEFAULT_PROCESS_OPTIONS)
    else:
        normalized_options = []
        for option in process_options:
            cleaned = option.lower().strip()
            if not cleaned or cleaned in normalized_options:
                continue
            normalized_options.append(cleaned)

        if not normalized_options:
            return JSONResponse(
                {"error": "Please select at least one processing option"},
                status_code=400,
            )

    invalid_options = [opt for opt in normalized_options if opt not in PROCESS_OPTIONS]
    if invalid_options:
        return JSONResponse(
            {
                "error": (
                    "Unsupported processing options requested: "
                    + ", ".join(sorted(set(invalid_options)))
                )
            },
            status_code=400,
        )

    if tts_speed_multiplier <= 0.5 or tts_speed_multiplier > 1.5:
        return JSONResponse(
            {"error": "The TTS speed multiplier must be between 0.5 and 1.5."},
            status_code=400,
        )
        
    video_id = str(uuid.uuid4())
    ext = Path(file.filename).suffix
    
    # Handle folder structure
    if folder_path:
        # Sanitize folder path
        folder_path = folder_path.strip().strip("/").strip("\\")
        if folder_path:
            # Create folder structure
            folder_dir = settings.PROCESSED_DIR / folder_path
            folder_dir.mkdir(parents=True, exist_ok=True)
            video_dir = folder_dir / video_id
        else:
            video_dir = settings.PROCESSED_DIR / video_id
    else:
        video_dir = settings.PROCESSED_DIR / video_id
    
    video_dir.mkdir(parents=True, exist_ok=True)

    video_path = video_dir / f"original{ext}"
    audio_path = video_dir / "audio.wav"
    meta_path = video_dir / "metadata.json"

    try:
        with open(video_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
    except Exception:
        logger.exception("Failed to store uploaded file")
        return JSONResponse(
            {"error": "Storing the uploaded video failed."}, status_code=500
        )

    job_store.create_job(video_id, file.filename)

    asyncio.create_task(
        process_video_job(
            video_id=video_id,
            video_dir=video_dir,
            video_path=video_path,
            audio_path=audio_path,
            meta_path=meta_path,
            languages=list(normalized_langs),
            original_filename=file.filename,
            process_options=normalized_options,
            tts_speed_multiplier=tts_speed_multiplier,
            folder_path=folder_path,
            is_private=is_private,
        )
    )

    return {"id": video_id, "status": JobStatus.PENDING}


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    job = job_store.get_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return job


async def process_video_job(
    *,
    video_id: str,
    video_dir: Path,
    video_path: Path,
    audio_path: Path,
    meta_path: Path,
    languages: List[str],
    original_filename: str,
    process_options: List[str],
    tts_speed_multiplier: float,
    folder_path: Optional[str] = None,
    is_private: bool = False,
):
    warnings: List[str] = []
    options_set = set(process_options or DEFAULT_PROCESS_OPTIONS)
    create_subtitles = "subs" in options_set or "subs_per_language" in options_set
    create_combined = False  # No longer used - removed combined subtitles option
    create_dub_audio = "dub_audio" in options_set
    create_dub_video = "dub_video" in options_set
    needs_dub_assets = create_dub_audio or create_dub_video
    job_store.mark_processing(video_id)

    audio_offset = 0.0
    try:
        audio_offset = await run_in_threadpool(
            get_audio_stream_start_offset, video_path
        )
    except Exception:
        logger.warning(
            "Kon audio-offset niet bepalen voor %s, ga verder met 0", video_id
        )

    
    try:
        await run_in_threadpool(extract_audio, video_path, audio_path)

        whisper_result = await run_in_threadpool(transcribe_audio_whisper, audio_path)
        original_lang = whisper_result.get("language", "unknown")

        sentence_segments = build_sentence_segments(
            whisper_result, base_offset=audio_offset
        )
        sentence_pairs = build_sentence_pairs(
            whisper_result, base_offset=audio_offset
        )
        translations, translation_warnings = await run_in_threadpool(
            translate_segments, sentence_segments, languages

        )
        warnings.extend(translation_warnings)

        if not translations:
            raise RuntimeError("Translations failed for all requested languages.")

        if create_subtitles:
            for lang, segs in translations.items():
                vtt_path = video_dir / f"subs_{lang}.vtt"
                paired_segments = pair_translation_segments(segs)
                await run_in_threadpool(generate_vtt, paired_segments, vtt_path)

        # Combined subtitles option removed - no longer needed

        if needs_dub_assets:
            for lang, segs in translations.items():
                if lang in LANGUAGES_WITHOUT_DUBBING:
                    label = LANGUAGE_LABELS.get(lang, lang.upper())
                    warnings.append(
                        f"Dubbing is voorlopig niet beschikbaar voor {label}."
                    )
                    continue
                temp_audio_path: Optional[Path] = None
                try:
                    if create_dub_audio:
                        audio_path_target = video_dir / f"dub_audio_{lang}.mp3"
                    else:
                        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
                        temp_audio_path = Path(tmp.name)
                        audio_path_target = temp_audio_path

                    await generate_dub_audio(
                        segs,
                        lang,
                        audio_path_target,
                        speed_multiplier=tts_speed_multiplier,
                        leading_silence=audio_offset,
                    )

                    if create_dub_video:
                        dub_video_path = video_dir / f"video_dub_{lang}.mp4"
                        await run_in_threadpool(
                            replace_video_audio,
                            video_path,
                            audio_path_target,
                            dub_video_path,
                        )
                except NotImplementedError:
                    pass
                except RuntimeError as exc:
                    warnings.append(
                        f"The dub for {lang} could not be created: {exc}"
                    )
                except Exception:
                    logger.exception("Unexpected error while creating dub for %s", lang)
                    warnings.append(
                        f"The dub for {lang} could not be created because of an unexpected error."
                    )
                finally:
                    if (not create_dub_audio) and temp_audio_path and temp_audio_path.exists():
                        temp_audio_path.unlink()
            
                    
        # 7. metadata opslaan (with folder and privacy info)
        meta = VideoMetadata(
            id=video_id,
            filename=original_filename,
            original_language=original_lang,
            sentence_pairs=sentence_pairs,
            translations=translations,
        )
        await run_in_threadpool(save_metadata, meta, meta_path)
        
        # Save folder and privacy info in a separate file
        info_path = video_dir / "info.json"
        info_data = {
            "folder_path": folder_path if folder_path else None,
            "is_private": is_private,
        }
        info_path.write_text(json.dumps(info_data, indent=2), encoding="utf-8")
        job_store.mark_completed(
            video_id,
            warnings=warnings,
            original_language=original_lang,
        )

    except RuntimeError as exc:
        logger.warning("Error while processing job %s: %s", video_id, exc)
        job_store.mark_failed(
            video_id,
            str(exc),
            warnings=warnings,
        )
    except Exception:
        logger.exception("Unexpected error while processing job %s", video_id)
        job_store.mark_failed(
            video_id,
            "Something went wrong while processing the video.",
            warnings=warnings,
        )


# ---------- video + ondertitels / dub leveren ----------
def _video_base_stem(meta: Optional[VideoMetadata], fallback: Path) -> str:
    if meta:
        return Path(meta.filename).stem
    return fallback.stem

@app.get("/videos/{video_id}/original")
async def get_original_video(request: Request, video_id: str):
    video_dir = _find_video_directory(video_id)
    if not video_dir or not video_dir.exists():
        return JSONResponse({"error": "Vidéo non trouvée"}, status_code=404)
    
    # Check privacy (video itself or parent folder)
    info = _load_video_info(video_dir)
    video_is_private = info.get("is_private", False) or _is_folder_private(info.get("folder_path"))
    if video_is_private and not is_editor(request):
        return JSONResponse({"error": "Accès refusé"}, status_code=403)

    original_path = _find_original_video(video_dir)
    if original_path is None:
        return JSONResponse({"error": "Vidéo originale non trouvée"}, status_code=404)

    meta = _load_video_metadata(video_dir)
    filename = meta.filename if meta else original_path.name
    return FileResponse(original_path, filename=filename)


@app.get("/videos/{video_id}/dub/{lang}")
async def get_dubbed_video(request: Request, video_id: str, lang: str):
    video_dir = _find_video_directory(video_id)
    if not video_dir or not video_dir.exists():
        return JSONResponse({"error": "Vidéo non trouvée"}, status_code=404)
    
    # Check privacy (video itself or parent folder)
    info = _load_video_info(video_dir)
    video_is_private = info.get("is_private", False) or _is_folder_private(info.get("folder_path"))
    if video_is_private and not is_editor(request):
        return JSONResponse({"error": "Accès refusé"}, status_code=403)

    dub_path = video_dir / f"video_dub_{lang}.mp4"
    if not dub_path.exists():
        return JSONResponse({"error": "Vidéo doublée non trouvée"}, status_code=404)

    meta = _load_video_metadata(video_dir)
    original_path = _find_original_video(video_dir)
    base_stem = _video_base_stem(meta, dub_path if original_path is None else original_path)
    filename = f"{base_stem}_dub_{lang}.mp4"
    return FileResponse(dub_path, filename=filename)       

@app.get("/videos/{video_id}/dub-audio/{lang}")
async def get_dub_audio(request: Request, video_id: str, lang: str):
    video_dir = _find_video_directory(video_id)
    if not video_dir or not video_dir.exists():
        return JSONResponse({"error": "Vidéo non trouvée"}, status_code=404)
    
    # Check privacy (video itself or parent folder)
    info = _load_video_info(video_dir)
    video_is_private = info.get("is_private", False) or _is_folder_private(info.get("folder_path"))
    if video_is_private and not is_editor(request):
        return JSONResponse({"error": "Accès refusé"}, status_code=403)

    audio_path = video_dir / f"dub_audio_{lang}.mp3"
    if not audio_path.exists():
        return JSONResponse({"error": "Audio de doublage non trouvé"}, status_code=404)

    meta = _load_video_metadata(video_dir)
    original_path = _find_original_video(video_dir)
    base_stem = _video_base_stem(meta, audio_path if original_path is None else original_path)
    filename = f"{base_stem}_dub_{lang}.mp3"
    return FileResponse(audio_path, media_type="audio/mpeg", filename=filename)

@app.get("/videos/{video_id}/subs/{lang}")
async def get_subtitles(request: Request, video_id: str, lang: str):
    video_dir = _find_video_directory(video_id)
    if not video_dir or not video_dir.exists():
        return JSONResponse({"error": "Vidéo non trouvée"}, status_code=404)
    
    # Check privacy (video itself or parent folder)
    info = _load_video_info(video_dir)
    video_is_private = info.get("is_private", False) or _is_folder_private(info.get("folder_path"))
    if video_is_private and not is_editor(request):
        return JSONResponse({"error": "Accès refusé"}, status_code=403)

    subs_path = video_dir / f"subs_{lang}.vtt"
    if not subs_path.exists():
        return JSONResponse({"error": "Sous-titres non trouvés"}, status_code=404)

    meta = _load_video_metadata(video_dir)
    original_path = _find_original_video(video_dir)
    base_stem = _video_base_stem(meta, subs_path if original_path is None else original_path)
    filename = f"{base_stem}_{lang}.vtt"
    return FileResponse(subs_path, media_type="text/vtt", filename=filename)
    
@app.get("/videos/{video_id}/subs/combined")
async def get_combined_subtitles(request: Request, video_id: str, langs: Optional[str] = None):
    video_dir = _find_video_directory(video_id)
    if not video_dir or not video_dir.exists():
        return JSONResponse({"error": "Vidéo non trouvée"}, status_code=404)
    
    # Check privacy (video itself or parent folder)
    info = _load_video_info(video_dir)
    video_is_private = info.get("is_private", False) or _is_folder_private(info.get("folder_path"))
    if video_is_private and not is_editor(request):
        return JSONResponse({"error": "Accès refusé"}, status_code=403)

    meta = _load_video_metadata(video_dir)
    if meta is None or not meta.translations:
        return JSONResponse({"error": "No subtitles available"}, status_code=404)

    if langs:
        requested = [part.strip().lower() for part in langs.split(",") if part.strip()]
    else:
        requested = list(meta.translations.keys())[:2]

    deduped: List[str] = []
    for code in requested:
        if code not in deduped:
            deduped.append(code)

    if len(deduped) < 2:
        return JSONResponse(
            {"error": "Please provide two subtitle languages for a combined download"},
            status_code=400,
        )

    if len(deduped) > 2:
        return JSONResponse(
            {"error": "Only two subtitle languages can be combined"},
            status_code=400,
        )

    missing = [code for code in deduped if code not in meta.translations]
    if missing:
        return JSONResponse(
            {"error": f"Subtitles not found for: {', '.join(missing)}"},
            status_code=404,
        )

    stored_path = video_dir / _combined_subtitle_filename(deduped)
    base_stem = Path(meta.filename).stem if meta.filename else video_dir.name
    suffix = "_".join(deduped)
    filename = f"{base_stem}_{suffix}_combined.vtt"

    if stored_path.exists():
        return FileResponse(stored_path, media_type="text/vtt", filename=filename)
        
    try:
        combined_segments = _build_combined_segments(meta.translations, deduped)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    if not combined_segments:
        return JSONResponse({"error": "No subtitle segments available"}, status_code=404)

    content = render_vtt_content(combined_segments)

    return PlainTextResponse(
        content,
        media_type="text/vtt",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------- Folder management (editors only) ----------

@app.post("/api/folders")
async def create_folder(request: Request, folder_path: str = Form(...), is_private: bool = Form(False), color: Optional[str] = Form(None)):
    """Create a new folder."""
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent créer des dossiers."}, status_code=403)
    
    folder_path = folder_path.strip().strip("/").strip("\\")
    if not folder_path:
        return JSONResponse({"error": "Le chemin du dossier ne peut pas être vide."}, status_code=400)
    
    # Validate color (hex color code)
    folder_color = "#f0f0f0"  # default gray
    if color:
        color = color.strip()
        if color.startswith("#") and len(color) == 7:
            try:
                int(color[1:], 16)  # Validate hex
                folder_color = color
            except ValueError:
                pass
    
    folder_dir = settings.PROCESSED_DIR / folder_path
    try:
        folder_dir.mkdir(parents=True, exist_ok=True)
        # Save privacy and color info
        info_path = folder_dir / ".folder_info.json"
        info_data = {"is_private": is_private, "color": folder_color}
        info_path.write_text(json.dumps(info_data, indent=2), encoding="utf-8")
        return JSONResponse({"message": "Dossier créé avec succès.", "path": folder_path})
    except Exception as e:
        logger.exception("Failed to create folder")
        return JSONResponse({"error": f"Impossible de créer le dossier: {e}"}, status_code=500)

@app.get("/api/folders")
async def list_folders(request: Request):
    """List all folders."""
    user_is_editor = is_editor(request)
    folders = []
    
    def _scan_folders(directory: Path, parent_path: Optional[str] = None):
        for item in directory.iterdir():
            if item.is_dir():
                # Check if it's a folder (not a video directory)
                meta = _load_video_metadata(item)
                if meta is None:
                    # This is a folder (check if it has .folder_info.json or is empty)
                    folder_name = item.name
                    current_path = parent_path + "/" + folder_name if parent_path else folder_name
                    
                    # Check privacy and color
                    info_path = item / ".folder_info.json"
                    is_private = False
                    folder_color = "#f0f0f0"  # default gray
                    if info_path.exists():
                        try:
                            info_data = json.loads(info_path.read_text(encoding="utf-8"))
                            is_private = info_data.get("is_private", False)
                            folder_color = info_data.get("color", "#f0f0f0")
                        except Exception:
                            pass
                    
                    # Check if parent folder is private
                    parent_is_private = _is_folder_private(parent_path)
                    
                    # Filter: viewers can't see private folders or folders in private parent folders
                    if not user_is_editor and (is_private or parent_is_private):
                        _scan_folders(item, current_path)  # Still scan subfolders (but don't show them)
                        continue
                    
                    folders.append({
                        "name": folder_name,
                        "path": current_path,
                        "is_private": is_private,
                        "color": folder_color,
                        "parent_path": parent_path,
                    })
                    
                    # Recursively scan subfolders
                    _scan_folders(item, current_path)
                else:
                    # This is a video directory, check if parent is a folder
                    # (video directories are not folders themselves)
                    pass
    
    _scan_folders(settings.PROCESSED_DIR)
    return JSONResponse(folders)

@app.put("/api/folders/{folder_path:path}/privacy")
async def toggle_folder_privacy(request: Request, folder_path: str, is_private: bool = Form(...)):
    """Toggle folder privacy."""
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent modifier la confidentialité des dossiers."}, status_code=403)
    
    folder_dir = settings.PROCESSED_DIR / folder_path
    if not folder_dir.exists() or not folder_dir.is_dir():
        return JSONResponse({"error": "Dossier non trouvé."}, status_code=404)
    
    try:
        info_path = folder_dir / ".folder_info.json"
        # Preserve existing color
        existing_color = "#f0f0f0"
        if info_path.exists():
            try:
                existing_data = json.loads(info_path.read_text(encoding="utf-8"))
                existing_color = existing_data.get("color", "#f0f0f0")
            except Exception:
                pass
        info_data = {"is_private": is_private, "color": existing_color}
        info_path.write_text(json.dumps(info_data, indent=2), encoding="utf-8")
        return JSONResponse({"message": "Confidentialité du dossier mise à jour."})
    except Exception as e:
        logger.exception("Failed to update folder privacy")
        return JSONResponse({"error": f"Impossible de mettre à jour la confidentialité: {e}"}, status_code=500)

@app.put("/api/folders/{folder_path:path}/color")
async def update_folder_color(request: Request, folder_path: str, color: str = Form(...)):
    """Update folder color."""
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent modifier la couleur des dossiers."}, status_code=403)
    
    folder_dir = settings.PROCESSED_DIR / folder_path
    if not folder_dir.exists() or not folder_dir.is_dir():
        return JSONResponse({"error": "Dossier non trouvé."}, status_code=404)
    
    # Validate color
    folder_color = "#f0f0f0"
    if color:
        color = color.strip()
        if color.startswith("#") and len(color) == 7:
            try:
                int(color[1:], 16)  # Validate hex
                folder_color = color
            except ValueError:
                return JSONResponse({"error": "Couleur invalide. Utilisez un code hexadécimal (ex: #ffc107)."}, status_code=400)
    
    try:
        info_path = folder_dir / ".folder_info.json"
        # Preserve existing privacy
        is_private = False
        if info_path.exists():
            try:
                existing_data = json.loads(info_path.read_text(encoding="utf-8"))
                is_private = existing_data.get("is_private", False)
            except Exception:
                pass
        info_data = {"is_private": is_private, "color": folder_color}
        info_path.write_text(json.dumps(info_data, indent=2), encoding="utf-8")
        return JSONResponse({"message": "Couleur du dossier mise à jour."})
    except Exception as e:
        logger.exception("Failed to update folder color")
        return JSONResponse({"error": f"Impossible de mettre à jour la couleur: {e}"}, status_code=500)

@app.post("/api/folders/{folder_path:path}/upload")
async def upload_file_to_folder(
    request: Request,
    folder_path: str,
    file: UploadFile = File(...)
):
    """Upload a file (video, audio, or text) directly to a folder without processing."""
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent télécharger des fichiers."}, status_code=403)
    
    folder_dir = settings.PROCESSED_DIR / folder_path
    if not folder_dir.exists() or not folder_dir.is_dir():
        return JSONResponse({"error": "Dossier non trouvé."}, status_code=404)
    
    try:
        # Save file directly to folder
        file_path = folder_dir / file.filename
        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        return JSONResponse({"message": f"Fichier téléchargé avec succès dans {folder_path}.", "filename": file.filename})
    except Exception as e:
        logger.exception("Failed to upload file to folder")
        return JSONResponse({"error": f"Impossible de télécharger le fichier: {e}"}, status_code=500)


# ============================================================
# AUDIO AND TEXT PROCESSING
# ============================================================
async def handle_audio_text_upload(
    request: Request,
    file_type: str,
    file: UploadFile,
    languages: Optional[List[str]],
    process_options: Optional[List[str]],
    folder_path: Optional[str],
    is_private: bool,
    source_language: str,
):
    """Handle upload and processing of audio or text files."""
    if process_options is None:
        return JSONResponse(
            {"error": "Veuillez sélectionner au moins une option de traitement."},
            status_code=400,
        )
    
    normalized_options = [opt.lower().strip() for opt in process_options if opt.strip()]
    if not normalized_options:
        return JSONResponse(
            {"error": "Veuillez sélectionner au moins une option de traitement."},
            status_code=400,
        )
    
    # Validate languages if translation or audio generation is requested
    normalized_langs = []
    if languages:
        normalized_langs = [lang.lower() for lang in languages]
        if len(normalized_langs) > 2:
            return JSONResponse(
                {"error": "Veuillez sélectionner au maximum deux langues cibles."},
                status_code=400,
            )
        invalid = [lang for lang in normalized_langs if lang not in ALLOWED_LANGUAGE_CODES]
        if invalid:
            return JSONResponse(
                {"error": f"Langues non supportées: {', '.join(invalid)}"},
                status_code=400,
            )
    
    # Check if languages are needed
    needs_languages = any(opt in ["translate", "generate_audio"] for opt in normalized_options)
    if needs_languages and not normalized_langs:
        return JSONResponse(
            {"error": "Veuillez sélectionner au moins une langue cible pour la traduction ou la génération audio."},
            status_code=400,
        )
    
    file_id = str(uuid.uuid4())
    ext = Path(file.filename).suffix
    
    # Handle folder structure
    if folder_path:
        folder_path = folder_path.strip().strip("/").strip("\\")
        if folder_path:
            folder_dir = settings.PROCESSED_DIR / folder_path
            folder_dir.mkdir(parents=True, exist_ok=True)
            file_dir = folder_dir / file_id
        else:
            file_dir = settings.PROCESSED_DIR / file_id
    else:
        file_dir = settings.PROCESSED_DIR / file_id
    
    file_dir.mkdir(parents=True, exist_ok=True)
    
    # Save uploaded file
    original_path = file_dir / f"original{ext}"
    try:
        with open(original_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
    except Exception:
        logger.exception("Failed to store uploaded file")
        return JSONResponse(
            {"error": "Impossible de sauvegarder le fichier."},
            status_code=500,
        )
    
    # Save info.json
    info_path = file_dir / "info.json"
    info_data = {
        "folder_path": folder_path if folder_path else None,
        "is_private": is_private,
        "file_type": file_type,  # Store file type for audio/text files
    }
    info_path.write_text(json.dumps(info_data, indent=2), encoding="utf-8")
    
    # Process the file based on options
    try:
        results = {}
        
        if file_type == "audio":
            # Transcribe audio
            if "transcribe" in normalized_options:
                logger.info(f"Transcribing audio: {original_path}")
                transcribed_text = transcribe_long_audio(original_path, language=source_language)
                text_path = file_dir / "transcribed.txt"
                text_path.write_text(transcribed_text, encoding="utf-8")
                results["transcribed"] = str(text_path)
                
                # Improve text if requested
                if "improve_text" in normalized_options:
                    logger.info("Improving text with AI...")
                    improved_text = improve_text_with_ai(transcribed_text, language=source_language)
                    improved_path = file_dir / "improved.txt"
                    improved_path.write_text(improved_text, encoding="utf-8")
                    results["improved"] = str(improved_path)
                    transcribed_text = improved_text  # Use improved text for translation
                
                # Translate if requested
                if "translate" in normalized_options and normalized_langs:
                    for target_lang in normalized_langs:
                        logger.info(f"Translating to {target_lang}...")
                        translated_text = translate_text(transcribed_text, source_language, target_lang)
                        translated_path = file_dir / f"translated_{target_lang}.txt"
                        translated_path.write_text(translated_text, encoding="utf-8")
                        results[f"translated_{target_lang}"] = str(translated_path)
                        
                        # Generate audio if requested
                        if "generate_audio" in normalized_options:
                            logger.info(f"Generating TTS audio for {target_lang}...")
                            audio_path = file_dir / f"audio_{target_lang}.mp3"
                            await generate_long_tts_audio(translated_text, target_lang, audio_path)
                            results[f"audio_{target_lang}"] = str(audio_path)
        
        elif file_type == "text":
            # Read text file
            text_content = original_path.read_text(encoding="utf-8")
            
            # Improve text if requested
            if "improve_text" in normalized_options:
                logger.info("Improving text with AI...")
                improved_text = improve_text_with_ai(text_content, language=source_language)
                improved_path = file_dir / "improved.txt"
                improved_path.write_text(improved_text, encoding="utf-8")
                results["improved"] = str(improved_path)
                text_content = improved_text  # Use improved text for translation
            
            # Translate if requested
            if "translate" in normalized_options and normalized_langs:
                for target_lang in normalized_langs:
                    logger.info(f"Translating to {target_lang}...")
                    translated_text = translate_text(text_content, source_language, target_lang)
                    translated_path = file_dir / f"translated_{target_lang}.txt"
                    translated_path.write_text(translated_text, encoding="utf-8")
                    results[f"translated_{target_lang}"] = str(translated_path)
                    
                    # Generate audio if requested
                    if "generate_audio" in normalized_options:
                        logger.info(f"Generating TTS audio for {target_lang}...")
                        audio_path = file_dir / f"audio_{target_lang}.mp3"
                        await generate_long_tts_audio(translated_text, target_lang, audio_path)
                        results[f"audio_{target_lang}"] = str(audio_path)
        
        return JSONResponse({
            "id": file_id,
            "message": "Fichier traité avec succès.",
            "results": results,
        })
    
    except Exception as e:
        logger.exception("Error processing audio/text file")
        return JSONResponse(
            {"error": f"Erreur lors du traitement: {str(e)}"},
            status_code=500,
        )


# ---------- File downloads ----------
@app.get("/files/{file_id}/{filename:path}")
async def download_file(request: Request, file_id: str, filename: str):
    """Download a processed file (transcribed text, translated text, generated audio, etc.)."""
    # Find file directory (could be in a folder)
    file_dir = _find_video_directory(file_id)
    if not file_dir:
        return JSONResponse({"error": "Fichier non trouvé."}, status_code=404)
    
    file_path = file_dir / filename
    if not file_path.exists() or not file_path.is_file():
        return JSONResponse({"error": "Fichier non trouvé."}, status_code=404)
    
    # Check privacy
    info = _load_video_info(file_dir)
    is_private = info.get("is_private", False)
    folder_path = info.get("folder_path")
    if not is_editor(request) and (is_private or _is_folder_private(folder_path)):
        return JSONResponse({"error": "Accès refusé."}, status_code=403)
    
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/octet-stream",
    )


# ---------- File management (editors only) ----------

@app.delete("/api/videos/{video_id}")
async def delete_video(request: Request, video_id: str):
    """Delete a video and all its files."""
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent supprimer des vidéos."}, status_code=403)
    
    # Find video directory (could be in a folder)
    def _find_video_dir(directory: Path) -> Optional[Path]:
        for item in directory.iterdir():
            if item.is_dir():
                meta = _load_video_metadata(item)
                if meta and meta.id == video_id:
                    return item
                # Recursively search
                found = _find_video_dir(item)
                if found:
                    return found
        return None
    
    video_dir = _find_video_dir(settings.PROCESSED_DIR)
    if not video_dir:
        return JSONResponse({"error": "Vidéo non trouvée."}, status_code=404)
    
    try:
        shutil.rmtree(video_dir)
        return JSONResponse({"message": "Vidéo supprimée avec succès."})
    except Exception as e:
        logger.exception("Failed to delete video")
        return JSONResponse({"error": f"Impossible de supprimer la vidéo: {e}"}, status_code=500)

@app.put("/api/videos/{video_id}/rename")
async def rename_video(request: Request, video_id: str, new_filename: str = Form(...)):
    """Rename a video file."""
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent renommer des vidéos."}, status_code=403)
    
    # Find video directory
    def _find_video_dir(directory: Path) -> Optional[Path]:
        for item in directory.iterdir():
            if item.is_dir():
                meta = _load_video_metadata(item)
                if meta and meta.id == video_id:
                    return item
                found = _find_video_dir(item)
                if found:
                    return found
        return None
    
    video_dir = _find_video_dir(settings.PROCESSED_DIR)
    if not video_dir:
        return JSONResponse({"error": "Vidéo non trouvée."}, status_code=404)
    
    try:
        meta_path = video_dir / "metadata.json"
        meta = load_metadata(meta_path)
        meta.filename = new_filename
        save_metadata(meta, meta_path)
        return JSONResponse({"message": "Vidéo renommée avec succès."})
    except Exception as e:
        logger.exception("Failed to rename video")
        return JSONResponse({"error": f"Impossible de renommer la vidéo: {e}"}, status_code=500)

@app.put("/api/videos/{video_id}/privacy")
async def toggle_video_privacy(request: Request, video_id: str, is_private: bool = Form(...)):
    """Toggle video privacy."""
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent modifier la confidentialité."}, status_code=403)
    
    # Find video directory
    def _find_video_dir(directory: Path) -> Optional[Path]:
        for item in directory.iterdir():
            if item.is_dir():
                meta = _load_video_metadata(item)
                if meta and meta.id == video_id:
                    return item
                found = _find_video_dir(item)
                if found:
                    return found
        return None
    
    video_dir = _find_video_dir(settings.PROCESSED_DIR)
    if not video_dir:
        return JSONResponse({"error": "Vidéo non trouvée."}, status_code=404)
    
    try:
        info_path = video_dir / "info.json"
        info_data = {"folder_path": None, "is_private": is_private}
        if info_path.exists():
            existing = json.loads(info_path.read_text(encoding="utf-8"))
            info_data["folder_path"] = existing.get("folder_path")
        info_path.write_text(json.dumps(info_data, indent=2), encoding="utf-8")
        return JSONResponse({"message": "Confidentialité mise à jour."})
    except Exception as e:
        logger.exception("Failed to update privacy")
        return JSONResponse({"error": f"Impossible de mettre à jour la confidentialité: {e}"}, status_code=500)


# ---------- Subtitle editor (editors only) ----------

@app.get("/api/videos/{video_id}/subs/{lang}/edit")
async def get_subtitle_for_edit(request: Request, video_id: str, lang: str):
    """Get subtitle content for editing."""
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent modifier les sous-titres."}, status_code=403)
    
    # Find video directory
    def _find_video_dir(directory: Path) -> Optional[Path]:
        for item in directory.iterdir():
            if item.is_dir():
                meta = _load_video_metadata(item)
                if meta and meta.id == video_id:
                    return item
                found = _find_video_dir(item)
                if found:
                    return found
        return None
    
    video_dir = _find_video_dir(settings.PROCESSED_DIR)
    if not video_dir:
        return JSONResponse({"error": "Vidéo non trouvée."}, status_code=404)
    
    subs_path = video_dir / f"subs_{lang}.vtt"
    if not subs_path.exists():
        return JSONResponse({"error": "Sous-titres non trouvés."}, status_code=404)
    
    try:
        content = subs_path.read_text(encoding="utf-8")
        return JSONResponse({"content": content})
    except Exception as e:
        return JSONResponse({"error": f"Impossible de lire les sous-titres: {e}"}, status_code=500)

@app.put("/api/videos/{video_id}/subs/{lang}/edit")
async def save_subtitle_edit(request: Request, video_id: str, lang: str, content: str = Form(...)):
    """Save edited subtitle content."""
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent modifier les sous-titres."}, status_code=403)
    
    # Find video directory
    def _find_video_dir(directory: Path) -> Optional[Path]:
        for item in directory.iterdir():
            if item.is_dir():
                meta = _load_video_metadata(item)
                if meta and meta.id == video_id:
                    return item
                found = _find_video_dir(item)
                if found:
                    return found
        return None
    
    video_dir = _find_video_dir(settings.PROCESSED_DIR)
    if not video_dir:
        return JSONResponse({"error": "Vidéo non trouvée."}, status_code=404)
    
    subs_path = video_dir / f"subs_{lang}.vtt"
    try:
        subs_path.write_text(content, encoding="utf-8")
        return JSONResponse({"message": "Sous-titres mis à jour avec succès."})
    except Exception as e:
        return JSONResponse({"error": f"Impossible de sauvegarder les sous-titres: {e}"}, status_code=500)
