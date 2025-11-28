import json
import logging
import math
import subprocess
import tempfile
import time
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import requests
from openai import InternalServerError, OpenAI, RateLimitError

from .config import settings
from .models import Segment, TranslationSegment, VideoMetadata
from .languages import DEEPL_LANG_MAP, SUPPORTED_DEEPL, LANGUAGE_LABELS
import edge_tts
from edge_tts import exceptions as edge_tts_exceptions

_openai_client: Optional[OpenAI] = None
_TRANSCRIBE_MAX_ATTEMPTS = 3
_TRANSCRIBE_INITIAL_BACKOFF = 1.0
_RETRYABLE_WHISPER_ERRORS = (InternalServerError, RateLimitError)
logger = logging.getLogger(__name__)

DEFAULT_TTS_VOICE = "en-US-GuyNeural"
VOICE_PREFERENCES: Dict[str, Tuple[str, ...]] = {
    "nl": (
        "nl-NL-MaartenNeural",
        "nl-NL-ColetteNeural",
        "nl-BE-ArnaudNeural",
    ),
    "en": (
        DEFAULT_TTS_VOICE,
        "en-US-JennyNeural",
        "en-GB-RyanNeural",
        "en-AU-WilliamNeural",
    ),
    "es": ("es-ES-AlvaroNeural",),
    "it": ("it-IT-GiuseppeNeural",),
    "fr": ("fr-FR-HenriNeural",),
    "de": ("de-DE-ConradNeural",),
    "sv": ("sv-SE-MattiasNeural",),
    "pt-br": ("pt-BR-AntonioNeural",),
    "pt-pt": ("pt-PT-DuarteNeural",),
    "fi": ("fi-FI-HarriNeural",),

    # Voor Lingala/Tshiluba kiezen we bv. een Franse stem
    # (omdat die vaak beter met Afrikaanse namen/klanken omgaat)
    "ln": ("fr-FR-DeniseNeural",),
}


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

# ---------- Audio metadata helpers ----------

def get_audio_stream_start_offset(video_path: Path) -> float:
    """Bepaal de offset van de eerste audiostream in de originele video."""

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=start_time",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "Kon audio-offset voor %s niet bepalen: %s", video_path, exc
        )
        return 0.0

    value = result.stdout.strip()
    if not value:
        return 0.0

    try:
        offset = float(value)
    except ValueError:
        return 0.0

    if not math.isfinite(offset):
        return 0.0

    return max(0.0, offset)




# ---------- Whisper transcriptie ----------
def _build_initial_prompt(previous_texts: List[str], max_length: int = 200) -> Optional[str]:
    """Build an initial prompt from previous transcriptions to help Whisper with transcription."""
    if not previous_texts:
        return None
    
    # Use the last few transcriptions as context (most relevant)
    recent_context = previous_texts[-3:] if len(previous_texts) >= 3 else previous_texts
    prompt = " ".join(recent_context).strip()
    
    if len(prompt) > max_length:
        # Take the end of the prompt (most recent context)
        prompt = prompt[-max_length:]
    
    return prompt if prompt else None


def _transcribe_whisper_file(client: OpenAI, path: Path, initial_prompt: Optional[str] = None) -> Dict:
    """Transcribe audio file with Whisper, optionally using context from previous transcriptions."""
    attempts = max(1, _TRANSCRIBE_MAX_ATTEMPTS)
    backoff = max(0.0, _TRANSCRIBE_INITIAL_BACKOFF)
    last_exc: Optional[Exception] = None

    for attempt in range(1, attempts + 1):
        try:
            with open(path, "rb") as f:
                request_kwargs = {
                    "model": "whisper-1",
                    "file": f,
                    "response_format": "verbose_json",
                    "temperature": 0.0,  # More deterministic, better for consistent quality
                }
                # Use context from previous transcriptions to help Whisper
                if initial_prompt:
                    request_kwargs["prompt"] = initial_prompt[:200]  # Limit length
                
                transcription = client.audio.transcriptions.create(**request_kwargs)
            return transcription.to_dict()
        except _RETRYABLE_WHISPER_ERRORS as exc:
            last_exc = exc
            logger.warning(
                "Whisper transcription attempt %d/%d failed: %s",
                attempt,
                attempts,
                exc,
            )
            if attempt == attempts:
                break
            if backoff > 0:
                time.sleep(backoff)
                backoff *= 2
        except Exception:
            raise

    raise RuntimeError(
        "Whisper transcription failed after multiple OpenAI API errors"
    ) from last_exc

    


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
        return _transcribe_whisper_file(client, audio_path, initial_prompt=None)
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

        for idx, chunk_path in enumerate(chunk_paths):
            # Build initial prompt from previous chunks for better transcription quality
            initial_prompt = _build_initial_prompt(combined_texts) if combined_texts else None
            
            chunk_result = _transcribe_whisper_file(client, chunk_path, initial_prompt=initial_prompt)

            if detected_language is None:
                detected_language = chunk_result.get("language")

            chunk_text = chunk_result.get("text", "")
            combined_texts.append(chunk_text)

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

def build_sentence_segments(
    whisper_result: Dict, base_offset: float = 0.0
) -> List[Segment]:
    """Maak losse Whisper-segmenten met start/stop tijden."""

    segments: List[Segment] = []
    offset = float(base_offset or 0.0)
    
    for raw in whisper_result.get("segments", []):
        text = (raw.get("text") or "").strip()
        if not text:
            continue

        raw_start = float(raw.get("start", 0.0))
        raw_end = float(raw.get("end", raw_start))

        start = raw_start + offset
        end = raw_end + offset

        segments.append(Segment(start=start, end=end, text=text))

    return segments


def _pair_segments(segments: List[Segment]) -> List[Segment]:
    """Groepeer algemene segmenten per twee."""

    pairs: List[Segment] = []
    buffer: List[Segment] = []

    for seg in segments:
        buffer.append(seg)
        if len(buffer) == 2:
            start = buffer[0].start
            end = buffer[-1].end
            text = " ".join(s.text.strip() for s in buffer if s.text)
            pairs.append(Segment(start=start, end=end, text=text))
            buffer = []

    # Als laatste segment overblijft:
    if buffer:
        start = buffer[0].start
        end = buffer[-1].end
        text = " ".join(s.text.strip() for s in buffer if s.text)
        pairs.append(Segment(start=start, end=end, text=text))

    return pairs


# ---------- Vertaling ----------

def filter_amara_segments(
    segments: List[TranslationSegment],
) -> List[TranslationSegment]:
    """Filter out segments that contain 'Amara.org' in any language variant."""
    filtered = []
    for seg in segments:
        if not seg.text or not seg.text.strip():
            continue
        # Check for Amara.org in various language variants (case-insensitive)
        text_lower = seg.text.lower()
        if "amara" in text_lower and "org" in text_lower:
            # Skip this segment - it contains Amara.org reference
            continue
        # Also check for common subtitle attribution phrases
        if any(phrase in text_lower for phrase in [
            "ondertiteling ingediend door",
            "subtitles submitted by",
            "sous-titres soumis par",
            "subtítulos enviados por",
            "sottotitoli inviati da"
        ]):
            # Skip attribution segments
            continue
        filtered.append(seg)
    return filtered


def pair_translation_segments(
    segments: List[TranslationSegment],
) -> List[TranslationSegment]:
    """Groepeer vertaalde segmenten per twee voor we VTT's schrijven."""

    if not segments:
        return []

    pairs: List[TranslationSegment] = []
    buffer: List[TranslationSegment] = []

    for seg in segments:
        if not seg.text or not seg.text.strip():
            continue
        buffer.append(seg)
        if len(buffer) == 2:
            start = buffer[0].start
            end = buffer[-1].end
            text = " ".join(s.text.strip() for s in buffer if s.text)
            language = buffer[0].language
            pairs.append(
                TranslationSegment(
                    start=start,
                    end=end,
                    text=text,
                    language=language,
                )
            )
            buffer = []

    if buffer:
        start = buffer[0].start
        end = buffer[-1].end
        text = " ".join(s.text.strip() for s in buffer if s.text)
        language = buffer[0].language
        pairs.append(
            TranslationSegment(start=start, end=end, text=text, language=language)
        )

    return pairs


def build_sentence_pairs(whisper_result: Dict, base_offset: float = 0.0) -> List[Segment]:
    """Maak paren van Whisper-segmenten voor metadata en subtitles."""

    return _pair_segments(
        build_sentence_segments(whisper_result, base_offset=base_offset)
    )



def translate_text_deepl(text: str, target_lang: str) -> str:
    """
    Vertaal via DeepL API.
    target_lang bv. 'EN', 'NL', 'ES', ...
    """
    if not settings.DEEPL_API_KEY:
        raise RuntimeError("DEEPL_API_KEY ontbreekt in .env")

    last_exc: Optional[Exception] = None
    backoff_seconds = 1.0
    for attempt in range(3):
        try:
            resp = requests.post(
                "https://api.deepl.com/v2/translate",
                data={
                    "auth_key": settings.DEEPL_API_KEY,
                    "text": text,
                    "target_lang": DEEPL_LANG_MAP[target_lang],
                },
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["translations"][0]["text"]
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response else None
            if status == 429:
                # Free DeepL accounts raken snel tegen de limieten aan. We proberen
                # nog een keer met een korte backoff en geven daarna een duidelijke
                # foutmelding zodat de gebruiker weet wat er aan de hand is.
            
                last_exc = exc
                if attempt < 2:
                    time.sleep(backoff_seconds)
                    backoff_seconds *= 2
                    continue
                raise RuntimeError(
                    "DeepL translation limit reached (HTTP 429). "
                    "Wacht even of schakel over op een betalende DeepL licentie."
                ) from exc
                
            raise RuntimeError(
                f"DeepL translation for {target_lang} failed: {exc}"
            ) from exc
        except requests.RequestException as exc:
            last_exc = exc
            break

    raise RuntimeError(
        f"DeepL translation for {target_lang} failed: {last_exc}"
    ) from last_exc


def _remove_language_prefix(text: str, target_lang: str) -> str:
    """Remove language name prefixes like 'Tshiluba:', 'Lingala:', 'Kituba:', etc. from translated text."""
    if not text:
        return text
    
    # Get possible language names that might be used as prefixes
    lang_name = LANGUAGE_LABELS.get(target_lang, target_lang)
    lang_code_upper = target_lang.upper()
    
    # Patterns to remove: "Tshiluba:", "LINGALA:", "LUA:", "Kituba:", "Kikongo:", etc.
    patterns = [
        f"{lang_name}:",
        f"{lang_name.upper()}:",
        f"{lang_name.lower()}:",
        f"{lang_code_upper}:",
        f"{target_lang.upper()}:",
    ]
    
    # Special cases: for Kituba (kg), also check for "Kituba:" and "Kikongo:" separately
    if target_lang.lower() == "kg":
        patterns.extend([
            "Kituba:",
            "KITUBA:",
            "kituba:",
            "Kikongo:",
            "KIKONGO:",
            "kikongo:",
        ])
    
    # Special cases: for Malagasy (mg), also check for "Malagasy:" separately
    if target_lang.lower() == "mg":
        patterns.extend([
            "Malagasy:",
            "MALAGASY:",
            "malagasy:",
        ])
    
    # Special cases: for Yoruba (yo), also check for "Yoruba:" separately
    if target_lang.lower() == "yo":
        patterns.extend([
            "Yoruba:",
            "YORUBA:",
            "yoruba:",
        ])
    
    text_cleaned = text.strip()
    for pattern in patterns:
        # Remove at start of line
        if text_cleaned.startswith(pattern):
            text_cleaned = text_cleaned[len(pattern):].strip()
        # Also check for pattern at start of each line (for multi-line text)
        lines = text_cleaned.split('\n')
        cleaned_lines = []
        for line in lines:
            line_stripped = line.strip()
            for pat in patterns:
                if line_stripped.startswith(pat):
                    line_stripped = line_stripped[len(pat):].strip()
                    break
            cleaned_lines.append(line_stripped)
        text_cleaned = '\n'.join(cleaned_lines)
    
    return text_cleaned.strip()


def translate_text_ai(text: str, target_lang: str) -> str:
    """Use OpenAI to translate into languages that DeepL does not support."""
    lang_name = LANGUAGE_LABELS.get(target_lang, target_lang)
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

    translated = response.output_text
    # Remove any language prefix that the AI might have added
    return _remove_language_prefix(translated, target_lang)


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

def _edge_rate_from_speed(speed_multiplier: float) -> Optional[str]:
    """Map a numeric speed multiplier to an Edge TTS rate string."""

    if speed_multiplier <= 0:
        return None

    delta_pct = (speed_multiplier - 1.0) * 100.0
    if abs(delta_pct) < 0.5:
        return None

    clamped = max(min(delta_pct, 100.0), -50.0)
    rounded = int(round(clamped))
    if rounded == 0:
        return None

    return f"{rounded:+d}%"


async def _edge_tts_to_bytes(text: str, voice: str, rate: Optional[str] = None) -> bytes:
    """
    Helper: roept edge-tts aan en geeft audio terug als bytes.
    """
    kwargs = {"text": text, "voice": voice}
    if rate:
        kwargs["rate"] = rate

    communicate = edge_tts.Communicate(**kwargs)
    audio_chunks = []

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.append(chunk["data"])

    return b"".join(audio_chunks)


def get_elevenlabs_voices(api_key: str) -> List[Dict]:
    """
    Helper: haal alle beschikbare voices op van ElevenLabs.
    Retourneert een lijst met voice dictionaries met 'voice_id' en 'name'.
    """
    url = "https://api.elevenlabs.io/v1/voices"
    headers = {
        "xi-api-key": api_key
    }
    
    response = requests.get(url, headers=headers, timeout=30)
    
    if response.status_code != 200:
        error_msg = response.text
        raise RuntimeError(f"Failed to fetch ElevenLabs voices: {response.status_code} - {error_msg}")
    
    data = response.json()
    return data.get("voices", [])


async def _elevenlabs_tts_to_bytes(text: str, voice_id: str, api_key: str, speed_multiplier: float = 1.0) -> bytes:
    """
    Helper: roept ElevenLabs API aan en geeft audio terug als bytes.
    """
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key
    }
    
    # ElevenLabs stability en similarity settings (optioneel, kan aangepast worden)
    data = {
        "text": text,
        "model_id": "eleven_multilingual_v2",  # Multilingual model voor Lingala
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    }
    
    # Speed multiplier: ElevenLabs gebruikt geen directe speed parameter,
    # maar we kunnen dit later met ffmpeg aanpassen indien nodig
    response = requests.post(url, json=data, headers=headers, timeout=30)
    
    if response.status_code != 200:
        error_msg = response.text
        raise RuntimeError(f"ElevenLabs TTS failed: {response.status_code} - {error_msg}")
    
    return response.content


def _phonetic_for_lingala(text: str) -> str:
    """
    Maak een fonetische versie van de tekst voor Lingala.
    We gebruiken OpenAI om syllabes/klanken zo te herschrijven dat
    een TTS-stem ze verstaanbaar uitspreekt.
    """
    client = get_openai_client()

    try:
        response = client.responses.create(
            model="gpt-4o-mini",
            instructions=(
                  "Zet deze Lingala-tekst om naar een fonetische versie met Latijnse letters "
                "zodat een TTS-stem het begrijpelijk kan uitspreken. Verander niets aan de betekenis. "
                "Geen uitleg, alleen de fonetisch herschreven tekst."
            ),
            input=text,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Phonetic conversion for Lingala failed: {exc}"
        ) from exc
    return response.output_text


async def tts_for_language(text: str, lang: str, speed_multiplier: float = 1.0) -> bytes:
    """Generate TTS audio for the requested language."""

    lang = lang.lower()
    
    # Voor Lingala: gebruik ElevenLabs als API key beschikbaar is
    if lang == "ln" and settings.LINGALA_TTS_API_KEY and settings.LINGALA_ELEVENLABS_VOICE_ID:
        try:
            # Gebruik de originele Lingala tekst (geen fonetische conversie nodig met ElevenLabs)
            audio_bytes = await _elevenlabs_tts_to_bytes(
                text, 
                settings.LINGALA_ELEVENLABS_VOICE_ID, 
                settings.LINGALA_TTS_API_KEY,
                speed_multiplier=speed_multiplier
            )
            # Als speed_multiplier niet 1.0 is, moeten we de audio aanpassen met ffmpeg
            # Voor nu retourneren we de audio zoals die is (speed kan later worden toegepast)
            return audio_bytes
        except Exception as exc:
            logger.warning(
                "ElevenLabs TTS mislukt voor Lingala, val terug op edge-tts: %s",
                exc
            )
            # Fallback naar edge-tts
            pass
    
    # Voor Tshiluba: gebruik ElevenLabs als API key beschikbaar is
    if lang == "lua" and settings.TSHILUBA_TTS_API_KEY and settings.TSHILUBA_ELEVENLABS_VOICE_ID:
        try:
            # Gebruik de originele Tshiluba tekst (geen fonetische conversie nodig met ElevenLabs)
            audio_bytes = await _elevenlabs_tts_to_bytes(
                text, 
                settings.TSHILUBA_ELEVENLABS_VOICE_ID, 
                settings.TSHILUBA_TTS_API_KEY,
                speed_multiplier=speed_multiplier
            )
            # Als speed_multiplier niet 1.0 is, moeten we de audio aanpassen met ffmpeg
            # Voor nu retourneren we de audio zoals die is (speed kan later worden toegepast)
            return audio_bytes
        except Exception as exc:
            logger.warning(
                "ElevenLabs TTS mislukt voor Tshiluba, val terug op edge-tts: %s",
                exc
            )
            # Fallback naar edge-tts
            pass
    
    # Voor Kituba: gebruik ElevenLabs als API key beschikbaar is
    if lang == "kg" and settings.KITUBA_TTS_API_KEY and settings.KITUBA_ELEVENLABS_VOICE_ID:
        try:
            # Gebruik de originele Kituba tekst (geen fonetische conversie nodig met ElevenLabs)
            audio_bytes = await _elevenlabs_tts_to_bytes(
                text, 
                settings.KITUBA_ELEVENLABS_VOICE_ID, 
                settings.KITUBA_TTS_API_KEY,
                speed_multiplier=speed_multiplier
            )
            # Als speed_multiplier niet 1.0 is, moeten we de audio aanpassen met ffmpeg
            # Voor nu retourneren we de audio zoals die is (speed kan later worden toegepast)
            return audio_bytes
        except Exception as exc:
            logger.warning(
                "ElevenLabs TTS mislukt voor Kituba, val terug op edge-tts: %s",
                exc
            )
            # Fallback naar edge-tts
            pass
    
    # Voor Malagasy: gebruik ElevenLabs als API key beschikbaar is
    if lang == "mg" and settings.MALAGASY_TTS_API_KEY and settings.MALAGASY_ELEVENLABS_VOICE_ID:
        try:
            # Gebruik de originele Malagasy tekst (geen fonetische conversie nodig met ElevenLabs)
            audio_bytes = await _elevenlabs_tts_to_bytes(
                text, 
                settings.MALAGASY_ELEVENLABS_VOICE_ID, 
                settings.MALAGASY_TTS_API_KEY,
                speed_multiplier=speed_multiplier
            )
            # Als speed_multiplier niet 1.0 is, moeten we de audio aanpassen met ffmpeg
            # Voor nu retourneren we de audio zoals die is (speed kan later worden toegepast)
            return audio_bytes
        except Exception as exc:
            logger.warning(
                "ElevenLabs TTS mislukt voor Malagasy, val terug op edge-tts: %s",
                exc
            )
            # Fallback naar edge-tts
            pass
    
    # Voor Yoruba: gebruik ElevenLabs als API key beschikbaar is
    if lang == "yo" and settings.YORUBA_TTS_API_KEY and settings.YORUBA_ELEVENLABS_VOICE_ID:
        try:
            # Gebruik de originele Yoruba tekst (geen fonetische conversie nodig met ElevenLabs)
            audio_bytes = await _elevenlabs_tts_to_bytes(
                text, 
                settings.YORUBA_ELEVENLABS_VOICE_ID, 
                settings.YORUBA_TTS_API_KEY,
                speed_multiplier=speed_multiplier
            )
            # Als speed_multiplier niet 1.0 is, moeten we de audio aanpassen met ffmpeg
            # Voor nu retourneren we de audio zoals die is (speed kan later worden toegepast)
            return audio_bytes
        except Exception as exc:
            logger.warning(
                "ElevenLabs TTS mislukt voor Yoruba, val terug op edge-tts: %s",
                exc
            )
            # Fallback naar edge-tts
            pass
    
    # Standaard: gebruik edge-tts
    voices = VOICE_PREFERENCES.get(lang, (DEFAULT_TTS_VOICE,))

    # 2. Fonetik stap voor ln/lu (alleen als we edge-tts gebruiken)
    if lang == "ln":
        phonetic_text = _phonetic_for_lingala(text)
        tts_text = phonetic_text
    else:
        tts_text = text

    # 3. edge-tts async helper aanroepen
    rate = _edge_rate_from_speed(speed_multiplier)

    last_exc: Optional[Exception] = None
    for voice in voices:
        try:
            return await _edge_tts_to_bytes(tts_text, voice, rate=rate)
        except edge_tts_exceptions.NoAudioReceived as exc:
            last_exc = exc
            logger.warning(
                "Edge TTS leverde geen audio voor stem %s (%s), probeer fallback",
                voice,
                lang,
            )
            continue
        except Exception as exc:
            raise RuntimeError(f"TTS-generatie mislukt voor {lang}: {exc}") from exc

    error = last_exc or RuntimeError("No audio received from edge-tts")
    raise RuntimeError(f"TTS-generatie mislukt voor {lang}: {error}") from error

def _calculate_leading_delay_adjustment(
    delays_ms: List[int], required_silence_ms: int
) -> int:
    """Return extra delay to guarantee at least ``required_silence_ms`` silence."""

    if not delays_ms or required_silence_ms <= 0:
        return 0

    first_delay = min(delays_ms)
    if first_delay >= required_silence_ms:
        return 0

    return required_silence_ms - first_delay




async def generate_dub_audio(
    translated_segments: List[TranslationSegment],
    lang: str,
    output_path: Path,
    speed_multiplier: float = 1.0,
    leading_silence: float = 0.0,
) -> Path:
    """Generate a TTS narration track that matches the source timings.

    Every translated segment is converted to speech individually. Each piece of
    audio is delayed so that it starts at the exact same timestamp as the
    corresponding original segment. This keeps the dubbed narration aligned
    with the original pacing while still allowing the speech to end slightly
    earlier when necessary.
    """

    if not translated_segments:
        raise RuntimeError("No translated segments available for dubbing")

    # Filter out Amara.org segments and ensure we process the segments in chronological order
    amara_filtered = filter_amara_segments(translated_segments)
    
    # Ensure we process the segments in chronological order and skip empty
    # entries up front.
    filtered_segments = [
        seg for seg in sorted(amara_filtered, key=lambda s: s.start)
        if seg.text and seg.text.strip()
    ]

    if not filtered_segments:
        raise RuntimeError("Translated segments do not contain any text")

    required_silence_ms = max(0, int(round((leading_silence or 0.0) * 1000)))
    
    # Calculate the actual start time of the first segment (relative to video start)
    first_segment_start_ms = max(0, int(round(filtered_segments[0].start * 1000)))


    with tempfile.TemporaryDirectory(prefix="dub_segments_") as tmpdir:
        segment_audio: List[Tuple[Path, int]] = []
        segment_delays: List[int] = []

        for idx, seg in enumerate(filtered_segments):
            audio_bytes = await tts_for_language(
                seg.text.strip(), lang, speed_multiplier=speed_multiplier
            )
            segment_path = Path(tmpdir) / f"segment_{idx}.mp3"
            segment_path.write_bytes(audio_bytes)
            # Calculate delay relative to the first segment start time
            # This ensures the first segment starts at the same time as the original audio
            delay_ms = max(0, int(round(seg.start * 1000))) - first_segment_start_ms
            segment_audio.append((segment_path, delay_ms))
            segment_delays.append(delay_ms)

        if not segment_audio:
            raise RuntimeError("Failed to create any TTS segments for dubbing")

        # The first segment should start at the original audio start time
        # Add the first segment start time + required silence to all delays
        base_delay_ms = first_segment_start_ms + required_silence_ms

        ffmpeg_cmd = ["ffmpeg", "-y"]
        for path, _ in segment_audio:
            ffmpeg_cmd.extend(["-i", str(path)])

        filter_parts: List[str] = []
        mix_inputs: List[str] = []
        for idx, (_, delay_ms) in enumerate(segment_audio):
            label = f"a{idx}"
            # Apply delay: base_delay ensures first segment starts at correct time
            # delay_ms is already relative to first segment, so add base_delay
            total_delay_ms = delay_ms + base_delay_ms
            filter_parts.append(
                f"[{idx}:a]adelay={total_delay_ms}|{total_delay_ms}[{label}]"
            )
            mix_inputs.append(f"[{label}]")

        mix_filter = "".join(mix_inputs) + f"amix=inputs={len(mix_inputs)}:normalize=0[aout]"
        filter_parts.append(mix_filter)

        filter_complex = "; ".join(filter_parts)
        ffmpeg_cmd.extend(
            [
                "-filter_complex",
                filter_complex,
                "-map",
                "[aout]",
                "-c:a",
                "mp3",
                str(output_path),
            ]
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            subprocess.run(ffmpeg_cmd, check=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Dub audio compositing failed: {exc}") from exc
    return output_path

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
