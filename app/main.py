import asyncio
import logging
import shutil
import tempfile
import uuid
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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
    "subs_per_language",
    "subs_combined",
    "dub_audio",
    "dub_video",
}
DEFAULT_PROCESS_OPTIONS = list(PROCESS_OPTIONS)

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
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "available_languages": LANGUAGE_OPTIONS,
        },
    )


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

        subtitles = [
            lang
            for lang in meta.translations.keys()
            if (video_dir / f"subs_{lang}.vtt").exists()
        ]

        dubs = [
            lang
            for lang in meta.translations.keys()
            if (video_dir / f"video_dub_{lang}.mp4").exists()
        ]

        dub_audios = [
            lang
            for lang in meta.translations.keys()
            if (video_dir / f"dub_audio_{lang}.mp3").exists()
        ]

        combined = []
        for combined_file in video_dir.glob("subs_combined_*.vtt"):
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
                available_subtitles=subtitles,
                available_dubs=dubs,
                available_dub_audios=dub_audios,
                available_combined_subtitles=combined,
            )
        )

    return items


# ---------- API: upload + verwerken ----------

@app.post("/api/upload")
async def upload_video(
    languages: List[str] = Form(...),  # max. 2 talen aanvinken in frontend
    process_options: Optional[List[str]] = Form(None),
    tts_speed_multiplier: float = Form(1.0),
    file: UploadFile = File(...)
):
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
):
    warnings: List[str] = []
    options_set = set(process_options or DEFAULT_PROCESS_OPTIONS)
    create_subtitles = "subs_per_language" in options_set
    create_combined = "subs_combined" in options_set
    create_dub_audio = "dub_audio" in options_set
    create_dub_video = "dub_video" in options_set
    needs_dub_assets = create_dub_audio or create_dub_video
    job_store.mark_processing(video_id)

    try:
        await run_in_threadpool(extract_audio, video_path, audio_path)

        whisper_result = await run_in_threadpool(transcribe_audio_whisper, audio_path)
        original_lang = whisper_result.get("language", "unknown")

        sentence_segments = build_sentence_segments(whisper_result)
        sentence_pairs = build_sentence_pairs(whisper_result)
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

        if create_combined:
            if len(languages) < 2:
                warnings.append("Combined subtitles require two target languages.")
            else:
                try:
                    combined_segments = _build_combined_segments(translations, languages)
                except ValueError as exc:
                    warnings.append(str(exc))
                else:
                    combined_path = video_dir / _combined_subtitle_filename(languages)
                    content = render_vtt_content(combined_segments)
                    combined_path.write_text(content, encoding="utf-8")

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

@app.get("/videos/{video_id}/dub-audio/{lang}")
async def get_dub_audio(video_id: str, lang: str):
    video_dir = settings.PROCESSED_DIR / video_id
    if not video_dir.exists():
        return JSONResponse({"error": "Video not found"}, status_code=404)

    audio_path = video_dir / f"dub_audio_{lang}.mp3"
    if not audio_path.exists():
        return JSONResponse({"error": "Dub audio not found"}, status_code=404)

    meta = _load_video_metadata(video_dir)
    original_path = _find_original_video(video_dir)
    base_stem = _video_base_stem(meta, audio_path if original_path is None else original_path)
    filename = f"{base_stem}_dub_{lang}.mp3"
    return FileResponse(audio_path, media_type="audio/mpeg", filename=filename)

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
    
@app.get("/videos/{video_id}/subs/combined")
async def get_combined_subtitles(video_id: str, langs: Optional[str] = None):
    video_dir = settings.PROCESSED_DIR / video_id
    if not video_dir.exists():
        return JSONResponse({"error": "Video not found"}, status_code=404)

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
