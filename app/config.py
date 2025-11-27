import os
import shlex
from dotenv import load_dotenv
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

class Settings:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    DEEPL_API_KEY: str = os.getenv("DEEPL_API_KEY", "")
    # Voor Tshiluba / Lingala / Kituba TTS, als je aparte provider gebruikt:
    LINGALA_TTS_API_KEY: str = os.getenv("LINGALA_TTS_API_KEY", "")
    LINGALA_ELEVENLABS_VOICE_ID: str = os.getenv("LINGALA_ELEVENLABS_VOICE_ID", "")
    TSHILUBA_TTS_API_KEY: str = os.getenv("TSHILUBA_TTS_API_KEY", "")
    TSHILUBA_ELEVENLABS_VOICE_ID: str = os.getenv("TSHILUBA_ELEVENLABS_VOICE_ID", "")
    KITUBA_TTS_API_KEY: str = os.getenv("KITUBA_TTS_API_KEY", "")
    KITUBA_ELEVENLABS_VOICE_ID: str = os.getenv("KITUBA_ELEVENLABS_VOICE_ID", "")

    # ffmpeg tuning
    WHISPER_MAX_UPLOAD_MB: int = int(os.getenv("WHISPER_MAX_UPLOAD_MB", "24"))
    FFMPEG_HWACCEL_ARGS: List[str] = shlex.split(
        os.getenv("FFMPEG_HWACCEL_ARGS", "")
    )

    UPLOAD_DIR: Path = BASE_DIR / "data" / "uploads"
    PROCESSED_DIR: Path = BASE_DIR / "data" / "processed"

settings = Settings()
