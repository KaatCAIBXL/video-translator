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

    # Stable Diffusion settings
    # Option 1: Use Stable Diffusion WebUI API (local, no external API needed, FREE)
    STABLE_DIFFUSION_API_URL: str = os.getenv("STABLE_DIFFUSION_API_URL", "http://127.0.0.1:7860")
    STABLE_DIFFUSION_MODEL: str = os.getenv("STABLE_DIFFUSION_MODEL", "")  # Dreambooth model name
    STABLE_DIFFUSION_ENABLED: bool = os.getenv("STABLE_DIFFUSION_ENABLED", "false").lower() == "true"
    # Option 2: Use diffusers library directly (alternative, no WebUI needed)
    STABLE_DIFFUSION_USE_DIRECT: bool = os.getenv("STABLE_DIFFUSION_USE_DIRECT", "false").lower() == "true"
    STABLE_DIFFUSION_DIRECT_MODEL: str = os.getenv("STABLE_DIFFUSION_DIRECT_MODEL", "runwayml/stable-diffusion-v1-5")
    # Option 3: Use external API service (e.g., Diffus.me - PAID, but easier setup)
    STABLE_DIFFUSION_USE_EXTERNAL_API: bool = os.getenv("STABLE_DIFFUSION_USE_EXTERNAL_API", "false").lower() == "true"
    STABLE_DIFFUSION_EXTERNAL_API_URL: str = os.getenv("STABLE_DIFFUSION_EXTERNAL_API_URL", "https://api.diffus.me/v3")
    STABLE_DIFFUSION_EXTERNAL_API_KEY: str = os.getenv("STABLE_DIFFUSION_EXTERNAL_API_KEY", "")
    STABLE_DIFFUSION_IMAGE_WIDTH: int = int(os.getenv("STABLE_DIFFUSION_IMAGE_WIDTH", "512"))
    STABLE_DIFFUSION_IMAGE_HEIGHT: int = int(os.getenv("STABLE_DIFFUSION_IMAGE_HEIGHT", "512"))
    STABLE_DIFFUSION_STEPS: int = int(os.getenv("STABLE_DIFFUSION_STEPS", "20"))
    STABLE_DIFFUSION_CFG_SCALE: float = float(os.getenv("STABLE_DIFFUSION_CFG_SCALE", "7.0"))
    STABLE_DIFFUSION_FPS: float = float(os.getenv("STABLE_DIFFUSION_FPS", "2.0"))
    STABLE_DIFFUSION_DURATION_PER_IMAGE: float = float(os.getenv("STABLE_DIFFUSION_DURATION_PER_IMAGE", "2.0"))

    # Dreambooth settings
    DREAMBOOTH_ENABLED: bool = os.getenv("DREAMBOOTH_ENABLED", "false").lower() == "true"
    DREAMBOOTH_PATH: Path = Path(os.getenv("DREAMBOOTH_PATH", str(BASE_DIR / "dreambooth")))
    DREAMBOOTH_BASE_MODEL: str = os.getenv("DREAMBOOTH_BASE_MODEL", "")  # Path to base model .ckpt file
    DREAMBOOTH_REGULARIZATION_IMAGES: str = os.getenv("DREAMBOOTH_REGULARIZATION_IMAGES", "")  # Path to regularization images
    DREAMBOOTH_MAX_TRAINING_STEPS: int = int(os.getenv("DREAMBOOTH_MAX_TRAINING_STEPS", "3000"))
    DREAMBOOTH_LEARNING_RATE: float = float(os.getenv("DREAMBOOTH_LEARNING_RATE", "1.0e-06"))
    DREAMBOOTH_SAVE_EVERY_X_STEPS: int = int(os.getenv("DREAMBOOTH_SAVE_EVERY_X_STEPS", "500"))

    UPLOAD_DIR: Path = BASE_DIR / "data" / "uploads"
    PROCESSED_DIR: Path = BASE_DIR / "data" / "processed"
    CHARACTERS_DIR: Path = BASE_DIR / "data" / "characters"

settings = Settings()
