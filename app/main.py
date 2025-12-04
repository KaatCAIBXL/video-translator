import asyncio
import json
import logging
import re
import shutil
import tempfile
import time
import uuid
import requests
import base64
import httpx
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from .auth import get_role_from_request, is_editor, is_admin, create_session, can_generate_video, can_manage_characters, can_read_admin_messages, get_session_count, session_exists

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import settings
from .services import (
    ensure_dirs,
    extract_audio,
    extract_video_frame,
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
    filter_amara_segments,
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
from .models import VideoListItem, VideoMetadata, TranslationSegment, Character
from .job_store import job_store, JobStatus
from .stable_diffusion_service import StableDiffusionService, create_video_from_images
try:
    from .character_service import character_service
except Exception as e:
    logging.warning(f"Could not import character_service: {e}")
    character_service = None

try:
    from .message_service import message_service
except Exception as e:
    logging.warning(f"Could not import message_service: {e}")
    message_service = None

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

# Try to determine last update date from git (used on home page)
try:
    import subprocess

    repo_root = Path(__file__).resolve().parents[1]
    last_update_raw = subprocess.check_output(
        ["git", "log", "-1", "--format=%cd", "--date=short"],
        cwd=str(repo_root),
    )
    LAST_UPDATE = last_update_raw.decode("utf-8").strip()
except Exception:
    LAST_UPDATE = None

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
                # 1) Als er metadata is, gebruik die (voor video's)
                meta = _load_video_metadata(item)
                if meta and meta.id == video_id:
                    return item
                # 2) Voor simpele library-items (audio/tekst) zonder metadata:
                #    gebruik de map-naam als ID
                if item.name == video_id:
                    return item
                # 3) Recursief verder zoeken in submappen
                found = _search(item)
                if found:
                    return found
        return None
    return _search(settings.PROCESSED_DIR)

def _find_loose_file(file_id: str) -> Optional[Path]:
    """Find a loose file (uploaded directly to folder) by ID.
    The file_id is generated from the relative path, e.g., 'folder1_folder2_filename_mp4'."""
    def _search(directory: Path, current_path: str = "") -> Optional[Path]:
        for item in directory.iterdir():
            if item.is_file():
                # Generate ID from path (same as in _scan_directory)
                try:
                    rel_path = item.relative_to(settings.PROCESSED_DIR)
                    generated_id = str(rel_path).replace("\\", "/").replace("/", "_").replace(".", "_")
                    if generated_id == file_id:
                        return item
                except ValueError:
                    pass
            elif item.is_dir():
                # Recursively search subdirectories
                new_path = current_path + "/" + item.name if current_path else item.name
                found = _search(item, new_path)
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
    """Show home page first, then redirect to role selection if no session."""
    role = get_role_from_request(request)
    if not role:
        # Show home page first
        return templates.TemplateResponse(
            "home.html",
            {
                "request": request,
                "last_update": LAST_UPDATE,
            },
        )
    
    try:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "available_languages": LANGUAGE_OPTIONS,
                "is_editor": is_editor(request),
                "is_admin": is_admin(request),
                "can_generate_video": can_generate_video(request),
                "can_manage_characters": can_manage_characters(request),
                "can_read_admin_messages": can_read_admin_messages(request),
            },
        )
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.exception(f"Error rendering index template: {e}")
        return JSONResponse(
            {"error": f"Internal server error: {str(e)}"},
            status_code=500
        )

@app.get("/select-role")
async def select_role(request: Request):
    """Role selection page."""
    module = request.query_params.get("module")
    
    # Als er een module parameter is, stuur door naar de juiste module
    if module == "live-translator":
        return RedirectResponse(url="/live-translator", status_code=302)
    elif module == "saints":
        # Set viewer role and redirect to main app (for now, later can have dedicated saints page)
        session_id = create_session("viewer")
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(key="session_id", value=session_id, httponly=True, max_age=86400)
        return response
    elif module == "itech":
        # Set editor role and redirect to main app
        session_id = create_session("editor")
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(key="session_id", value=session_id, httponly=True, max_age=86400)
        return response
    elif module == "admin":
        # Set admin role and redirect to main app
        session_id = create_session("admin")
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(key="session_id", value=session_id, httponly=True, max_age=86400)
        return response
    
    return templates.TemplateResponse("select_role.html", {"request": request})

@app.post("/api/set-role")
async def set_role(request: Request, role: str = Form(...)):
    """Set the user's role and create a session."""
    if role not in ("viewer", "editor", "admin"):
        return JSONResponse({"error": "Rôle invalide"}, status_code=400)
    
    session_id = create_session(role)
    logger.info(f"Created session for role '{role}': session_id={session_id[:10]}...")
    response = RedirectResponse(url="/", status_code=302)
    # Set cookie with proper settings for cross-origin and security
    response.set_cookie(
        key="session_id", 
        value=session_id, 
        httponly=True, 
        max_age=86400*30,  # 30 days
        samesite="lax",  # Allow cookie to be sent with cross-site requests
        secure=False  # Set to True in production with HTTPS
    )
    logger.info(f"Set cookie in response. Response headers: {dict(response.headers)}")
    return response

@app.get("/api/current-role")
async def get_current_role(request: Request):
    """Get the current user's role (for debugging)."""
    session_id = request.cookies.get("session_id")
    role = get_role_from_request(request)
    return JSONResponse({
        "role": role,
        "session_id": session_id[:20] + "..." if session_id else None,
        "session_exists": session_exists(session_id) if session_id else False,
        "is_editor": is_editor(request),
        "is_admin": is_admin(request),
        "active_sessions": get_session_count()
    })


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
                    
                    # Check if transcribed.txt exists
                    has_transcription = (item / "transcribed.txt").exists()
                    
                    items.append(
                        VideoListItem(
                            id=meta.id,
                            filename=meta.filename,
                            file_type="video",
                            available_subtitles=subtitles,
                            available_dubs=dubs,
                            available_dub_audios=dub_audios,
                            available_combined_subtitles=combined,
                            has_transcription=has_transcription,
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
                                # Gebruik titel uit info.json indien aanwezig, anders bestandsnaam
                                filename = info.get("title") or original_file.name
                                # Use directory name as ID (same as video)
                                file_id = item.name
                                
                                # Check if transcribed.txt exists for audio/text files
                                has_transcription = (item / "transcribed.txt").exists()
                                
                                # Get source language and available translations from info.json
                                source_lang = info.get("source_language")
                                available_translations = info.get("available_translations", [])
                                
                                items.append(
                                    VideoListItem(
                                        id=file_id,
                                        filename=filename,
                                        file_type=file_type,
                                        available_subtitles=[],
                                        available_dubs=[],
                                        available_dub_audios=[],
                                        available_combined_subtitles=[],
                                        has_transcription=has_transcription,
                                        folder_path=file_folder_path,
                                        is_private=file_is_private,
                                        source_language=source_lang,
                                        available_translations=available_translations,
                        )
                    )
                else:
                    # This might be a folder, scan recursively
                    current_folder = folder_path + "/" + item.name if folder_path else item.name
                    _scan_directory(item, current_folder)
            else:
                # Check if this is a loose file (uploaded directly to folder)
                # Only process video, audio, and text files
                if item.suffix.lower() in [".mp4", ".avi", ".mov", ".mkv", ".webm",  # video
                                           ".mp3", ".wav", ".m4a", ".ogg", ".flac",  # audio
                                           ".txt"]:  # text
                    # Determine file type from extension
                    if item.suffix.lower() in [".mp4", ".avi", ".mov", ".mkv", ".webm"]:
                        file_type = "video"
                    elif item.suffix.lower() in [".mp3", ".wav", ".m4a", ".ogg", ".flac"]:
                        file_type = "audio"
                    else:
                        file_type = "text"
                    
                    # Check privacy based on folder
                    file_is_private = _is_folder_private(folder_path)
                    
                    # Filter: viewers can't see files in private folders
                    if not user_is_editor and file_is_private:
                        continue
                    
                    # Generate a unique ID from the file path
                    try:
                        rel_path = item.relative_to(settings.PROCESSED_DIR)
                        file_id = str(rel_path).replace("\\", "/").replace("/", "_").replace(".", "_")
                    except ValueError:
                        file_id = item.name
                    
                    items.append(
                        VideoListItem(
                            id=file_id,
                            filename=item.name,
                            file_type=file_type,
                            available_subtitles=[],
                            available_dubs=[],
                            available_dub_audios=[],
                            available_combined_subtitles=[],
                            folder_path=folder_path,
                            is_private=file_is_private,
                        )
                    )

    _scan_directory(settings.PROCESSED_DIR)
    return items


# ---------- API: upload + verwerken ----------

@app.post("/api/upload")
async def upload_video(
    request: Request,
    file: UploadFile = File(...)
):
    # Debug: check session and role
    session_id = request.cookies.get("session_id")
    role = get_role_from_request(request)
    logger.info(f"Upload request - session_id: {session_id[:20] if session_id else None}..., role: {role}, is_editor: {is_editor(request)}")
    logger.info(f"All cookies: {list(request.cookies.keys())}")
    logger.info(f"Session store has {get_session_count()} active sessions")
    if session_id:
        logger.info(f"Session lookup: session_id exists in store: {session_exists(session_id)}")
    
    # Only editors can upload
    if not is_editor(request):
        logger.warning(f"Upload denied - session_id: {session_id}, role: {role}, is_editor: {is_editor(request)}")
        if not role:
            error_msg = "Vous devez sélectionner un rôle pour télécharger des fichiers. Veuillez visiter /select-role et choisir 'I-tech' ou 'Admin'."
        else:
            error_msg = f"Seuls les éditeurs (I-tech) et les administrateurs peuvent télécharger des fichiers. Votre rôle actuel: '{role}'. Veuillez sélectionner 'I-tech' ou 'Admin' en visitant /select-role"
        return JSONResponse(
            {"error": error_msg},
            status_code=403,
        )
    
    # Parse form data manually to handle multiple values with same name
    form_data = await request.form()
    
    # Get thumbnail file if uploaded (FastAPI can handle files in form_data)
    thumbnail_file_upload = None
    if "thumbnail_file" in form_data:
        thumbnail_file_upload = form_data["thumbnail_file"]
    
    # Extract all form fields manually
    file_type = form_data.get("file_type", "video").lower()
    tts_speed_multiplier = float(form_data.get("tts_speed_multiplier", "1.0"))
    folder_path = form_data.get("folder_path") or None
    is_private = form_data.get("is_private", "false").lower() == "true"
    source_language = form_data.get("source_language", "fr")
    
    # Extract languages (can be multiple with same name)
    languages = form_data.getlist("languages")
    
    # Debug logging
    logger.info(f"Received form data - languages: {languages}, file_type: {file_type}")
    logger.info(f"All form keys: {list(form_data.keys())}")
    for key in form_data.keys():
        if key != "file":  # Skip file to avoid logging binary data
            values = form_data.getlist(key)
            logger.info(f"  {key}: {values}")
    
    # Extract process_options (can be multiple with same name)
    process_options = form_data.getlist("process_options")
    
    # Extract thumbnail data (only for videos)
    thumbnail_source = form_data.get("thumbnail_source") if file_type == "video" else None
    thumbnail_time = form_data.get("thumbnail_time") if thumbnail_source == "video_frame" else None
    thumbnail_file = thumbnail_file_upload if thumbnail_source == "upload" else None
    
    file_type = file_type.lower()
    if file_type not in ["video", "audio", "text"]:
        return JSONResponse(
            {"error": "Type de fichier invalide. Utilisez 'video', 'audio' ou 'text'."},
            status_code=400,
        )
    
    # For audio/text files, handle differently
    if file_type in ["audio", "text"]:
        return await handle_audio_text_upload(
            request, file_type, file, languages if languages else None, 
            process_options if process_options else None,
            folder_path, is_private, source_language
        )
    
    # Original video processing logic
    # For videos, languages are required only if subs, dub_audio, or dub_video are selected
    normalized_options = []
    if process_options:
        normalized_options = [opt.lower().strip() for opt in process_options if opt.strip()]
    
    needs_languages = any(opt in normalized_options for opt in ["subs", "dub_audio", "dub_video"])
    
    if needs_languages:
        if not languages or len(languages) == 0:
            logger.warning(f"No languages found in form data. Languages list: {languages}")
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
    else:
        # No languages needed if only transcribe is selected
        normalized_langs = []
        languages = []
    
    # Only validate language codes if languages are provided
    if normalized_langs:
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
    thumbnail_path = video_dir / "thumbnail.jpg"

    try:
        with open(video_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
    except Exception:
        logger.exception("Failed to store uploaded file")
        return JSONResponse(
            {"error": "Storing the uploaded video failed."}, status_code=500
        )
    
    # Process thumbnail if provided
    if thumbnail_source and file_type == "video":
        try:
            if thumbnail_source == "video_frame" and thumbnail_time:
                # Extract frame from video using ffmpeg
                time_seconds = float(thumbnail_time)
                await run_in_threadpool(extract_video_frame, video_path, thumbnail_path, time_seconds)
                logger.info(f"Extracted thumbnail frame at {time_seconds}s from video {video_id}")
            elif thumbnail_source == "upload" and thumbnail_file:
                # Save uploaded thumbnail
                logger.info(f"Processing uploaded thumbnail for video {video_id}, type: {type(thumbnail_file)}")
                if hasattr(thumbnail_file, 'read'):
                    # It's an UploadFile object
                    content = await thumbnail_file.read()
                    with open(thumbnail_path, "wb") as f:
                        f.write(content)
                    logger.info(f"Saved uploaded thumbnail for video {video_id} to {thumbnail_path}, size: {len(content)} bytes")
                elif hasattr(thumbnail_file, 'filename') and thumbnail_file.filename:
                    # It's a file from form_data
                    content = await thumbnail_file.read()
                    with open(thumbnail_path, "wb") as f:
                        f.write(content)
                    logger.info(f"Saved uploaded thumbnail for video {video_id} to {thumbnail_path}, size: {len(content)} bytes")
                else:
                    logger.warning(f"Thumbnail file is not a valid UploadFile object: {type(thumbnail_file)}")
        except Exception as e:
            logger.exception(f"Failed to process thumbnail for video {video_id}: {e}")
            # Don't fail the upload if thumbnail processing fails

    job_store.create_job(video_id, file.filename)

    # Get source language for transcription (default to "fr" if not provided)
    source_lang = form_data.get("source_language", "fr") if "transcribe" in normalized_options else None

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
            source_language=source_lang,
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
    source_language: Optional[str] = None,
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

        try:
            # Use source_language if provided, otherwise let Whisper detect it
            whisper_result = await run_in_threadpool(transcribe_audio_whisper, audio_path, language=source_language)
        except Exception as transcribe_exc:
            logger.exception(f"Transcription failed for video {video_id}: {transcribe_exc}")
            raise RuntimeError(f"Transcription failed: {str(transcribe_exc)}") from transcribe_exc
        
        original_lang = whisper_result.get("language", "unknown")

        # Save transcription text file if transcribe option is enabled
        if "transcribe" in options_set:
            transcribed_text = whisper_result.get("text", "").strip()
            if transcribed_text:
                try:
                    text_path = video_dir / "transcribed.txt"
                    text_path.write_text(transcribed_text, encoding="utf-8")
                    logger.info(f"Transcription saved to {text_path}")
                except Exception as e:
                    logger.exception(f"Error saving transcription file: {e}")
                    warnings.append(f"Kon transcriptiebestand niet opslaan: {str(e)}")

        # Only process translations if languages are provided and subs/dub options are selected
        translations = {}
        sentence_pairs = []  # Initialize to empty list for transcription-only jobs
        if languages and (create_subtitles or needs_dub_assets):
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
        elif create_subtitles or needs_dub_assets:
            # If subs/dub are selected but no languages provided, skip them
            if create_subtitles:
                warnings.append("Sous-titres non générés: aucune langue cible sélectionnée.")
            if needs_dub_assets:
                warnings.append("Doublage non généré: aucune langue cible sélectionnée.")

        if create_subtitles and translations:
            for lang, segs in translations.items():
                vtt_path = video_dir / f"subs_{lang}.vtt"
                # Filter out Amara.org segments before pairing and generating VTT
                filtered_segs = filter_amara_segments(segs)
                paired_segments = pair_translation_segments(filtered_segs)
                await run_in_threadpool(generate_vtt, paired_segments, vtt_path)

        # Combined subtitles option removed - no longer needed

        if needs_dub_assets and translations:
            for lang, segs in translations.items():
                if lang in LANGUAGES_WITHOUT_DUBBING:
                    label = LANGUAGE_LABELS.get(lang, lang.upper())
                    warnings.append(
                        f"Dubbing is voorlopig niet beschikbaar voor {label}."
                    )
                    continue
                # Check if Lingala has ElevenLabs configuration
                if lang == "ln" and (not settings.LINGALA_TTS_API_KEY or not settings.LINGALA_ELEVENLABS_VOICE_ID):
                    label = LANGUAGE_LABELS.get(lang, lang.upper())
                    warnings.append(
                        f"Dubbing is niet beschikbaar voor {label}. ElevenLabs API key en voice ID moeten geconfigureerd zijn."
                    )
                    continue
                # Check if Tshiluba has ElevenLabs configuration
                if lang == "lua" and (not settings.TSHILUBA_TTS_API_KEY or not settings.TSHILUBA_ELEVENLABS_VOICE_ID):
                    label = LANGUAGE_LABELS.get(lang, lang.upper())
                    warnings.append(
                        f"Dubbing is niet beschikbaar voor {label}. ElevenLabs API key en voice ID moeten geconfigureerd zijn."
                    )
                    continue
                # Check if Kituba has ElevenLabs configuration
                if lang == "kg" and (not settings.KITUBA_TTS_API_KEY or not settings.KITUBA_ELEVENLABS_VOICE_ID):
                    label = LANGUAGE_LABELS.get(lang, lang.upper())
                    warnings.append(
                        f"Dubbing is niet beschikbaar voor {label}. ElevenLabs API key en voice ID moeten geconfigureerd zijn."
                    )
                    continue
                # Check if Malagasy has ElevenLabs configuration
                if lang == "mg" and (not settings.MALAGASY_TTS_API_KEY or not settings.MALAGASY_ELEVENLABS_VOICE_ID):
                    label = LANGUAGE_LABELS.get(lang, lang.upper())
                    warnings.append(
                        f"Dubbing is niet beschikbaar voor {label}. ElevenLabs API key en voice ID moeten geconfigureerd zijn."
                    )
                    continue
                # Check if Yoruba has ElevenLabs configuration
                if lang == "yo" and (not settings.YORUBA_TTS_API_KEY or not settings.YORUBA_ELEVENLABS_VOICE_ID):
                    label = LANGUAGE_LABELS.get(lang, lang.upper())
                    warnings.append(
                        f"Dubbing is niet beschikbaar voor {label}. ElevenLabs API key en voice ID moeten geconfigureerd zijn."
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

                    # Filter out Amara.org segments before generating dub audio
                    filtered_segs = filter_amara_segments(segs)
                    await generate_dub_audio(
                        filtered_segs,
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
        error_message = str(exc)
        # Provide more specific error messages for common issues
        if "transcription" in error_message.lower() or "whisper" in error_message.lower():
            error_message = f"Transcription error: {error_message}"
        elif "translation" in error_message.lower():
            error_message = f"Translation error: {error_message}"
        elif "dub" in error_message.lower() or "tts" in error_message.lower():
            error_message = f"Audio dubbing error: {error_message}"
        job_store.mark_failed(
            video_id,
            error_message,
            warnings=warnings,
        )
    except Exception as exc:
        logger.exception("Unexpected error while processing job %s: %s", video_id, exc)
        # Try to provide a more helpful error message
        error_message = f"Something went wrong while processing the video: {str(exc)}"
        # Check for common error types
        if "transcription" in str(exc).lower() or "whisper" in str(exc).lower():
            error_message = f"Transcription failed: {str(exc)}"
        elif "translation" in str(exc).lower():
            error_message = f"Translation failed: {str(exc)}"
        elif "dub" in str(exc).lower() or "tts" in str(exc).lower():
            error_message = f"Audio dubbing failed: {str(exc)}"
        job_store.mark_failed(
            video_id,
            error_message,
            warnings=warnings,
        )


# ---------- video + ondertitels / dub leveren ----------
def _video_base_stem(meta: Optional[VideoMetadata], fallback: Path) -> str:
    if meta:
        return Path(meta.filename).stem
    return fallback.stem

@app.get("/videos/{video_id}/thumbnail")
async def get_video_thumbnail(request: Request, video_id: str):
    """Get the thumbnail for a video."""
    video_dir = _find_video_directory(video_id)
    if not video_dir:
        logger.warning(f"Thumbnail requested for video {video_id} but directory not found")
        return JSONResponse({"error": "Video not found"}, status_code=404)
    
    thumbnail_path = video_dir / "thumbnail.jpg"
    if not thumbnail_path.exists():
        # Try other common thumbnail formats
        for ext in [".png", ".jpeg", ".jpg"]:
            alt_path = video_dir / f"thumbnail{ext}"
            if alt_path.exists():
                thumbnail_path = alt_path
                break
        else:
            logger.info(f"Thumbnail not found for video {video_id} in {video_dir}. Available files: {list(video_dir.iterdir()) if video_dir.exists() else 'directory does not exist'}")
            # Return 404 instead of transparent pixel so frontend can handle it properly
            return JSONResponse({"error": "Thumbnail not found"}, status_code=404)
    
    logger.info(f"Serving thumbnail for video {video_id} from {thumbnail_path}")
    # Determine correct media type based on file extension
    if thumbnail_path.suffix.lower() == ".png":
        media_type = "image/png"
    elif thumbnail_path.suffix.lower() in [".jpg", ".jpeg"]:
        media_type = "image/jpeg"
    else:
        media_type = "image/jpeg"  # Default
    
    return FileResponse(thumbnail_path, media_type=media_type)


@app.get("/videos/{video_id}/original")
async def get_original_video(request: Request, video_id: str):
    session_id = request.cookies.get("session_id")
    role = get_role_from_request(request)
    logger.info(f"Get original video request - session_id: {session_id}, role: {role}, is_editor: {is_editor(request)}, video_id: {video_id}")
    
    # First try to find as a video directory
    video_dir = _find_video_directory(video_id)
    if video_dir and video_dir.exists():
        # Check privacy (video itself or parent folder)
        info = _load_video_info(video_dir)
        video_is_private = info.get("is_private", False) or _is_folder_private(info.get("folder_path"))
        if video_is_private and not is_editor(request):
            logger.warning(f"Get original video denied - session_id: {session_id}, role: {role}, video_id: {video_id}, is_private: {video_is_private}")
            return JSONResponse({"error": "Accès refusé"}, status_code=403)

        original_path = _find_original_video(video_dir)
        if original_path is not None:
            meta = _load_video_metadata(video_dir)
            filename = meta.filename if meta else original_path.name
            return FileResponse(original_path, filename=filename)
    
    # If not found as video directory, try to find as loose video file
    loose_file = _find_loose_file(video_id)
    if loose_file and loose_file.exists() and loose_file.is_file():
        # Check if it's a video file
        if loose_file.suffix.lower() in [".mp4", ".avi", ".mov", ".mkv", ".webm"]:
            # Check privacy based on folder
            try:
                rel_path = loose_file.parent.relative_to(settings.PROCESSED_DIR)
                folder_path = str(rel_path) if str(rel_path) != "." else None
            except ValueError:
                folder_path = None
            
            if not is_editor(request) and _is_folder_private(folder_path):
                return JSONResponse({"error": "Accès refusé"}, status_code=403)
            
            return FileResponse(loose_file, filename=loose_file.name)
    
    return JSONResponse({"error": "Vidéo non trouvée"}, status_code=404)


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
    session_id = request.cookies.get("session_id")
    role = get_role_from_request(request)
    logger.info(f"Upload file to folder request - session_id: {session_id}, role: {role}, is_editor: {is_editor(request)}, filename: {file.filename}")
    
    if not is_editor(request):
        logger.warning(f"Upload to folder denied - session_id: {session_id}, role: {role}, filename: {file.filename}")
        return JSONResponse({"error": "Seuls les éditeurs peuvent télécharger des fichiers."}, status_code=403)
    
    folder_dir = settings.PROCESSED_DIR / folder_path
    if not folder_dir.exists() or not folder_dir.is_dir():
        return JSONResponse({"error": "Dossier non trouvé."}, status_code=404)
    
    try:
        # Check if file already exists
        file_path = folder_dir / file.filename
        if file_path.exists():
            return JSONResponse({"error": "Un fichier avec ce nom existe déjà dans ce dossier."}, status_code=400)
        
        # Save file directly to folder
        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        return JSONResponse({"message": f"Fichier téléchargé avec succès dans {folder_path}.", "filename": file.filename})
    except Exception as e:
        logger.exception("Failed to upload file to folder")
        return JSONResponse({"error": f"Impossible de télécharger le fichier: {e}"}, status_code=500)


# ============================================================
# NEW SIMPLIFIED ACTIONS (DOWNLOAD-ONLY)
# ============================================================

@app.post("/api/transcribe")
async def transcribe_file_download(
    request: Request,
    file: UploadFile = File(...),
    source_language: str = Form(...),
    improve_with_ai: bool = Form(False)
):
    """Transcribe audio/video file and return download (download-only, not saved to library)."""
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent transcrire des fichiers."}, status_code=403)
    
    # Create temporary file
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix) as tmp_file:
        tmp_path = Path(tmp_file.name)
        shutil.copyfileobj(file.file, tmp_file)
    
    try:
        # Extract audio if video
        if tmp_path.suffix.lower() in ['.mp4', '.avi', '.mov', '.mkv', '.webm']:
            audio_path = tmp_path.with_suffix('.wav')
            await run_in_threadpool(extract_audio, tmp_path, audio_path)
            audio_file_path = audio_path
        else:
            audio_file_path = tmp_path
        
        # Transcribe
        transcribed_text = await run_in_threadpool(
            transcribe_long_audio,
            audio_file_path,
            source_language
        )
        
        if not transcribed_text or not transcribed_text.strip():
            return JSONResponse({"error": "La transcription est vide."}, status_code=400)
        
        # Apply manual corrections (from subscription_corrections.txt)
        # Import needed function
        from .audio_text_services import _apply_subscription_corrections
        transcribed_text = _apply_subscription_corrections(transcribed_text)
        
        # Improve with AI if requested
        if improve_with_ai:
            transcribed_text = await run_in_threadpool(
                improve_text_with_ai,
                transcribed_text,
                source_language
            )
        
        # Create temporary text file for download
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', encoding='utf-8') as txt_file:
            txt_file.write(transcribed_text)
            txt_path = Path(txt_file.name)
        
        # Return file for download
        return FileResponse(
            txt_path,
            media_type='text/plain',
            filename=f"transcription_{Path(file.filename).stem}.txt",
            background=lambda: txt_path.unlink() if txt_path.exists() else None
        )
    except Exception as e:
        logger.exception("Error transcribing file")
        return JSONResponse({"error": f"Erreur lors de la transcription: {e}"}, status_code=500)
    finally:
        # Cleanup temp files
        temp_files = [tmp_path]
        if 'audio_file_path' in locals() and audio_file_path != tmp_path:
            temp_files.append(audio_file_path)
        for path in temp_files:
            if path and path.exists():
                try:
                    path.unlink()
                except:
                    pass


@app.post("/api/translate-text")
async def translate_text_download(
    request: Request,
    file: UploadFile = File(...),
    source_language: str = Form(...),
    target_language: str = Form(...)
):
    """Translate text file and return download (download-only, not saved to library)."""
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent traduire des fichiers."}, status_code=403)
    
    if target_language not in ALLOWED_LANGUAGE_CODES:
        return JSONResponse({"error": f"Langue cible non supportée: {target_language}"}, status_code=400)
    
    # Create temporary file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', encoding='utf-8') as tmp_file:
        content = await file.read()
        tmp_file.write(content.decode('utf-8'))
        tmp_path = Path(tmp_file.name)
    
    try:
        # Read text
        text_content = tmp_path.read_text(encoding='utf-8')
        
        # Translate
        translated_text = await run_in_threadpool(
            translate_text,
            text_content,
            source_language,
            target_language
        )
        
        if not translated_text or not translated_text.strip():
            return JSONResponse({"error": "La traduction est vide."}, status_code=400)
        
        # Create temporary text file for download
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', encoding='utf-8') as txt_file:
            txt_file.write(translated_text)
            txt_path = Path(txt_file.name)
        
        # Return file for download
        return FileResponse(
            txt_path,
            media_type='text/plain',
            filename=f"traduction_{Path(file.filename).stem}_{target_language}.txt",
            background=lambda: txt_path.unlink() if txt_path.exists() else None
        )
    except Exception as e:
        logger.exception("Error translating text")
        return JSONResponse({"error": f"Erreur lors de la traduction: {e}"}, status_code=500)
    finally:
        # Cleanup temp files
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except:
                pass


@app.post("/api/generate-audio")
async def generate_audio_download(
    request: Request,
    file: UploadFile = File(...),
    language: str = Form(...)
):
    """Generate audio from text file and return download (download-only, not saved to library)."""
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent générer des fichiers audio."}, status_code=403)
    
    if language not in ALLOWED_LANGUAGE_CODES:
        return JSONResponse({"error": f"Langue non supportée: {language}"}, status_code=400)
    
    # Create temporary file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', encoding='utf-8') as tmp_file:
        content = await file.read()
        tmp_file.write(content.decode('utf-8'))
        tmp_path = Path(tmp_file.name)
    
    try:
        # Read text
        text_content = tmp_path.read_text(encoding='utf-8')
        
        if not text_content or not text_content.strip():
            return JSONResponse({"error": "Le fichier texte est vide."}, status_code=400)
        
        # Create temporary audio file for download
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as audio_file:
            audio_path = Path(audio_file.name)
        
        # Generate audio
        await generate_long_tts_audio(text_content, language, audio_path)
        
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            return JSONResponse({"error": "La génération audio a échoué."}, status_code=500)
        
        # Return file for download
        return FileResponse(
            audio_path,
            media_type='audio/mpeg',
            filename=f"audio_{Path(file.filename).stem}_{language}.mp3",
            background=lambda: audio_path.unlink() if audio_path.exists() else None
        )
    except Exception as e:
        logger.exception("Error generating audio")
        return JSONResponse({"error": f"Erreur lors de la génération audio: {e}"}, status_code=500)
    finally:
        # Cleanup temp files
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except:
                pass


@app.post("/api/upload-video-to-library")
async def upload_video_to_library(
    request: Request,
    file: UploadFile = File(...),
    folder_path: str = Form(None),
    source_language: str = Form(...),
    thumbnail_source: str = Form(None),
    thumbnail_time: str = Form(None),
    thumbnail_file: UploadFile = File(None)
):
    """Upload video to library without processing (only save file + thumbnail + metadata)."""
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent télécharger des fichiers."}, status_code=403)
    
    video_id = str(uuid.uuid4())
    ext = Path(file.filename).suffix
    
    # Handle folder structure
    if folder_path:
        folder_path = folder_path.strip().strip("/").strip("\\")
        if folder_path:
            folder_dir = settings.PROCESSED_DIR / folder_path
            folder_dir.mkdir(parents=True, exist_ok=True)
            video_dir = folder_dir / video_id
        else:
            video_dir = settings.PROCESSED_DIR / video_id
    else:
        video_dir = settings.PROCESSED_DIR / video_id
    
    video_dir.mkdir(parents=True, exist_ok=True)
    
    video_path = video_dir / f"original{ext}"
    thumbnail_path = video_dir / "thumbnail.jpg"
    
    try:
        # Save video file
        with open(video_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
    except Exception:
        logger.exception("Failed to store uploaded video")
        return JSONResponse({"error": "Impossible de sauvegarder la vidéo."}, status_code=500)
    
    # Process thumbnail
    if thumbnail_source:
        try:
            if thumbnail_source == "video_frame" and thumbnail_time:
                time_seconds = float(thumbnail_time)
                await run_in_threadpool(extract_video_frame, video_path, thumbnail_path, time_seconds)
            elif thumbnail_source == "upload" and thumbnail_file:
                content = await thumbnail_file.read()
                with open(thumbnail_path, "wb") as f:
                    f.write(content)
        except Exception as e:
            logger.exception(f"Failed to process thumbnail: {e}")
    
    # Save minimal metadata
    meta = VideoMetadata(
        id=video_id,
        filename=file.filename,
        original_language=source_language,
        sentence_pairs=[],
        translations={}
    )
    meta_path = video_dir / "metadata.json"
    await run_in_threadpool(save_metadata, meta, meta_path)
    
    # Save info.json with folder and privacy info
    info_path = video_dir / "info.json"
    info_data = {
        "folder_path": folder_path if folder_path else None,
        "is_private": False,
    }
    info_path.write_text(json.dumps(info_data, indent=2), encoding="utf-8")
    
    return JSONResponse({"message": "Vidéo téléchargée avec succès.", "id": video_id})


@app.post("/api/upload-audio-to-library")
async def upload_audio_to_library(
    request: Request,
    files: List[UploadFile] = File(...),
    folder_path: str = Form(None),
    languages: List[str] = Form(...),
    title: str = Form(...)
):
    """Upload één audio-item met meerdere taalvarianten naar de bibliotheek.
    
    - Alle bestanden delen hetzelfde titelveld.
    - De eerste taal wordt beschouwd als de brontaal (source_language).
    - Extra talen worden opgeslagen als beschikbare vertalingen (available_translations).
    """
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent télécharger des fichiers."}, status_code=403)
    
    if not files or len(files) == 0:
        return JSONResponse({"error": "Au moins un fichier audio est requis."}, status_code=400)
    
    if not languages or len(languages) != len(files):
        return JSONResponse({"error": "Le nombre de langues doit correspondre au nombre de fichiers audio."}, status_code=400)
    
    file_id = str(uuid.uuid4())
    
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
    
    available_translations: List[str] = []
    source_language: Optional[str] = None
    
    try:
        for idx, (upload, lang) in enumerate(zip(files, languages)):
            ext = Path(upload.filename).suffix or ".mp3"
            if idx == 0:
                # Eerste bestand = originele bron
                source_language = lang
                target_path = file_dir / f"original{ext}"
            else:
                # Verdere bestanden = vertalingen
                target_path = file_dir / f"audio_{lang}{ext}"
                if lang not in available_translations:
                    available_translations.append(lang)
            
            with open(target_path, "wb") as f:
                shutil.copyfileobj(upload.file, f)
    except Exception:
        logger.exception("Failed to store uploaded audio")
        return JSONResponse({"error": "Impossible de sauvegarder les fichiers audio."}, status_code=500)
    
    # Save info.json
    info_path = file_dir / "info.json"
    info_data = {
        "folder_path": folder_path if folder_path else None,
        "is_private": False,
        "file_type": "audio",
        "source_language": source_language,
        "available_translations": available_translations,
        "title": title,
    }
    info_path.write_text(json.dumps(info_data, indent=2), encoding="utf-8")
    
    return JSONResponse({"message": "Fichier audio téléchargé avec succès.", "id": file_id})


@app.post("/api/upload-text-to-library")
async def upload_text_to_library(
    request: Request,
    file: UploadFile = File(...),
    folder_path: str = Form(None),
    source_language: str = Form(...)
):
    """Upload text to library without processing (only save file + metadata)."""
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent télécharger des fichiers."}, status_code=403)
    
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
    
    original_path = file_dir / f"original{ext}"
    
    try:
        # Save text file
        content = await file.read()
        with open(original_path, "wb") as f:
            f.write(content)
    except Exception:
        logger.exception("Failed to store uploaded text")
        return JSONResponse({"error": "Impossible de sauvegarder le fichier texte."}, status_code=500)
    
    # Save info.json
    info_path = file_dir / "info.json"
    info_data = {
        "folder_path": folder_path if folder_path else None,
        "is_private": False,
        "file_type": "text",
        "source_language": source_language
    }
    info_path.write_text(json.dumps(info_data, indent=2), encoding="utf-8")
    
    return JSONResponse({"message": "Fichier texte téléchargé avec succès.", "id": file_id})


# ============================================================
# AUDIO TRANSLATION AND TEXT EDITING
# ============================================================

@app.post("/api/audio/translate")
async def translate_audio(request: Request):
    """Generate a translated audio version from an existing audio file (TTS-based)."""
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent générer des traductions audio."}, status_code=403)
    
    # Parse JSON body
    try:
        body = await request.json()
        audio_id = body.get("audio_id")
        source_language = body.get("source_language")
        target_language = body.get("target_language")
    except Exception:
        return JSONResponse({"error": "Données JSON invalides."}, status_code=400)
    
    if not audio_id or not source_language or not target_language:
        return JSONResponse({"error": "audio_id, source_language et target_language sont requis."}, status_code=400)
    
    if target_language not in ALLOWED_LANGUAGE_CODES:
        return JSONResponse({"error": f"Langue cible non supportée: {target_language}"}, status_code=400)
    
    # Find audio directory
    audio_dir = _find_video_directory(audio_id)
    if not audio_dir or not audio_dir.exists():
        return JSONResponse({"error": "Fichier audio non trouvé."}, status_code=404)
    
    # Check if it's actually an audio file
    info = _load_video_info(audio_dir)
    if info.get("file_type") != "audio":
        return JSONResponse({"error": "Ce fichier n'est pas un fichier audio."}, status_code=400)
    
    # Get original audio file
    original_path = None
    for ext in ['.mp3', '.wav', '.m4a', '.ogg', '.flac']:
        test_path = audio_dir / f"original{ext}"
        if test_path.exists():
            original_path = test_path
            break
    
    if not original_path:
        return JSONResponse({"error": "Fichier audio original non trouvé."}, status_code=404)
    
    try:
        # Step 1: Transcribe audio if not already transcribed
        transcribed_path = audio_dir / "transcribed.txt"
        if not transcribed_path.exists():
            logger.info(f"Transcribing audio {audio_id} for translation")
            transcribed_text = await run_in_threadpool(
                transcribe_long_audio,
                original_path,
                source_language
            )
            if transcribed_text and transcribed_text.strip():
                transcribed_path.write_text(transcribed_text, encoding="utf-8")
            else:
                return JSONResponse({"error": "La transcription a échoué ou est vide."}, status_code=500)
        
        transcribed_text = transcribed_path.read_text(encoding="utf-8")
        
        # Step 2: Apply manual corrections
        from .audio_text_services import _apply_subscription_corrections
        transcribed_text = _apply_subscription_corrections(transcribed_text)
        
        # Step 3: Translate text
        logger.info(f"Translating audio {audio_id} from {source_language} to {target_language}")
        translated_text = await run_in_threadpool(
            translate_text,
            transcribed_text,
            source_language,
            target_language
        )
        
        if not translated_text or not translated_text.strip():
            return JSONResponse({"error": "La traduction a échoué ou est vide."}, status_code=500)
        
        # Step 4: Generate TTS audio
        translated_audio_path = audio_dir / f"audio_{target_language}.mp3"
        logger.info(f"Generating TTS audio for {audio_id} in {target_language}")
        await generate_long_tts_audio(translated_text, target_language, translated_audio_path)
        
        # Update info.json to track available translations
        info_data = info.copy()
        if "available_translations" not in info_data:
            info_data["available_translations"] = []
        if target_language not in info_data["available_translations"]:
            info_data["available_translations"].append(target_language)
        info_path = audio_dir / "info.json"
        info_path.write_text(json.dumps(info_data, indent=2), encoding="utf-8")
        
        return JSONResponse({
            "message": f"Traduction audio générée avec succès en {target_language}.",
            "audio_path": str(translated_audio_path)
        })
    except Exception as e:
        logger.exception(f"Error translating audio {audio_id}")
        return JSONResponse({"error": f"Erreur lors de la traduction audio: {e}"}, status_code=500)


@app.post("/api/audio/upload-translation")
async def upload_audio_translation(
    request: Request,
    audio_id: str = Form(...),
    target_language: str = Form(...),
    file: UploadFile = File(...),
):
    """
    Upload een reeds bestaande vertaalde audio voor een bestaand audio-item.
    De vertaalde audio wordt opgeslagen als audio_{lang}.ext onder dezelfde titel.
    """
    if not is_editor(request):
        return JSONResponse(
            {"error": "Seuls les éditeurs peuvent ajouter des traductions audio."},
            status_code=403,
        )

    if target_language not in ALLOWED_LANGUAGE_CODES:
        return JSONResponse(
            {"error": f"Langue cible non supportée: {target_language}"},
            status_code=400,
        )

    # Vind de map van dit audio-item
    audio_dir = _find_video_directory(audio_id)
    if not audio_dir or not audio_dir.exists():
        return JSONResponse(
            {"error": "Fichier audio non trouvé."},
            status_code=404,
        )

    # Bepaal extensie op basis van upload
    ext = Path(file.filename).suffix or ".mp3"
    translated_audio_path = audio_dir / f"audio_{target_language}{ext}"

    try:
        with open(translated_audio_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        # Metadata bijwerken
        info_path = audio_dir / "info.json"
        info = {}
        if info_path.exists():
            try:
                info = json.loads(info_path.read_text(encoding="utf-8"))
            except Exception:
                info = {}

        if "available_translations" not in info or not isinstance(
            info.get("available_translations"), list
        ):
            info["available_translations"] = []

        if target_language not in info["available_translations"]:
            info["available_translations"].append(target_language)

        info_path.write_text(json.dumps(info, indent=2), encoding="utf-8")

        return JSONResponse(
            {
                "message": f"Traduction audio téléchargée avec succès pour {target_language}.",
                "audio_path": str(translated_audio_path),
            }
        )
    except Exception as e:
        logger.exception("Failed to upload audio translation")
        return JSONResponse(
            {"error": f"Erreur lors du téléchargement de la traduction audio: {e}"},
            status_code=500,
        )

@app.post("/api/videos/generate-subtitles")
async def generate_video_subtitles(request: Request):
    """Generate subtitles for a video in specified languages."""
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent générer des sous-titres."}, status_code=403)
    
    # Parse JSON body
    try:
        body = await request.json()
        video_id = body.get("video_id")
        languages = body.get("languages", [])
    except Exception:
        return JSONResponse({"error": "Données JSON invalides."}, status_code=400)
    
    if not video_id or not languages:
        return JSONResponse({"error": "video_id et languages sont requis."}, status_code=400)
    
    # Find video directory
    video_dir = _find_video_directory(video_id)
    if not video_dir or not video_dir.exists():
        return JSONResponse({"error": "Vidéo non trouvée."}, status_code=404)
    
    # Validate languages
    invalid_langs = [lang for lang in languages if lang not in ALLOWED_LANGUAGE_CODES]
    if invalid_langs:
        return JSONResponse({"error": f"Langues non supportées: {', '.join(invalid_langs)}"}, status_code=400)
    
    try:
        # Get or create transcription
        transcribed_path = video_dir / "transcribed.txt"
        audio_path = video_dir / "audio.wav"
        
        if not transcribed_path.exists():
            if not audio_path.exists():
                # Extract audio from video
                video_path = _find_original_video(video_dir)
                if not video_path:
                    return JSONResponse({"error": "Fichier vidéo original non trouvé."}, status_code=404)
                await run_in_threadpool(extract_audio, video_path, audio_path)
            
            # Transcribe
            meta = _load_video_metadata(video_dir)
            source_lang = meta.original_language if meta else "fr"
            transcribed_text = await run_in_threadpool(
                transcribe_long_audio,
                audio_path,
                source_lang
            )
            if transcribed_text and transcribed_text.strip():
                transcribed_path.write_text(transcribed_text, encoding="utf-8")
            else:
                return JSONResponse({"error": "La transcription a échoué."}, status_code=500)
        
        transcribed_text = transcribed_path.read_text(encoding="utf-8")
        
        # Apply manual corrections
        from .audio_text_services import _apply_subscription_corrections
        transcribed_text = _apply_subscription_corrections(transcribed_text)
        
        # Load or create metadata
        meta = _load_video_metadata(video_dir)
        if not meta:
            # Create basic metadata
            meta = VideoMetadata(
                id=video_id,
                filename=video_dir.name,
                original_language="fr",
                sentence_pairs=[],
                translations={}
            )
        
        # Generate subtitles for each language
        from .services import generate_vtt
        sentence_pairs = meta.sentence_pairs if meta.sentence_pairs else []
        
        if not sentence_pairs:
            # Build very simple sentence segments op basis van de platte tekst.
            # We splitsen op regeleinden: elke regel wordt één subtitle-segment.
            sentence_pairs = []
            current_start = 0.0
            default_duration = 3.0  # eenvoudige placeholder-duur per regel
            for line in transcribed_text.splitlines():
                text = (line or "").strip()
                if not text:
                    continue
                pair = {
                    "start": current_start,
                    "end": current_start + default_duration,
                    "text": text,
                }
                sentence_pairs.append(pair)
                current_start += default_duration
        
        # Update metadata with translations
        for target_lang in languages:
            # Translate segments
            translated_segments: List[TranslationSegment] = []
            for pair in sentence_pairs:
                translated_text_seg = await run_in_threadpool(
                    translate_text,
                    pair.get("text", ""),
                    meta.original_language,
                    target_lang
                )
                translated_segments.append(
                    TranslationSegment(
                        start=pair.get("start", 0),
                        end=pair.get("end", 0),
                        text=translated_text_seg,
                        language=target_lang,
                    )
                )
            
            # Generate VTT file
            vtt_path = video_dir / f"subs_{target_lang}.vtt"
            # Gebruik dezelfde helper als elders in de code: generate_vtt schrijft zelf naar disk
            await run_in_threadpool(generate_vtt, translated_segments, vtt_path)
            
            # Update metadata for this language
            meta.translations[target_lang] = translated_segments
        
        # Save updated metadata
        meta_path = video_dir / "metadata.json"
        await run_in_threadpool(save_metadata, meta, meta_path)
        
        return JSONResponse({
            "message": f"Sous-titres générés avec succès pour {len(languages)} langue(s).",
            "languages": languages
        })
    except Exception as e:
        logger.exception(f"Error generating subtitles for video {video_id}")
        return JSONResponse({"error": f"Erreur lors de la génération des sous-titres: {e}"}, status_code=500)


@app.post("/api/texts/save")
async def save_text_file(request: Request):
    """Save edited text file."""
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent modifier des fichiers texte."}, status_code=403)
    
    # Parse JSON body
    try:
        body = await request.json()
        text_id = body.get("text_id")
        content = body.get("content")
    except Exception:
        return JSONResponse({"error": "Données JSON invalides."}, status_code=400)
    
    if not text_id or content is None:
        return JSONResponse({"error": "text_id et content sont requis."}, status_code=400)
    
    # Find text directory
    text_dir = _find_video_directory(text_id)
    if not text_dir or not text_dir.exists():
        return JSONResponse({"error": "Fichier texte non trouvé."}, status_code=404)
    
    # Check if it's actually a text file
    info = _load_video_info(text_dir)
    if info.get("file_type") != "text":
        return JSONResponse({"error": "Ce fichier n'est pas un fichier texte."}, status_code=400)
    
    try:
        # Save to original file
        original_path = None
        for ext in ['.txt', '.text']:
            test_path = text_dir / f"original{ext}"
            if test_path.exists():
                original_path = test_path
                break
        
        if not original_path:
            # Create new file if doesn't exist
            original_path = text_dir / "original.txt"
        
        original_path.write_text(content, encoding="utf-8")
        
        return JSONResponse({"message": "Texte enregistré avec succès."})
    except Exception as e:
        logger.exception(f"Error saving text {text_id}")
        return JSONResponse({"error": f"Erreur lors de l'enregistrement: {e}"}, status_code=500)


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
                try:
                    transcribed_text = transcribe_long_audio(original_path, language=source_language)
                    if transcribed_text and transcribed_text.strip():
                        text_path = file_dir / "transcribed.txt"
                        text_path.write_text(transcribed_text, encoding="utf-8")
                        results["transcribed"] = str(text_path)
                    else:
                        logger.warning("Transcription returned empty text, skipping file creation")
                except Exception as e:
                    logger.exception(f"Error during transcription: {e}")
                    # Don't write error to file, just log it
                
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
    """Download a processed file (transcribed text, translated text, generated audio, etc.) or a loose file."""
    session_id = request.cookies.get("session_id")
    role = get_role_from_request(request)
    logger.info(f"Download file request - session_id: {session_id}, role: {role}, is_editor: {is_editor(request)}, file_id: {file_id}, filename: {filename}")
    
    # First try to find as a directory-based file (processed files)
    file_dir = _find_video_directory(file_id)
    if file_dir:
        file_path = file_dir / filename
        if file_path.exists() and file_path.is_file():
            # Check privacy
            info = _load_video_info(file_dir)
            is_private = info.get("is_private", False)
            folder_path = info.get("folder_path")
            if not is_editor(request) and (is_private or _is_folder_private(folder_path)):
                logger.warning(f"Download denied - session_id: {session_id}, role: {role}, file_id: {file_id}, filename: {filename}, is_private: {is_private}")
                return JSONResponse({"error": "Accès refusé."}, status_code=403)
            
            # Set proper content type for text files
            content_type = "application/octet-stream"
            if filename.endswith(".txt"):
                content_type = "text/plain; charset=utf-8"
            
            return FileResponse(
                path=str(file_path),
                filename=filename,
                media_type=content_type,
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                },
            )
    
    # If not found as directory-based, try to find as loose file
    # The filename parameter might be the actual filename for loose files
    loose_file = _find_loose_file(file_id)
    if loose_file and loose_file.exists() and loose_file.is_file():
        # Check if filename matches (for loose files, filename is passed in the URL)
        if filename and loose_file.name != filename:
            # Try to find file with matching filename in the same directory
            loose_file_path = loose_file.parent / filename
            if loose_file_path.exists() and loose_file_path.is_file():
                loose_file = loose_file_path
            else:
                # If filename doesn't match, still allow download of the found file
                pass
        
        # Check privacy based on folder
        try:
            rel_path = loose_file.parent.relative_to(settings.PROCESSED_DIR)
            folder_path = str(rel_path) if str(rel_path) != "." else None
        except ValueError:
            folder_path = None
        
        if not is_editor(request) and _is_folder_private(folder_path):
            return JSONResponse({"error": "Accès refusé."}, status_code=403)
        
        # Set proper content type for text files
        content_type = "application/octet-stream"
        if loose_file.name.endswith(".txt"):
            content_type = "text/plain; charset=utf-8"
        
        return FileResponse(
            path=str(loose_file),
            filename=loose_file.name,
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{loose_file.name}"',
            },
        )
    
    return JSONResponse({"error": "Fichier non trouvé."}, status_code=404)


# ---------- File management (editors only) ----------

@app.delete("/api/videos/{video_id}")
async def delete_video(request: Request, video_id: str):
    """Delete a video, audio, text directory or loose file."""
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent supprimer des vidéos."}, status_code=403)
    
    # 1) Eerst proberen als directory-gebaseerd item (video/audio/tekst met metadata of info.json)
    video_dir = _find_video_directory(video_id)
    if video_dir and video_dir.exists():
        try:
            shutil.rmtree(video_dir)
            logger.info(f"Successfully deleted directory item {video_id} from {video_dir}")
            return JSONResponse({"message": "Fichier supprimé avec succès."})
        except FileNotFoundError:
            logger.warning(f"Directory already deleted: {video_dir}")
            return JSONResponse({"error": "Fichier non trouvée."}, status_code=404)
        except Exception as e:
            logger.exception(f"Failed to delete directory item {video_id} from {video_dir}: {e}")
            return JSONResponse({"error": f"Impossible de supprimer le fichier: {e}"}, status_code=500)

    # 2) Zoniet: proberen als los bestand (oude manier, ID met underscores)
    loose_file = _find_loose_file(video_id)
    if loose_file and loose_file.exists() and loose_file.is_file():
        try:
            parent_dir = loose_file.parent
            loose_file.unlink()

            # Verwijder lege mappen boven dit bestand binnen PROCESSED_DIR
            try:
                while parent_dir != settings.PROCESSED_DIR:
                    if any(parent_dir.iterdir()):
                        break
                    to_remove = parent_dir
                    parent_dir = parent_dir.parent
                    to_remove.rmdir()
            except Exception:
                # Niet kritisch als opruimen mislukt
                pass

            logger.info(f"Successfully deleted loose file {video_id} at {loose_file}")
            return JSONResponse({"message": "Fichier supprimé avec succès."})
        except FileNotFoundError:
            logger.warning(f"Loose file already deleted: {loose_file}")
            return JSONResponse({"error": "Fichier non trouvée."}, status_code=404)
        except Exception as e:
            logger.exception(f"Failed to delete loose file {video_id} at {loose_file}: {e}")
            return JSONResponse({"error": f"Impossible de supprimer le fichier: {e}"}, status_code=500)

    logger.warning(f"File or directory not found for ID: {video_id}")
    return JSONResponse({"error": "Fichier non trouvée."}, status_code=404)

@app.put("/api/videos/{video_id}/rename")
async def rename_video(request: Request, video_id: str, new_filename: str = Form(...)):
    """Rename a video, audio, or text file."""
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent renommer des fichiers."}, status_code=403)
    
    # First try to find as a video directory
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
    if video_dir:
        # This is a processed video directory
        try:
            meta_path = video_dir / "metadata.json"
            meta = load_metadata(meta_path)
            meta.filename = new_filename
            save_metadata(meta, meta_path)
            return JSONResponse({"message": "Fichier renommé avec succès."})
        except Exception as e:
            logger.exception("Failed to rename video")
            return JSONResponse({"error": f"Impossible de renommer le fichier: {e}"}, status_code=500)
    
    # If not found as video directory, try to find as loose file
    loose_file = _find_loose_file(video_id)
    if loose_file and loose_file.exists() and loose_file.is_file():
        try:
            # Rename the file
            new_file_path = loose_file.parent / new_filename
            if new_file_path.exists():
                return JSONResponse({"error": "Un fichier avec ce nom existe déjà."}, status_code=400)
            
            loose_file.rename(new_file_path)
            return JSONResponse({"message": "Fichier renommé avec succès."})
        except Exception as e:
            logger.exception("Failed to rename loose file")
            return JSONResponse({"error": f"Impossible de renommer le fichier: {e}"}, status_code=500)
    
    # Also check for audio/text files in directories with info.json
    def _find_file_dir(directory: Path) -> Optional[Path]:
        for item in directory.iterdir():
            if item.is_dir():
                info = _load_video_info(item)
                # Check if this directory has the same ID (directory name)
                if item.name == video_id and (item / "info.json").exists():
                    return item
                found = _find_file_dir(item)
                if found:
                    return found
        return None
    
    file_dir = _find_file_dir(settings.PROCESSED_DIR)
    if file_dir:
        # This is an audio/text file directory
        try:
            info_path = file_dir / "info.json"
            info = json.loads(info_path.read_text(encoding="utf-8"))
            # Update the original filename in info.json
            # The actual file is named "original.ext", so we need to update the stored filename
            info["filename"] = new_filename
            info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
            return JSONResponse({"message": "Fichier renommé avec succès."})
        except Exception as e:
            logger.exception("Failed to rename file directory")
            return JSONResponse({"error": f"Impossible de renommer le fichier: {e}"}, status_code=500)
    
    return JSONResponse({"error": "Fichier non trouvé."}, status_code=404)

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


# ---------- Text-to-Video Generation (Stable Diffusion) ----------
# Hidden feature - only accessible when STABLE_DIFFUSION_ENABLED is True

@app.post("/api/text-to-video")
async def generate_video_from_text(request: Request):
    """Generate a video from text using Stable Diffusion WebUI."""
    # Check if feature is enabled
    if not settings.STABLE_DIFFUSION_ENABLED:
        return JSONResponse(
            {"error": "Text-to-video feature is not enabled."},
            status_code=503
        )
    
    # Only admins can generate videos
    if not can_generate_video(request):
        return JSONResponse(
            {"error": "Seuls les administrateurs peuvent générer des vidéos."},
            status_code=403
        )
    
    try:
        form_data = await request.form()
        text = form_data.get("text", "").strip()
        character_id = form_data.get("character_id") or None
        model_name = form_data.get("model_name") or settings.STABLE_DIFFUSION_MODEL
        folder_path = form_data.get("folder_path") or None
        is_private = form_data.get("is_private", "false").lower() == "true"
        image_per_sentence = form_data.get("image_per_sentence", "true").lower() == "true"
        
        # If character is selected, use its model and enhance prompt
        if character_id:
            character = character_service.get_character(character_id)
            if character:
                if character.status == "completed" and character.model_path:
                    model_name = character.model_path
                # Enhance prompt with character token
                text = character_service.get_character_token_prompt(character, text)
        
        if not text:
            return JSONResponse(
                {"error": "Le texte est requis."},
                status_code=400
            )
        
        # Initialize Stable Diffusion service
        # Options:
        # 1. LOCAL WebUI (default, FREE): http://127.0.0.1:7860
        # 2. External API (PAID, easier): e.g., Diffus.me API
        sd_service = StableDiffusionService(
            api_url=settings.STABLE_DIFFUSION_API_URL,
            timeout=600,
            use_direct=settings.STABLE_DIFFUSION_USE_DIRECT,
            direct_model=settings.STABLE_DIFFUSION_DIRECT_MODEL,
            use_external_api=settings.STABLE_DIFFUSION_USE_EXTERNAL_API,
            external_api_url=settings.STABLE_DIFFUSION_EXTERNAL_API_URL,
            external_api_key=settings.STABLE_DIFFUSION_EXTERNAL_API_KEY
        )
        
        # Check connection
        if not sd_service.check_connection():
            return JSONResponse(
                {"error": "Stable Diffusion WebUI n'est pas accessible. Vérifiez que le serveur est démarré."},
                status_code=503
            )
        
        # Create job ID
        video_id = str(uuid.uuid4())
        job_store.create_job(video_id, "text-to-video")
        job_store.mark_processing(video_id)
        
        # Process in background
        asyncio.create_task(
            process_text_to_video_job(
                video_id=video_id,
                text=text,
                model_name=model_name,
                folder_path=folder_path,
                is_private=is_private,
                image_per_sentence=image_per_sentence,
                sd_service=sd_service
            )
        )
        
        return JSONResponse({
            "job_id": video_id,
            "message": "Génération de vidéo démarrée."
        })
        
    except Exception as e:
        logger.exception("Error in text-to-video generation")
        return JSONResponse(
            {"error": f"Erreur lors de la génération: {str(e)}"},
            status_code=500
        )


async def process_text_to_video_job(
    *,
    video_id: str,
    text: str,
    model_name: Optional[str],
    folder_path: Optional[str],
    is_private: bool,
    image_per_sentence: bool,
    sd_service: StableDiffusionService
):
    """Process text-to-video generation job."""
    try:
        # Create video directory
        if folder_path:
            video_dir = settings.PROCESSED_DIR / folder_path.replace("/", "\\") / video_id
        else:
            video_dir = settings.PROCESSED_DIR / video_id
        
        video_dir.mkdir(parents=True, exist_ok=True)
        
        # Split text into sentences if needed
        sentences = None
        if image_per_sentence:
            # Simple sentence splitting (can be improved)
            sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
        
        # Generate images
        logger.info(f"Generating images for text-to-video job {video_id}")
        images = await sd_service.generate_images_for_text(
            text=text,
            sentences=sentences,
            model_name=model_name,
            image_per_sentence=image_per_sentence,
            width=settings.STABLE_DIFFUSION_IMAGE_WIDTH,
            height=settings.STABLE_DIFFUSION_IMAGE_HEIGHT,
            steps=settings.STABLE_DIFFUSION_STEPS,
            cfg_scale=settings.STABLE_DIFFUSION_CFG_SCALE
        )
        
        if not images:
            raise RuntimeError("Aucune image générée")
        
        # Create video from images
        video_path = video_dir / "original.mp4"
        logger.info(f"Creating video from {len(images)} images")
        success = await run_in_threadpool(
            create_video_from_images,
            images,
            video_path,
            fps=settings.STABLE_DIFFUSION_FPS,
            duration_per_image=settings.STABLE_DIFFUSION_DURATION_PER_IMAGE
        )
        
        if not success:
            raise RuntimeError("Échec de la création de la vidéo")
        
        # Save metadata
        metadata = VideoMetadata(
            id=video_id,
            filename=f"text_to_video_{video_id}.mp4",
            original_language="fr",  # Default, can be made configurable
            sentence_pairs=[],
            translations={}
        )
        
        meta_path = video_dir / "metadata.json"
        save_metadata(meta_path, metadata)
        
        # Save info.json
        info = {
            "file_type": "video",
            "is_private": is_private,
            "folder_path": folder_path,
            "source": "text-to-video",
            "original_text": text
        }
        info_path = video_dir / "info.json"
        info_path.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
        
        job_store.mark_completed(video_id)
        logger.info(f"Text-to-video job {video_id} completed successfully")
        
    except Exception as e:
        logger.exception(f"Error processing text-to-video job {video_id}")
        job_store.mark_failed(video_id, str(e))


# ---------- Character Management (Dreambooth) ----------

@app.get("/api/characters")
async def list_characters(request: Request):
    """List all characters."""
    # Only admin can view characters
    if not can_manage_characters(request):
        return JSONResponse(
            {"error": "Seuls les administrateurs peuvent voir les personnages."},
            status_code=403
        )
    
    if not character_service:
        return JSONResponse(
            {"error": "Character service is not available."},
            status_code=503
        )
    
    characters = character_service.list_characters()
    return JSONResponse([char.model_dump() for char in characters])


@app.post("/api/characters")
async def create_character(request: Request):
    """Create a new character."""
    if not can_manage_characters(request):
        return JSONResponse(
            {"error": "Seuls les administrateurs peuvent créer des personnages."},
            status_code=403
        )
    
    try:
        form_data = await request.form()
        name = form_data.get("name", "").strip()
        token = form_data.get("token", "").strip()
        description = form_data.get("description", "").strip()
        class_word = form_data.get("class_word", "person").strip()
        
        if not name or not token:
            return JSONResponse(
                {"error": "Le nom et le token sont requis."},
                status_code=400
            )
        
        # Validate token (should be lowercase, alphanumeric + underscore)
        if not token.replace("_", "").isalnum() or not token.islower():
            return JSONResponse(
                {"error": "Le token doit être en minuscules et contenir uniquement des lettres, chiffres et underscores."},
                status_code=400
            )
        
        # Check if token already exists
        existing = character_service.list_characters()
        if any(char.token == token for char in existing):
            return JSONResponse(
                {"error": f"Un personnage avec le token '{token}' existe déjà."},
                status_code=400
            )
        
        character = character_service.create_character(
            name=name,
            token=token,
            description=description,
            class_word=class_word
        )
        
        return JSONResponse(character.model_dump())
        
    except Exception as e:
        logger.exception("Error creating character")
        return JSONResponse(
            {"error": f"Erreur lors de la création: {str(e)}"},
            status_code=500
        )


@app.post("/api/characters/{character_id}/images")
async def upload_character_images(
    request: Request,
    character_id: str,
    files: List[UploadFile] = File(...)
):
    """Upload training images for a character."""
    if not can_manage_characters(request):
        return JSONResponse(
            {"error": "Seuls les administrateurs peuvent télécharger des images pour les personnages."},
            status_code=403
        )
    
    character = character_service.get_character(character_id)
    if not character:
        return JSONResponse(
            {"error": "Personnage non trouvé."},
            status_code=404
        )
    
    if character.status == "training":
        return JSONResponse(
            {"error": "Le personnage est en cours d'entraînement. Attendez la fin de l'entraînement."},
            status_code=400
        )
    
    try:
        import tempfile
        uploaded_files = []
        
        for file in files:
            # Validate file type
            if not file.filename.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                continue
            
            # Save to temporary location
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix) as tmp:
                content = await file.read()
                tmp.write(content)
                uploaded_files.append(Path(tmp.name))
        
        # Add images to character
        count = character_service.add_training_images(character_id, uploaded_files)
        
        # Clean up temp files
        for tmp_file in uploaded_files:
            try:
                tmp_file.unlink()
            except:
                pass
        
        # Reload character to get updated count
        character = character_service.get_character(character_id)
        
        return JSONResponse({
            "message": f"{count} image(s) ajoutée(s).",
            "character": character.model_dump() if character else None
        })
        
    except Exception as e:
        logger.exception("Error uploading character images")
        return JSONResponse(
            {"error": f"Erreur lors du téléchargement: {str(e)}"},
            status_code=500
        )


@app.post("/api/characters/{character_id}/train")
async def train_character_endpoint(request: Request, character_id: str):
    """Start training a character."""
    if not can_manage_characters(request):
        return JSONResponse(
            {"error": "Seuls les administrateurs peuvent entraîner des personnages."},
            status_code=403
        )
    
    if not settings.DREAMBOOTH_ENABLED:
        return JSONResponse(
            {"error": "Dreambooth n'est pas activé."},
            status_code=503
        )
    
    character = character_service.get_character(character_id)
    if not character:
        return JSONResponse(
            {"error": "Personnage non trouvé."},
            status_code=404
        )
    
    if character.status == "training":
        return JSONResponse(
            {"error": "Le personnage est déjà en cours d'entraînement."},
            status_code=400
        )
    
    training_images_dir = character_service.get_training_images_dir(character_id)
    if not training_images_dir.exists() or len(list(training_images_dir.glob("*"))) == 0:
        return JSONResponse(
            {"error": "Aucune image d'entraînement trouvée. Ajoutez des images avant d'entraîner."},
            status_code=400
        )
    
    # Start training in background
    asyncio.create_task(character_service.train_character(character_id))
    
    return JSONResponse({
        "message": "Entraînement démarré.",
        "character_id": character_id
    })


@app.get("/api/characters/{character_id}")
async def get_character(request: Request, character_id: str):
    """Get character details."""
    if not can_manage_characters(request):
        return JSONResponse(
            {"error": "Seuls les administrateurs peuvent voir les personnages."},
            status_code=403
        )
    
    character = character_service.get_character(character_id)
    if not character:
        return JSONResponse(
            {"error": "Personnage non trouvé."},
            status_code=404
        )
    
    return JSONResponse(character.model_dump())


# ---------- Image Generation with ModelsLab Flux 2 Pro ----------

@app.post("/api/generate-image")
async def generate_image(request: Request):
    """Generate an image using ModelsLab Flux 2 Pro Text To Image API."""
    if not is_admin(request):
        return JSONResponse(
            {"error": "Seuls les administrateurs peuvent générer des images."},
            status_code=403
        )
    
    if not settings.MODELLAB_API_KEY:
        return JSONResponse(
            {"error": "ModelsLab API key niet geconfigureerd."},
            status_code=503
        )
    
    try:
        form_data = await request.form()
        prompt = form_data.get("prompt", "").strip()
        width = int(form_data.get("width", 1024))
        height = int(form_data.get("height", 1024))
        
        if not prompt:
            return JSONResponse(
                {"error": "Le prompt est requis."},
                status_code=400
            )
        
        # Valideer dimensies
        if width < 64 or width > 2048 or height < 64 or height > 2048:
            return JSONResponse(
                {"error": "Les dimensions doivent être entre 64 et 2048 pixels."},
                status_code=400
            )
        
        # ModelsLab API call
        api_url = settings.MODELLAB_API_URL
        headers = {
            "Authorization": f"Bearer {settings.MODELLAB_API_KEY}",
            "Content-Type": "application/json"
        }
        
        # ModelsLab API payload format
        payload = {
            "key": settings.MODELLAB_API_KEY,
            "model_id": "flux-2-pro",
            "prompt": prompt,
            "width": str(width),
            "height": str(height),
            "samples": "1",
            "num_inference_steps": "28",
            "guidance_scale": "3.5"
        }
        
        logger.info(f"Generating image with ModelsLab API: prompt={prompt[:50]}..., width={width}, height={height}, url={api_url}")
        
        try:
            response = requests.post(api_url, json=payload, headers=headers, timeout=120)
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"ModelsLab API request failed: {e}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_detail = e.response.json()
                    logger.error(f"API error response: {error_detail}")
                except:
                    logger.error(f"API error response text: {e.response.text}")
            raise
        
        logger.info(f"ModelsLab API response: {result}")
        
        # Check for error status - ModelsLab returns status and message for errors
        if "status" in result:
            status_value = result.get("status")
            # If status is not success/processing/completed, it's likely an error
            if status_value not in ["success", "processing", "completed", "succeeded"]:
                error_msg = result.get("message", f"Unknown error from ModelsLab API. Status: {status_value}")
                logger.error(f"ModelsLab API error: {error_msg}, Status: {status_value}")
                return JSONResponse(
                    {"error": f"Erreur de l'API ModelsLab: {error_msg}"},
                    status_code=500
                )
            
            # If status is processing, the API might return an ID to check later
            if status_value == "processing" and "id" in result:
                # For async processing, we'd need to poll for the result
                # For now, return an error asking to check the API documentation
                logger.warning(f"ModelsLab API returned processing status with ID: {result.get('id')}")
                return JSONResponse(
                    {"error": "L'API retourne un statut 'processing'. La génération d'images peut être asynchrone. Vérifiez la documentation de l'API."},
                    status_code=500
                )
        
        # Haal de afbeelding data op - ModelsLab kan verschillende response formats hebben
        image_bytes = None
        
        # Format 1: Direct image URL in response
        if "output" in result and isinstance(result["output"], list) and len(result["output"]) > 0:
            image_url = result["output"][0]
            img_response = requests.get(image_url, timeout=30)
            img_response.raise_for_status()
            image_bytes = img_response.content
        # Format 2: Check if message contains image URL
        elif "message" in result and isinstance(result["message"], str) and (result["message"].startswith("http") or result["message"].startswith("https")):
            image_url = result["message"]
            img_response = requests.get(image_url, timeout=30)
            img_response.raise_for_status()
            image_bytes = img_response.content
        # Format 3: Image URL in status field (dict)
        elif "status" in result and isinstance(result["status"], dict) and "output" in result["status"]:
            output = result["status"]["output"]
            if isinstance(output, list) and len(output) > 0:
                image_url = output[0]
            elif isinstance(output, str):
                image_url = output
            else:
                image_url = None
            
            if image_url:
                img_response = requests.get(image_url, timeout=30)
                img_response.raise_for_status()
                image_bytes = img_response.content
        # Format 4: Base64 encoded image
        elif "image" in result:
            image_bytes = base64.b64decode(result["image"])
        # Format 5: Standard OpenAI-style format
        elif "data" in result and len(result["data"]) > 0:
            image_data = result["data"][0]
            if "url" in image_data:
                image_url = image_data["url"]
                img_response = requests.get(image_url, timeout=30)
                img_response.raise_for_status()
                image_bytes = img_response.content
            elif "b64_json" in image_data:
                image_bytes = base64.b64decode(image_data["b64_json"])
        # Format 6: Check if status contains the image URL directly
        elif "status" in result and isinstance(result["status"], str) and result["status"].startswith("http"):
            image_url = result["status"]
            img_response = requests.get(image_url, timeout=30)
            img_response.raise_for_status()
            image_bytes = img_response.content
        
        if not image_bytes:
            # Log the full response for debugging
            logger.error(f"Could not extract image from ModelsLab API response. Full response: {json.dumps(result, indent=2)}")
            error_msg = f"Impossible d'extraire l'image de la réponse de l'API. Format: {list(result.keys())}"
            if "message" in result:
                error_msg += f" - Message: {result['message']}"
            if "status" in result:
                error_msg += f" - Status: {result['status']}"
            # Include full response in error for debugging
            return JSONResponse(
                {
                    "error": error_msg,
                    "api_response": result  # Include full response for debugging
                },
                status_code=500
            )
            
            # Sla de afbeelding op in een tijdelijk bestand
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                tmp.write(image_bytes)
                image_path = tmp.name
            
            # Return base64 encoded image for display
            image_base64 = base64.b64encode(image_bytes).decode('utf-8')
            
            return JSONResponse({
                "success": True,
                "image": f"data:image/png;base64,{image_base64}",
                "image_path": image_path
            })
        
        logger.error(f"No image data in ModelsLab API response: {result}")
        return JSONResponse(
            {"error": "Aucune donnée d'image dans la réponse de l'API."},
            status_code=500
        )
        
    except requests.exceptions.RequestException as e:
        logger.exception("Error calling ModelsLab API")
        error_msg = str(e)
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_detail = e.response.json()
                error_msg = f"{error_msg}: {error_detail}"
            except:
                error_msg = f"{error_msg}: {e.response.text}"
        return JSONResponse(
            {"error": f"Erreur lors de l'appel à l'API ModelsLab: {error_msg}"},
            status_code=500
        )
    except Exception as e:
        logger.exception("Unexpected error generating image")
        return JSONResponse(
            {"error": f"Erreur inattendue: {str(e)}"},
            status_code=500
        )


@app.get("/api/generated-image/{image_id}")
async def get_generated_image(request: Request, image_id: str):
    """Download a generated image."""
    if not is_admin(request):
        return JSONResponse(
            {"error": "Seuls les administrateurs peuvent télécharger des images."},
            status_code=403
        )
    
    # In een echte implementatie zou je de image_id gebruiken om het bestand op te halen
    # Voor nu gebruiken we een eenvoudige implementatie
    image_path = Path(tempfile.gettempdir()) / image_id
    if image_path.exists():
        return FileResponse(image_path, media_type="image/png")
    else:
        return JSONResponse(
            {"error": "Image non trouvée."},
            status_code=404
        )


@app.post("/api/generate-video")
async def generate_video(request: Request):
    """Generate a video using ModelsLab Video Fusion API."""
    if not is_admin(request):
        return JSONResponse(
            {"error": "Seuls les administrateurs peuvent générer des vidéos."},
            status_code=403
        )
    
    if not settings.MODELLAB_API_KEY:
        return JSONResponse(
            {"error": "ModelsLab API key niet geconfigureerd."},
            status_code=503
        )
    
    try:
        form_data = await request.form()
        prompt = form_data.get("prompt", "").strip()
        duration = int(form_data.get("duration", 8))
        
        if not prompt:
            return JSONResponse(
                {"error": "La description de la scène est requise."},
                status_code=400
            )
        
        if duration not in [4, 8, 12]:
            return JSONResponse(
                {"error": "La durée doit être 4, 8 ou 12 secondes."},
                status_code=400
            )
        
        # Process reference images
        reference_images = {}
        
        # Style image
        style_file = form_data.get("style_image")
        if style_file and hasattr(style_file, 'filename') and style_file.filename:
            style_data = await _save_uploaded_image(style_file, "style")
            if style_data:
                reference_images["style"] = [style_data["data"]]
        
        # Character images
        character_names = form_data.getlist("character_name[]")
        character_files = form_data.getlist("character_image[]")
        if character_names and character_files:
            characters_dict = {}
            for name, char_file in zip(character_names, character_files):
                if name and name.strip() and hasattr(char_file, 'filename') and char_file.filename:
                    char_data = await _save_uploaded_image(char_file, f"character_{name.strip()}")
                    if char_data:
                        characters_dict[name.strip()] = char_data["data"]
            if characters_dict:
                reference_images["characters"] = characters_dict
        
        # Environment image
        environment_file = form_data.get("environment_image")
        if environment_file and hasattr(environment_file, 'filename') and environment_file.filename:
            env_data = await _save_uploaded_image(environment_file, "environment")
            if env_data:
                reference_images["environment"] = [env_data["data"]]
        
        # Audio settings
        enable_audio = form_data.get("enable_audio", "false").lower() == "true"
        allow_ambient_sound = form_data.get("allow_ambient_sound", "false").lower() == "true"
        disable_music = form_data.get("disable_music", "false").lower() == "true"
        disable_voices = form_data.get("disable_voices", "false").lower() == "true"
        
        # Build payload for ModelsLab Video Fusion API
        # Note: ModelsLab Video Fusion API requires the key in the payload AND in Authorization header (consistent with image API)
        # For video generation, we use Sora-2 model
        payload = {
            "key": settings.MODELLAB_API_KEY,  # API key as request parameter
            "model_id": "sora-2",  # Use Sora-2 model for video generation
            "prompt": prompt,
            "duration": duration,
            "audio": {
                "enable": enable_audio,
                "allow_ambient_sound": allow_ambient_sound,
                "disable_music": disable_music,
                "disable_voices": disable_voices
            }
        }
        
        # Only add reference_images if we have any (don't send empty dict)
        if reference_images:
            payload["reference_images"] = reference_images
        
        logger.info(f"Video generation payload: prompt={prompt[:50]}..., duration={duration}, has_reference_images={bool(reference_images)}")
        
        headers = {
            "Authorization": f"Bearer {settings.MODELLAB_API_KEY}",  # Also include in header for consistency
            "Content-Type": "application/json"
        }
        
        logger.info(f"Generating video with ModelsLab Video Fusion API: prompt={prompt[:50]}..., duration={duration}")
        
        try:
            response = requests.post(
                settings.MODELLAB_VIDEO_API_URL,
                json=payload,
                headers=headers,
                timeout=300  # 5 minutes timeout for video generation
            )
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"ModelsLab Video Fusion API request failed: {e}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_detail = e.response.json()
                    logger.error(f"API error response: {error_detail}")
                except:
                    logger.error(f"API error response text: {e.response.text}")
            return JSONResponse(
                {"error": f"Erreur lors de l'appel à l'API ModelsLab: {str(e)}"},
                status_code=500
            )
        
        logger.info(f"ModelsLab Video Fusion API response: {result}")
        
        # Check for error status - ModelsLab returns status and message for errors (consistent with image API)
        if "status" in result:
            status_value = result.get("status")
            # If status is not success/processing/completed, it's likely an error
            if status_value not in ["success", "processing", "completed", "succeeded", "pending"]:
                error_msg = result.get("message", f"Unknown error from ModelsLab API. Status: {status_value}")
                logger.error(f"ModelsLab Video Fusion API error: {error_msg}, Status: {status_value}")
                return JSONResponse(
                    {"error": f"Erreur de l'API ModelsLab: {error_msg}"},
                    status_code=500
                )
            
            # If status is processing/pending, check if we have a video URL or output already
            # Some APIs return the video URL even when status is 'processing'
            if status_value in ["processing", "pending"]:
                logger.info(f"ModelsLab Video Fusion API returned {status_value} status. Checking for video URL or output...")
                # Continue to check for video URL below - don't return error yet
        
        # Handle response - ModelsLab Video Fusion might return video URL or base64
        video_url = None
        if "video_url" in result:
            video_url = result["video_url"]
        elif "url" in result:
            video_url = result["url"]
        elif "output" in result and isinstance(result["output"], str) and result["output"].startswith("http"):
            video_url = result["output"]
        elif "data" in result and isinstance(result["data"], dict):
            if "video_url" in result["data"]:
                video_url = result["data"]["video_url"]
            elif "url" in result["data"]:
                video_url = result["data"]["url"]
        
        if not video_url:
            # If no direct URL, check for base64 or other formats
            if "video" in result:
                video_data = result["video"]
                if isinstance(video_data, str):
                    if video_data.startswith("http"):
                        video_url = video_data
                    elif video_data.startswith("data:"):
                        # Base64 encoded video
                        return JSONResponse({
                            "success": True,
                            "video": video_data
                        })
        
        if video_url:
            return JSONResponse({
                "success": True,
                "video_url": video_url
            })
        else:
            # If status was processing/pending and we have an ID, return that for polling
            if "status" in result and result.get("status") in ["processing", "pending"] and "id" in result:
                job_id = result.get("id")
                logger.info(f"Video generation is processing. Job ID: {job_id}. Returning job ID for polling.")
                return JSONResponse({
                    "success": False,
                    "status": "processing",
                    "job_id": job_id,
                    "message": "La génération de la vidéo est en cours. Veuillez patienter..."
                })
            
            logger.error(f"Could not extract video from ModelsLab Video Fusion API response. Full response: {json.dumps(result, indent=2)}")
            return JSONResponse(
                {"error": "Impossible d'extraire la vidéo de la réponse de l'API. Format de réponse non reconnu."},
                status_code=500
            )
            
    except Exception as e:
        logger.exception(f"Error generating video: {e}")
        return JSONResponse(
            {"error": f"Erreur lors de la génération de la vidéo: {str(e)}"},
            status_code=500
        )


async def _save_uploaded_image(file: UploadFile, prefix: str) -> Optional[dict]:
    """Save an uploaded image and return base64 encoded data URL."""
    try:
        # Read file content
        content = await file.read()
        
        # Determine MIME type
        mime_type = "image/jpeg"
        if file.filename:
            ext = Path(file.filename).suffix.lower()
            if ext == ".png":
                mime_type = "image/png"
            elif ext == ".gif":
                mime_type = "image/gif"
            elif ext == ".webp":
                mime_type = "image/webp"
        
        # Encode as base64
        base64_data = base64.b64encode(content).decode('utf-8')
        data_url = f"data:{mime_type};base64,{base64_data}"
        
        logger.info(f"Encoded reference image: {prefix}, size: {len(content)} bytes")
        return {"data": data_url, "mime_type": mime_type}
    except Exception as e:
        logger.error(f"Error processing uploaded image: {e}")
        return None


@app.delete("/api/characters/{character_id}")
async def delete_character_endpoint(request: Request, character_id: str):
    """Delete a character."""
    if not can_manage_characters(request):
        return JSONResponse(
            {"error": "Seuls les administrateurs peuvent supprimer des personnages."},
            status_code=403
        )
    
    if character_service.delete_character(character_id):
        return JSONResponse({"message": "Personnage supprimé."})
    else:
        return JSONResponse(
            {"error": "Personnage non trouvé."},
            status_code=404
        )


# ==================== ADMIN MESSAGES ROUTES ====================

@app.post("/api/messages")
async def create_message_to_admin(request: Request):
    """Send a message to admin."""
    try:
        form = await request.form()
        message = form.get("message", "").strip()
        
        if not message:
            return JSONResponse(
                {"error": "Le message ne peut pas être vide."},
                status_code=400
            )
        
        if not message_service:
            return JSONResponse(
                {"error": "Service de messages non disponible."},
                status_code=503
            )
        
        role = get_role_from_request(request) or "viewer"
        
        # Create message
        message_data = await run_in_threadpool(
            message_service.create_message,
            sender_role=role,
            message=message,
            sender_name=None
        )
        
        return JSONResponse({
            "message": "Message envoyé avec succès.",
            "id": message_data["id"]
        })
        
    except Exception as e:
        logger.error(f"Error creating message: {e}")
        return JSONResponse(
            {"error": f"Erreur lors de l'envoi: {str(e)}"},
            status_code=500
        )


@app.get("/api/messages")
async def get_admin_messages(request: Request):
    """Get all admin messages (admin only)."""
    if not can_read_admin_messages(request):
        return JSONResponse(
            {"error": "Seuls les administrateurs peuvent voir les messages."},
            status_code=403
        )
    
    if not message_service:
        return JSONResponse(
            {"error": "Service de messages non disponible."},
            status_code=503
        )
    
    try:
        unread_only = request.query_params.get("unread_only", "false").lower() == "true"
        messages = await run_in_threadpool(
            message_service.get_messages,
            unread_only=unread_only
        )
        return JSONResponse(messages)
    except Exception as e:
        logger.error(f"Error getting messages: {e}")
        return JSONResponse(
            {"error": f"Erreur lors de la récupération des messages: {str(e)}"},
            status_code=500
        )


@app.put("/api/messages/{message_id}/read")
async def mark_message_as_read(request: Request, message_id: str):
    """Mark a message as read (admin only)."""
    if not can_read_admin_messages(request):
        return JSONResponse(
            {"error": "Seuls les administrateurs peuvent marquer les messages comme lus."},
            status_code=403
        )
    
    if not message_service:
        return JSONResponse(
            {"error": "Service de messages non disponible."},
            status_code=503
        )
    
    try:
        success = await run_in_threadpool(
            message_service.mark_as_read,
            message_id=message_id
        )
        
        if success:
            return JSONResponse({"message": "Message marqué comme lu."})
        else:
            return JSONResponse(
                {"error": "Message non trouvé."},
                status_code=404
            )
    except Exception as e:
        logger.error(f"Error marking message as read: {e}")
        return JSONResponse(
            {"error": f"Erreur: {str(e)}"},
            status_code=500
        )


@app.delete("/api/messages/{message_id}")
async def delete_admin_message(request: Request, message_id: str):
    """Delete a message (admin only)."""
    if not can_read_admin_messages(request):
        return JSONResponse(
            {"error": "Seuls les administrateurs peuvent supprimer les messages."},
            status_code=403
        )
    
    if not message_service:
        return JSONResponse(
            {"error": "Service de messages non disponible."},
            status_code=503
        )
    
    try:
        success = await run_in_threadpool(
            message_service.delete_message,
            message_id=message_id
        )
        
        if success:
            return JSONResponse({"message": "Message supprimé."})
        else:
            return JSONResponse(
                {"error": "Message non trouvé."},
                status_code=404
            )
    except Exception as e:
        logger.error(f"Error deleting message: {e}")
        return JSONResponse(
            {"error": f"Erreur: {str(e)}"},
            status_code=500
        )


# ==================== LIVE TRANSLATOR ROUTES ====================

@app.get("/live-translator")
async def live_translator_page(request: Request):
    """Live translator frontend page."""
    return templates.TemplateResponse("live_translator.html", {"request": request})


# ==================== LIVE TRANSLATOR SESSION STATE ====================
# Simple session state for live translator (kan later uitgebreid worden met proper session management)
_live_translator_sessions: Dict[str, Dict] = {}
_last_speaker_timestamp: Optional[float] = None

def _get_session_id(request: Request) -> str:
    """Get or create session ID for live translator."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        session_id = str(uuid.uuid4())
    return session_id

def _get_session_state(session_id: str) -> Dict:
    """Get or create session state."""
    if session_id not in _live_translator_sessions:
        _live_translator_sessions[session_id] = {
            "vorige_zinnen": [],
            "seen_transcriptions": set(),
        }
    return _live_translator_sessions[session_id]

@app.post("/api/translate")
async def live_translate_audio(request: Request, audio: UploadFile = File(...)):
    """Live translate audio - met volledige functionaliteit: context-aware correctie, duplicate detection, audio preprocessing, speaker/interpreter filtering."""
    from .live_translator_service import (
        _apply_subscription_corrections,
        corrigeer_zin_met_context,
        verwijder_ongewenste_transcripties,
        _is_duplicate_transcription,
        _should_filter_interpreter_segment,
        AudioPreprocessingConfig,
        _preprocess_audio_file,
        map_whisper_language_hint,
    )
    from .services import transcribe_audio_whisper
    from .audio_text_services import translate_text
    
    global _last_speaker_timestamp
    
    try:
        form = await request.form()
        session_id = _get_session_id(request)
        session_state = _get_session_state(session_id)
        
        bron_taal = form.get("from", "fr").lower()
        doel_taal = form.get("to", "nl").lower()
        interpreter_lang = form.get("interpreter_lang", "").lower()
        interpreter_lang_hint = map_whisper_language_hint(interpreter_lang) if interpreter_lang else None
        
        # Save audio to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as tmp:
            audio_path = Path(tmp.name)
            content = await audio.read()
            audio_path.write_bytes(content)
        
        try:
            # Audio preprocessing (normalize, filter, trim silence)
            preprocess_config = AudioPreprocessingConfig.from_request(form)
            speech_remains = await run_in_threadpool(
                _preprocess_audio_file, str(audio_path), preprocess_config
            )
            if not speech_remains:
                return JSONResponse({
                    "recognized": "",
                    "corrected": "",
                    "translation": "",
                    "silenceDetected": True,
                })
            
            # Map language codes for Whisper
            whisper_lang = map_whisper_language_hint(bron_taal)
            
            # Transcribe
            whisper_result = await run_in_threadpool(
                transcribe_audio_whisper, audio_path, language=whisper_lang
            )
            
            ruwe_tekst = whisper_result.get("text", "").strip()
            detected_language = whisper_result.get("language")
            segment_timestamp = time.time()
            
            if not ruwe_tekst:
                return JSONResponse({
                    "recognized": "",
                    "corrected": "",
                    "translation": "",
                    "silenceDetected": True,
                })
            
            # Speaker/interpreter filtering
            if interpreter_lang_hint:
                should_filter = await run_in_threadpool(
                    _should_filter_interpreter_segment,
                    detected_language,
                    interpreter_lang_hint,
                    ruwe_tekst,
                    segment_timestamp
                )
                if should_filter:
                    return JSONResponse({
                        "recognized": "",
                        "corrected": "",
                        "translation": "",
                        "silenceDetected": True,
                        "interpreterFiltered": True,
                    })
            
            # Remove unwanted transcriptions
            tekst = await run_in_threadpool(verwijder_ongewenste_transcripties, ruwe_tekst)
            if not tekst:
                return JSONResponse({
                    "recognized": "",
                    "corrected": "",
                    "translation": "",
                    "silenceDetected": True,
                })
            
            # Apply subscription corrections
            tekst = await run_in_threadpool(_apply_subscription_corrections, tekst)
            
            # Context-aware correction with full GPT prompt
            vorige_zinnen = session_state["vorige_zinnen"]
            verbeterde_zin = await run_in_threadpool(
                corrigeer_zin_met_context, tekst, vorige_zinnen
            )
            verbeterde_zin = await run_in_threadpool(
                verwijder_ongewenste_transcripties, verbeterde_zin
            )
            
            if not verbeterde_zin:
                return JSONResponse({
                    "recognized": "",
                    "corrected": "",
                    "translation": "",
                    "silenceDetected": True,
                })
            
            # Duplicate detection
            if await run_in_threadpool(_is_duplicate_transcription, tekst, verbeterde_zin):
                logger.info("Duplicate transcription detected, skipping")
                return JSONResponse({
                    "recognized": "",
                    "corrected": "",
                    "translation": "",
                    "silenceDetected": True,
                })
            
            # Add to seen transcriptions
            from .live_translator_service import _normalize_text_for_dedup, _seen_transcriptions
            norm_recognized = await run_in_threadpool(_normalize_text_for_dedup, tekst)
            norm_corrected = await run_in_threadpool(_normalize_text_for_dedup, verbeterde_zin)
            if norm_recognized:
                _seen_transcriptions.add(norm_recognized)
            if norm_corrected and norm_corrected != norm_recognized:
                _seen_transcriptions.add(norm_corrected)
            
            # Translate
            vertaling = ""
            if verbeterde_zin:
                vertaling = await run_in_threadpool(
                    translate_text, verbeterde_zin, bron_taal, doel_taal
                )
                # Fallback: gebruik gecorrigeerde tekst als vertaling leeg is
                if not vertaling or not vertaling.strip():
                    vertaling = verbeterde_zin
            
            # Update session state
            session_state["vorige_zinnen"].append(verbeterde_zin)
            if len(session_state["vorige_zinnen"]) > 10:
                session_state["vorige_zinnen"] = session_state["vorige_zinnen"][-10:]
            
            return JSONResponse({
                "recognized": tekst,
                "corrected": verbeterde_zin,
                "translation": vertaling,
            })
            
        finally:
            # Cleanup
            if audio_path.exists():
                audio_path.unlink()
                
    except Exception as e:
        logger.error(f"Error in live translate: {e}", exc_info=True)
        return JSONResponse(
            {"error": f"Translation error: {str(e)}"},
            status_code=500
        )


@app.post("/api/speak")
async def live_speak_text(request: Request):
    """Generate TTS audio for live translator."""
    try:
        form = await request.form()
        text = form.get("text", "").strip()
        lang = form.get("lang", "nl").strip() or "nl"
        speak = form.get("speak", "true") == "true"
        
        if not speak:
            return JSONResponse({"error": "Spraakuitvoer is uitgeschakeld"}, status_code=400)
        
        if not text:
            return JSONResponse({"error": "Geen tekst om uit te spreken"}, status_code=400)
        
        # Generate TTS audio using existing service
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            tmp_path = Path(tmp.name)
        
        try:
            await generate_long_tts_audio(text, lang, tmp_path)
            
            # Read the generated audio file
            audio_content = tmp_path.read_bytes()
            
            # Clean up
            tmp_path.unlink()
            
            return Response(
                content=audio_content,
                media_type="audio/mpeg",
                headers={"Content-Disposition": "attachment; filename=tts.mp3"}
            )
        except Exception as e:
            if tmp_path.exists():
                tmp_path.unlink()
            raise e
            
    except Exception as e:
        logger.error(f"Error generating TTS: {e}")
        return JSONResponse(
            {"error": f"TTS service error: {str(e)}"},
            status_code=500
        )
