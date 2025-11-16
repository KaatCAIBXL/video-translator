import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import requests
from openai import OpenAI

from .config import settings
from .models import Segment, TranslationSegment, VideoMetadata
import edge_tts

_openai_client: Optional[OpenAI] = None
logger = logging.getLogger(__name__)

# Whisper verwacht audio chunks als 16 kHz mono PCM. Bij het opsplitsen
# hercoderen we daarom altijd naar deze instellingen. De bitrate daarvan is
# 16.000 samples * 2 bytes = 32.000 bytes per seconde (256 kbit/s). Zelfs als
# de oorspronkelijke video een lagere bitrate heeft, moeten we hiermee
# rekening houden zodat chunks onder de 25 MB limiet blijven.
PCM16_MONO_16KHZ_BYTES_PER_SECOND = 16000 * 2


def get_openai_client() -> OpenAI:
    """Return a lazily initialised OpenAI client.

    We keep the instantiation here to avoid creating a client with an empty
    API key at import time. When the key is missing we raise a RuntimeError so
    the calling code can surface a friendly error to the user instead of a
    generic 500 response.
    """

    global _openai_client

    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY ontbreekt in de configuratie")

    if _openai_client is None:
        _openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)

    return _openai_client



# ---------- Hulp: padbeheer ----------

def ensure_dirs():
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    settings.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

def _build_ffmpeg_cmd(*args: str) -> List[str]:
    """Return a base ffmpeg command with optional hwaccel flags."""

    cmd = ["ffmpeg", "-y"]
    if settings.FFMPEG_HWACCEL_ARGS:
        cmd.extend(settings.FFMPEG_HWACCEL_ARGS)
    cmd.extend(args)
    return cmd

def extract_audio(video_path: Path, audio_path: Path):
    """Extract audio from video using ffmpeg."""

    cmd = _build_ffmpeg_cmd(
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(audio_path),
    )
    subprocess.run(cmd, check=True)


# ---------- Whisper transcriptie ----------
def _transcribe_whisper_file(client: OpenAI, path: Path) -> Dict:
    with open(path, "rb") as f:
        transcription = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
        )
    return transcription.to_dict()


def _get_audio_duration(audio_path: Path) -> float:
    """Bepaal de duur van een audiobestand in seconden met ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Kan de duur van audio ({audio_path}) niet bepalen: {exc}"
        ) from exc

    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0

def _select_chunk_duration(
    file_size_bytes: int,
    duration_seconds: float,
    max_bytes: int,
    default_seconds: int = 600,
) -> int:
    """Pick a chunk length that keeps each piece below Whisper's upload cap."""

    if (
        file_size_bytes <= 0
        or duration_seconds <= 0
        or max_bytes <= 0
    ):
        return max(1, default_seconds)

    if default_seconds <= 0:
        default_seconds = 600

    bytes_per_second = file_size_bytes / duration_seconds
    
    # Tijdens het segmenteren re-encoderen we naar 16 kHz mono PCM. Wanneer het
    # bronbestand een lagere bitrate heeft zou een eenvoudige verhouding
    # chunks kunnen opleveren die alsnog groter dan 25 MB zijn. Door rekening te
    # houden met de feitelijke bitrate van de uiteindelijke chunks, kiezen we
    # een conservatieve waarde en voorkomen we Whisper uploads die sneuvelen op
    # de limiet.
    bytes_per_second = max(bytes_per_second, PCM16_MONO_16KHZ_BYTES_PER_SECOND)
    if bytes_per_second <= 0:
        return default_seconds

    chunk_seconds = int(max_bytes / bytes_per_second)
    return max(1, chunk_seconds)


def _max_upload_bytes() -> int:
    configured = settings.WHISPER_MAX_UPLOAD_MB
    if configured <= 0:
        return 24 * 1024 * 1024
    return configured * 1024 * 1024


def transcribe_audio_whisper(audio_path: Path) -> Dict:
    """Transcribe audio with Whisper, chunking long files transparently."""
    
    client = get_openai_client()
    max_bytes = _max_upload_bytes()


    if audio_path.stat().st_size <= max_bytes:
        return _transcribe_whisper_file(client, audio_path)
    try:
        audio_duration = _get_audio_duration(audio_path)
    except RuntimeError as exc:
        logger.warning("Could not determine audio duration for chunking: %s", exc)
        audio_duration = 0.0

    segment_seconds = _select_chunk_duration(
        audio_path.stat().st_size,
        audio_duration,
        max_bytes,
        default_seconds=600,
    )

    approx_single_chunk = max_bytes / PCM16_MONO_16KHZ_BYTES_PER_SECOND


    logger.info(
        "Audio file %s is larger than %d MB (~%.1f s @16kHz PCM). Splitting into %d second chunks before Whisper processing",
        max_bytes // (1024 * 1024),
        approx_single_chunk,
        segment_seconds,
    )

    combined_segments = []
    combined_texts: List[str] = []
    detected_language: Optional[str] = None
    offset = 0.0

    with tempfile.TemporaryDirectory() as tmpdir:
        chunk_dir = Path(tmpdir)
        chunk_pattern = chunk_dir / "chunk_%03d.wav"

        try:
            subprocess.run(
                    _build_ffmpeg_cmd(
                    "-i",
                    str(audio_path),
                    "-acodec",
                    "pcm_s16le",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    # Door opnieuw te encoderen naar 16 kHz mono PCM houden we de
                    # chunks gegarandeerd onder de limiet van Whisper.
                    "-f",
                    "segment",
                    "-segment_time",
                    str(segment_seconds),
                    str(chunk_pattern),
                    ),
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError("Splitting audio before Whisper processing failed") from exc

        chunk_paths = sorted(chunk_dir.glob("chunk_*.wav"))
        if not chunk_paths:
            raise RuntimeError(
                "Splitting audio before Whisper processing failed: no chunks were produced"
            )

        for chunk_path in chunk_paths:
            chunk_result = _transcribe_whisper_file(client, chunk_path)

            if detected_language is None:
                detected_language = chunk_result.get("language")

            combined_texts.append(chunk_result.get("text", ""))

            chunk_segments = chunk_result.get("segments", []) or []
            if chunk_segments:
                for seg in chunk_segments:
                    seg_copy = dict(seg)
                    seg_copy["start"] = seg_copy.get("start", 0.0) + offset
                    seg_copy["end"] = seg_copy.get("end", 0.0) + offset
                    combined_segments.append(seg_copy)

                offset = combined_segments[-1]["end"]
            else:
                offset += _get_audio_duration(chunk_path)

    return {
        "text": " ".join(part.strip() for part in combined_texts if part).strip(),
        "segments": combined_segments,
        "language": detected_language or "unknown",
    }

def build_sentence_pairs(whisper_result: Dict) -> List[Segment]:
    """
    Neem Whisper 'segments' en groepeer per 2 'zinnen' (segments).
    start = start van eerste, end = end van laatste.
    """
    segments = whisper_result.get("segments", [])
    pairs: List[Segment] = []

    buffer = []
    for seg in segments:
        buffer.append(seg)
        if len(buffer) == 2:
            start = buffer[0]["start"]
            end = buffer[-1]["end"]
            text = " ".join(s["text"].strip() for s in buffer)
            pairs.append(Segment(start=start, end=end, text=text))
            buffer = []

    # Als laatste segment overblijft:
    if buffer:
        start = buffer[0]["start"]
        end = buffer[-1]["end"]
        text = " ".join(s["text"].strip() for s in buffer)
        pairs.append(Segment(start=start, end=end, text=text))

    return pairs


# ---------- Vertaling ----------

DEEPL_LANG_MAP = {
    "es": "ES",
    "en": "EN",
    "nl": "NL",
    "pt": "PT-PT",  # of PT-BR naar keuze
    "fi": "FI",
}

SUPPORTED_DEEPL = set(DEEPL_LANG_MAP.keys())
AI_ONLY_LANGS = {"ln", "lu"}  # Lingala / Tshiluba (je kan codes zelf kiezen)


def translate_text_deepl(text: str, target_lang: str) -> str:
    """
    Vertaal via DeepL API.
    target_lang bv. 'EN', 'NL', 'ES', ...
    """
    if not settings.DEEPL_API_KEY:
        raise RuntimeError("DEEPL_API_KEY ontbreekt in .env")

    try:
        resp = requests.post(
            "https://api-free.deepl.com/v2/translate",
            data={
                "auth_key": settings.DEEPL_API_KEY,
                "text": text,
                "target_lang": DEEPL_LANG_MAP[target_lang],
            },
            timeout=60,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"DeepL translation for {target_lang} failed: {exc}") from exc

    data = resp.json()
    return data["translations"][0]["text"]


def translate_text_ai(text: str, target_lang: str) -> str:
    """Use OpenAI to translate into languages that DeepL does not support."""
    lang_name = {
        "ln": "Lingala",
        "lu": "Tshiluba",
    }.get(target_lang, target_lang)
    client = get_openai_client()

    try:
        response = client.responses.create(
            model="gpt-4o-mini",
            instructions=(
                f"Je bent een professionele vertaler. Vertaal exact naar {lang_name}. Geen uitleg, enkel de vertaling."
            ),
            input=text,
        )
    except Exception as exc:
        raise RuntimeError(f"AI translation for {lang_name} failed: {exc}") from exc

    return response.output_text


def translate_segments(
    sentence_pairs: List[Segment], target_langs: List[str]
) -> Tuple[Dict[str, List[TranslationSegment]], List[str]]:
    """Translate all segments into each requested target language."""
    result: Dict[str, List[TranslationSegment]] = {}
    warnings: List[str] = []

    for lang in target_langs:
        lang = lang.lower()
        translated_list: List[TranslationSegment] = []

        for seg in sentence_pairs:
            try:
                if lang in SUPPORTED_DEEPL:
                    translated_text = translate_text_deepl(seg.text, lang)
                else:
                    translated_text = translate_text_ai(seg.text, lang)
            except RuntimeError as exc:
                warnings.append(f"Vertaling mislukt voor {lang}: {exc}")
                translated_list = []
                break
            except Exception as exc:
                warnings.append(
                    f"Vertaling mislukt voor {lang}: onverwachte fout ({exc})."
                )
                logger.exception("Vertaling mislukt voor %s", lang)
                translated_list = []
                break

            translated_list.append(
                TranslationSegment(
                    start=seg.start,
                    end=seg.end,
                    text=translated_text,
                    language=lang,
                )
            )
        if translated_list:
            result[lang] = translated_list

    return result, warnings


# ---------- VTT ondertitels ----------

def _format_timestamp(seconds: float) -> str:
    """Return a timestamp in VTT format (HH:MM:SS.mmm)."""
    ms = int((seconds - int(seconds)) * 1000)
    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    s2 = s % 60
    return f"{h:02}:{m:02}:{s2:02}.{ms:03}"


def render_vtt_content(segments: List[TranslationSegment]) -> str:
    """Render VTT inhoud zonder het direct naar disk te schrijven."""
    lines = ["WEBVTT", ""]
    for idx, seg in enumerate(segments, start=1):
        start = _format_timestamp(seg.start)
        end = _format_timestamp(seg.end)
        lines.append(str(idx))
        lines.append(f"{start} --> {end}")
        lines.append(seg.text)
        lines.append("")  # lege regel

    return "\n".join(lines)


def generate_vtt(segments: List[TranslationSegment], out_path: Path):
    """
    Schrijf WebVTT bestand met de vertaalde segmenten.
    """
    out_path.write_text(render_vtt_content(segments), encoding="utf-8")


# ---------- Dubbing (audio vervangen) ----------

async def _edge_tts_to_bytes(text: str, voice: str) -> bytes:
    """
    Helper: roept edge-tts aan en geeft audio terug als bytes.
    """
    communicate = edge_tts.Communicate(text=text, voice=voice)
    audio_chunks = []

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.append(chunk["data"])

    return b"".join(audio_chunks)


def _phonetic_for_lingala_tshiluba(text: str, lang: str) -> str:
    """
    Maak een fonetische versie van de tekst voor Lingala / Tshiluba.
    We gebruiken OpenAI om syllabes/klanken zo te herschrijven dat
    een TTS-stem ze verstaanbaar uitspreekt.
    """
    lang_name = "Lingala" if lang == "ln" else "Tshiluba"
    client = get_openai_client()

    try:
        response = client.responses.create(
            model="gpt-4o-mini",
            instructions=(
                f"Zet deze {lang_name}-tekst om naar een fonetische versie met Latijnse letters "
                f"zodat een TTS-stem het begrijpelijk kan uitspreken. Verander niets aan de betekenis. "
                f"Geen uitleg, alleen de fonetisch herschreven tekst."
            ),
            input=text,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Phonetic conversion for {lang_name} failed: {exc}"
        ) from exc
    return response.output_text


async def tts_for_language(text: str, lang: str) -> bytes:
    """Generate TTS audio for the requested language."""

    # 1. stem per taal
    voice_map = {
        "nl": "nl-NL-MaartenNeural",
        "en": "en-US-GuyNeural",
        "es": "es-ES-AlvaroNeural",
        "pt": "pt-BR-AntonioNeural",
        "fi": "fi-FI-HarriNeural",

        # Voor Lingala/Tshiluba kiezen we bv. een Franse stem
        # (omdat die vaak beter met Afrikaanse namen/klanken omgaat)
        "ln": "fr-FR-DeniseNeural",
        "lu": "fr-FR-DeniseNeural",
    }

    lang = lang.lower()
    voice = voice_map.get(lang, "en-US-GuyNeural")

    # 2. Fonetik stap voor ln/lu
    if lang in ["ln", "lu"]:
        phonetic_text = _phonetic_for_lingala_tshiluba(text, lang)
        tts_text = phonetic_text
    else:
        tts_text = text

    # 3. edge-tts async helper aanroepen
    try:
        return await _edge_tts_to_bytes(tts_text, voice)
    except Exception as exc:
        raise RuntimeError(f"TTS-generatie mislukt voor {lang}: {exc}") from exc



async def generate_dub_audio(
    translated_segments: List[TranslationSegment], lang: str, dub_audio_path: Path
):
    """Generate a single TTS track for all translated segments."""
    full_text = " ".join(seg.text for seg in translated_segments)
    audio_bytes = await tts_for_language(full_text, lang)

    with open(dub_audio_path, "wb") as f:
        f.write(audio_bytes)


def replace_video_audio(
    video_path: Path, new_audio_path: Path, output_video_path: Path
):
    """
    ffmpeg: audio vervangen, video stream kopiëren.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(video_path),
        "-i", str(new_audio_path),
        "-c:v", "copy",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        str(output_video_path),
    ]
    subprocess.run(cmd, check=True)


# ---------- Opslag van metadata ----------

def save_metadata(meta: VideoMetadata, meta_path: Path):
    meta_path.write_text(meta.model_dump_json(indent=2), encoding="utf-8")


def load_metadata(meta_path: Path) -> VideoMetadata:
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    return VideoMetadata(**data)
