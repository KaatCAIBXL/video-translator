import asyncio
import logging
import os
import tempfile
import zipfile
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pathlib import Path
from typing import List, Optional
import shutil
import uuid

from .config import settings
from .services import (
    ensure_dirs,
    extract_audio,
    transcribe_audio_whisper,
    build_sentence_pairs,
    translate_segments,
    generate_vtt,
    generate_dub_audio,
    replace_video_audio,
    save_metadata,
    load_metadata,
)
from .models import VideoListItem, VideoMetadata
from .job_store import job_store, JobStatus
from starlette.background import BackgroundTask

app = FastAPI()
ensure_dirs()

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

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


def _find_original_video(video_dir: Path) -> Optional[Path]:
    for candidate in video_dir.iterdir():
        if candidate.name.startswith("original"):
            return candidate
    return None



# ---------- Frontend ----------

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ---------- API: lijst video's ----------

@app.get("/api/videos", response_model=List[VideoListItem])
async def list_videos():
    items: List[VideoListItem] = []

    for video_dir in settings.PROCESSED_DIR.iterdir():
        if not video_dir.is_dir():
            continue
        meta = _load_video_metadata(video_dir)
        if meta is None:
            continue

        subtitles = list(meta.translations.keys())

        # beschikbare dubs: check of er een video_dub_{lang}.mp4 is
        dubs = []
        for lang in meta.translations.keys():
            if (video_dir / f"video_dub_{lang}.mp4").exists():
                dubs.append(lang)
        audio_tracks = []
        for lang in meta.translations.keys():
            if (video_dir / f"dub_{lang}.mp3").exists():
                audio_tracks.append(lang)


        items.append(
            VideoListItem(
                id=meta.id,
                filename=meta.filename,
                available_subtitles=subtitles,
                available_dubs=dubs,
                 available_audio=audio_tracks,
            )
        )

    return items


# ---------- API: upload + verwerken ----------

@app.post("/api/upload")
async def upload_video(
    languages: List[str] = Form(...),  # max. 2 talen aanvinken in frontend
    file: UploadFile = File(...)
):
    if len(languages) == 0 or len(languages) > 2:
        return JSONResponse(
            {"error": "Please select one or two target languages"},
            status_code=400,
        )

    video_id = str(uuid.uuid4())
    ext = Path(file.filename).suffix
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
            languages=list(languages),
            original_filename=file.filename,
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
):
    warnings: List[str] = []
    job_store.mark_processing(video_id)

    try:
        await run_in_threadpool(extract_audio, video_path, audio_path)

        whisper_result = await run_in_threadpool(transcribe_audio_whisper, audio_path)
        original_lang = whisper_result.get("language", "unknown")

        sentence_pairs = build_sentence_pairs(whisper_result)
        translations, translation_warnings = await run_in_threadpool(
            translate_segments, sentence_pairs, languages

        )
        warnings.extend(translation_warnings)

        if not translations:
            raise RuntimeError("Translations failed for all requested languages.")

        for lang, segs in translations.items():
            vtt_path = video_dir / f"subs_{lang}.vtt"
            await run_in_threadpool(generate_vtt, segs, vtt_path)

        for lang, segs in translations.items():
            dub_audio_path = video_dir / f"dub_{lang}.mp3"
            try:
                await generate_dub_audio(segs, lang, dub_audio_path)
                dub_video_path = video_dir / f"video_dub_{lang}.mp4"
                await run_in_threadpool(
                    replace_video_audio, video_path, dub_audio_path, dub_video_path
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

        # 7. metadata opslaan
        meta = VideoMetadata(
            id=video_id,
            filename=original_filename,
            original_language=original_lang,
            sentence_pairs=sentence_pairs,
            translations=translations,
        )
        await run_in_threadpool(save_metadata, meta, meta_path)
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
async def get_original_video(video_id: str):
    video_dir = settings.PROCESSED_DIR / video_id
    if not video_dir.exists():
        return JSONResponse({"error": "Video not found"}, status_code=404)

    original_path = _find_original_video(video_dir)
    if original_path is None:
        return JSONResponse({"error": "Original video not found"}, status_code=404)

    meta = _load_video_metadata(video_dir)
    filename = meta.filename if meta else original_path.name
    return FileResponse(original_path, filename=filename)


@app.get("/videos/{video_id}/dub/{lang}")
async def get_dubbed_video(video_id: str, lang: str):
    video_dir = settings.PROCESSED_DIR / video_id
    if not video_dir.exists():
        return JSONResponse({"error": "Video not found"}, status_code=404)

    dub_path = video_dir / f"video_dub_{lang}.mp4"
    if not dub_path.exists():
        return JSONResponse({"error": "Dubbed video not found"}, status_code=404)

    meta = _load_video_metadata(video_dir)
    original_path = _find_original_video(video_dir)
    base_stem = _video_base_stem(meta, dub_path if original_path is None else original_path)
    filename = f"{base_stem}_dub_{lang}.mp4"
    return FileResponse(dub_path, filename=filename)


@app.get("/videos/{video_id}/audio/{lang}")
async def get_dub_audio(video_id: str, lang: str):
    video_dir = settings.PROCESSED_DIR / video_id
    if not video_dir.exists():
        return JSONResponse({"error": "Video not found"}, status_code=404)

    audio_path = video_dir / f"dub_{lang}.mp3"
    if not audio_path.exists():
        return JSONResponse({"error": "Dubbed audio not found"}, status_code=404)

    meta = _load_video_metadata(video_dir)
    original_path = _find_original_video(video_dir)
    base_stem = _video_base_stem(meta, audio_path if original_path is None else original_path)
    filename = f"{base_stem}_dub_{lang}.mp3"
    return FileResponse(audio_path, filename=filename, media_type="audio/mpeg")
        


@app.get("/videos/{video_id}/subs/{lang}")
async def get_subtitles(video_id: str, lang: str):
    video_dir = settings.PROCESSED_DIR / video_id
    if not video_dir.exists():
        return JSONResponse({"error": "Video not found"}, status_code=404)

    subs_path = video_dir / f"subs_{lang}.vtt"
    if not subs_path.exists():
        return JSONResponse({"error": "Subtitles not found"}, status_code=404)

    meta = _load_video_metadata(video_dir)
    original_path = _find_original_video(video_dir)
    base_stem = _video_base_stem(meta, subs_path if original_path is None else original_path)
    filename = f"{base_stem}_{lang}.vtt"
    return FileResponse(subs_path, media_type="text/vtt", filename=filename)


@app.get("/videos/{video_id}/package/{lang}")
async def download_video_package(video_id: str, lang: str, include_audio: bool = False):
    video_dir = settings.PROCESSED_DIR / video_id
    if not video_dir.exists():
        return JSONResponse({"error": "Video not found"}, status_code=404)

    original_path = _find_original_video(video_dir)
    if original_path is None:
        return JSONResponse({"error": "Original video not found"}, status_code=404)

    subs_path = video_dir / f"subs_{lang}.vtt"
    if not subs_path.exists():
        return JSONResponse({"error": "Subtitles not found"}, status_code=404)

    audio_path = video_dir / f"dub_{lang}.mp3"
    if include_audio and not audio_path.exists():
        return JSONResponse({"error": "Requested audio track not found"}, status_code=404)

    meta = _load_video_metadata(video_dir)
    original_filename = meta.filename if meta else original_path.name
    base_stem = Path(original_filename).stem

    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
        tmp_path = Path(tmp_file.name)

    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(original_path, arcname=original_filename)
        archive.write(subs_path, arcname=f"{base_stem}_{lang}.vtt")
        if include_audio and audio_path.exists():
            archive.write(audio_path, arcname=f"{base_stem}_{lang}.mp3")

    package_name = (
        f"{base_stem}_{lang}_with_audio.zip" if include_audio else f"{base_stem}_{lang}_subtitles.zip"
    )

    return FileResponse(
        tmp_path,
        filename=package_name,
        media_type="application/zip",
        background=BackgroundTask(
            lambda path=str(tmp_path): os.path.exists(path) and os.remove(path)
        ),
    )
        
