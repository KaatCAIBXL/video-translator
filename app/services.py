import json
import uuid
import subprocess
from pathlib import Path
from typing import List, Dict

import requests
from openai import OpenAI

from .config import settings
from .models import Segment, TranslationSegment, VideoMetadata

client = OpenAI(api_key=settings.OPENAI_API_KEY)


# ---------- Hulp: padbeheer ----------

def ensure_dirs():
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    settings.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


def extract_audio(video_path: Path, audio_path: Path):
    """
    Extract audio from video using ffmpeg.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(audio_path),
    ]
    subprocess.run(cmd, check=True)


# ---------- Whisper transcriptie ----------

def transcribe_audio_whisper(audio_path: Path) -> Dict:
    """
    Gebruik OpenAI Whisper (whisper-1) om audio te transcriberen.
    We vragen 'verbose_json' zodat we segmenten met start/eindtijd krijgen.
    """
    with open(audio_path, "rb") as f:
        transcription = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json"
        )
    # transcription is een OpenAI object; we converteren naar dict
    return transcription.to_dict()


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
    data = resp.json()
    return data["translations"][0]["text"]


def translate_text_ai(text: str, target_lang: str) -> str:
    """
    Gebruik OpenAI om te vertalen naar talen die DeepL niet ondersteunt
    (bv. Lingala / Tshiluba).
    target_lang: 'ln' of 'lu' of iets dergelijks.
    """
    lang_name = {
        "ln": "Lingala",
        "lu": "Tshiluba",
    }.get(target_lang, target_lang)

    response = client.responses.create(
        model="gpt-4o-mini",
        instructions=f"Je bent een professionele vertaler. Vertaal exact naar {lang_name}. Geen uitleg, enkel de vertaling.",
        input=text,
    )
    return response.output_text


def translate_segments(
    sentence_pairs: List[Segment], target_langs: List[str]
) -> Dict[str, List[TranslationSegment]]:
    """
    Vertaal alle segmenten naar alle geselecteerde talen.
    max 2 talen volgens jouw wens.
    """
    result: Dict[str, List[TranslationSegment]] = {}

    for lang in target_langs:
        lang = lang.lower()
        translated_list: List[TranslationSegment] = []

        for seg in sentence_pairs:
            if lang in SUPPORTED_DEEPL:
                translated_text = translate_text_deepl(seg.text, lang)
            else:
                translated_text = translate_text_ai(seg.text, lang)

            translated_list.append(
                TranslationSegment(
                    start=seg.start,
                    end=seg.end,
                    text=translated_text,
                    language=lang,
                )
            )
        result[lang] = translated_list

    return result


# ---------- VTT ondertitels ----------

def _format_timestamp(seconds: float) -> str:
    """
    VTT tijdformaat: HH:MM:SS.mmm
    """
    ms = int((seconds - int(seconds)) * 1000)
    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    s2 = s % 60
    return f"{h:02}:{m:02}:{s2:02}.{ms:03}"


def generate_vtt(segments: List[TranslationSegment], out_path: Path):
    """
    Schrijf WebVTT bestand met de vertaalde segmenten.
    """
    lines = ["WEBVTT", ""]
    for idx, seg in enumerate(segments, start=1):
        start = _format_timestamp(seg.start)
        end = _format_timestamp(seg.end)
        lines.append(str(idx))
        lines.append(f"{start} --> {end}")
        lines.append(seg.text)
        lines.append("")  # lege regel

    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---------- Dubbing (audio vervangen) ----------

def tts_for_language(text: str, lang: str) -> bytes:
    """
    hier koppel je je eigen TTS-systeem.
    - voor es/en/nl/pt/fi kun je bv. edge-tts of ElevenLabs gebruiken
    - voor 'ln' (Lingala) of 'lu' (Tshiluba) je custom API met je key

    Nu: stub -> jij moet deze functie koppelen aan je bestaande TTS-code.
    """
    # TODO: verbind met jouw bestaande TTS (reading.py / reading_fonetical.py)
    raise NotImplementedError("Koppel hier jouw TTS-engine in tts_for_language().")


def generate_dub_audio(
    translated_segments: List[TranslationSegment], lang: str, dub_audio_path: Path
):
    """
    Heel simpele versie: concat alle TTS-audio stukjes achter elkaar.
    In praktijk kun je per segment TTS doen en dan met ffmpeg concat.
    Hier doen we het (voor demo) als één grote TTS-call op volledig script.
    """
    full_text = " ".join(seg.text for seg in translated_segments)
    audio_bytes = tts_for_language(full_text, lang)

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
