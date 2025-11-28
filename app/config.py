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
    # Voor Tshiluba / Lingala / Kituba / Malagasy / Yoruba TTS, als je aparte provider gebruikt:
    LINGALA_TTS_API_KEY: str = os.getenv("LINGALA_TTS_API_KEY", "")
    LINGALA_ELEVENLABS_VOICE_ID: str = os.getenv("LINGALA_ELEVENLABS_VOICE_ID", "")
    TSHILUBA_TTS_API_KEY: str = os.getenv("TSHILUBA_TTS_API_KEY", "")
    TSHILUBA_ELEVENLABS_VOICE_ID: str = os.getenv("TSHILUBA_ELEVENLABS_VOICE_ID", "")
    KITUBA_TTS_API_KEY: str = os.getenv("KITUBA_TTS_API_KEY", "")
    KITUBA_ELEVENLABS_VOICE_ID: str = os.getenv("KITUBA_ELEVENLABS_VOICE_ID", "")
    MALAGASY_TTS_API_KEY: str = os.getenv("MALAGASY_TTS_API_KEY", "")
    MALAGASY_ELEVENLABS_VOICE_ID: str = os.getenv("MALAGASY_ELEVENLABS_VOICE_ID", "")
    YORUBA_TTS_API_KEY: str = os.getenv("YORUBA_TTS_API_KEY", "")
    YORUBA_ELEVENLABS_VOICE_ID: str = os.getenv("YORUBA_ELEVENLABS_VOICE_ID", "")

    # ffmpeg tuning
    WHISPER_MAX_UPLOAD_MB: int = int(os.getenv("WHISPER_MAX_UPLOAD_MB", "24"))
    FFMPEG_HWACCEL_ARGS: List[str] = shlex.split(
        os.getenv("FFMPEG_HWACCEL_ARGS", "")
    )

    # Stable Diffusion WebUI settings
    STABLE_DIFFUSION_API_URL: str = os.getenv("STABLE_DIFFUSION_API_URL", "http://127.0.0.1:7860")
    STABLE_DIFFUSION_MODEL: str = os.getenv("STABLE_DIFFUSION_MODEL", "")  # Dreambooth model name
    STABLE_DIFFUSION_ENABLED: bool = os.getenv("STABLE_DIFFUSION_ENABLED", "false").lower() == "true"
    STABLE_DIFFUSION_IMAGE_WIDTH: int = int(os.getenv("STABLE_DIFFUSION_IMAGE_WIDTH", "512"))
    STABLE_DIFFUSION_IMAGE_HEIGHT: int = int(os.getenv("STABLE_DIFFUSION_IMAGE_HEIGHT", "512"))
    STABLE_DIFFUSION_STEPS: int = int(os.getenv("STABLE_DIFFUSION_STEPS", "20"))
    STABLE_DIFFUSION_CFG_SCALE: float = float(os.getenv("STABLE_DIFFUSION_CFG_SCALE", "7.0"))
    STABLE_DIFFUSION_FPS: float = float(os.getenv("STABLE_DIFFUSION_FPS", "2.0"))
    STABLE_DIFFUSION_DURATION_PER_IMAGE: float = float(os.getenv("STABLE_DIFFUSION_DURATION_PER_IMAGE", "2.0"))

    UPLOAD_DIR: Path = BASE_DIR / "data" / "uploads"
    PROCESSED_DIR: Path = BASE_DIR / "data" / "processed"

settings = Settings()
