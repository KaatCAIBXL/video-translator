import logging
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pathlib import Path
from typing import List
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

app = FastAPI()
ensure_dirs()

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")

logger = logging.getLogger(__name__)




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
        meta_path = video_dir / "metadata.json"
        if not meta_path.exists():
            continue

        meta = load_metadata(meta_path)
        subtitles = list(meta.translations.keys())

        # beschikbare dubs: check of er een video_dub_{lang}.mp4 is
        dubs = []
        for lang in meta.translations.keys():
            if (video_dir / f"video_dub_{lang}.mp4").exists():
                dubs.append(lang)

        items.append(
            VideoListItem(
                id=meta.id,
                filename=meta.filename,
                available_subtitles=subtitles,
                available_dubs=dubs,
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
            {"error": "Je moet 1 of 2 talen kiezen."},
            status_code=400,
        )

    video_id = str(uuid.uuid4())
    ext = Path(file.filename).suffix
    video_dir = settings.PROCESSED_DIR / video_id
    video_dir.mkdir(parents=True, exist_ok=True)

    video_path = video_dir / f"original{ext}"
    audio_path = video_dir / "audio.wav"
    meta_path = video_dir / "metadata.json"

    warnings: List[str] = []

    try:
        # sla upload op
        with open(video_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        # 1. audio extracten
        extract_audio(video_path, audio_path)

        # 2. whisper transcriptie
        whisper_result = transcribe_audio_whisper(audio_path)
        original_lang = whisper_result.get("language", "unknown")

        # 3. zin-paren bouwen
        sentence_pairs = build_sentence_pairs(whisper_result)

        # 4. vertalingen
       translations, translation_warnings = translate_segments(
            sentence_pairs, languages
        )
        warnings.extend(translation_warnings)

        if not translations:
            raise RuntimeError("Vertalingen zijn voor geen van de talen gelukt.")
            
        # 5. VTT bestanden per taal
        for lang, segs in translations.items():
            vtt_path = video_dir / f"subs_{lang}.vtt"
            generate_vtt(segs, vtt_path)

        # 6. optioneel: dubbings genereren (hier: meteen doen)
        for lang, segs in translations.items():
            dub_audio_path = video_dir / f"dub_{lang}.mp3"
            try:
                await generate_dub_audio(segs, lang, dub_audio_path)
                dub_video_path = video_dir / f"video_dub_{lang}.mp4"
                replace_video_audio(video_path, dub_audio_path, dub_video_path)
            except NotImplementedError:
                # Als TTS nog niet geïmplementeerd is, slaan we dubbing over
                pass
            except RuntimeError as exc:
                warnings.append(
                    f"Dubbing voor {lang} kon niet worden gemaakt: {exc}"
                )
            except Exception as exc:
                logger.exception("Onverwachte fout bij dubbing voor %s", lang)
                warnings.append(
                    f"Dubbing voor {lang} kon niet worden gemaakt door een onverwachte fout."
                )

        # 7. metadata opslaan
        meta = VideoMetadata(
            id=video_id,
            filename=file.filename,
            original_language=original_lang,
            sentence_pairs=sentence_pairs,
            translations=translations,
        )
        save_metadata(meta, meta_path)

    except RuntimeError as exc:
        logger.warning("Fout bij verwerken van upload: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("Onverwachte fout bij verwerken van upload")
        return JSONResponse(
            {"error": "Er ging iets mis tijdens het verwerken van de video."},
            status_code=500,
        )

    return {"id": video_id, "warnings": warnings}


# ---------- video + ondertitels / dub leveren ----------

@app.get("/videos/{video_id}/original")
async def get_original_video(video_id: str):
    video_dir = settings.PROCESSED_DIR / video_id
    for f in video_dir.iterdir():
        if f.name.startswith("original"):
            return FileResponse(f)
    return JSONResponse({"error": "Video niet gevonden"}, status_code=404)


@app.get("/videos/{video_id}/dub/{lang}")
async def get_dubbed_video(video_id: str, lang: str):
    video_dir = settings.PROCESSED_DIR / video_id
    dub_path = video_dir / f"video_dub_{lang}.mp4"
    if not dub_path.exists():
        return JSONResponse({"error": "Dub niet gevonden"}, status_code=404)
    return FileResponse(dub_path)


@app.get("/videos/{video_id}/subs/{lang}")
async def get_subtitles(video_id: str, lang: str):
    video_dir = settings.PROCESSED_DIR / video_id
    subs_path = video_dir / f"subs_{lang}.vtt"
    if not subs_path.exists():
        return JSONResponse({"error": "Subtitles niet gevonden"}, status_code=404)
    return FileResponse(subs_path, media_type="text/vtt")
