import os
from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

class Settings:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    DEEPL_API_KEY: str = os.getenv("DEEPL_API_KEY", "")
    # Voor Tshiluba / Lingala TTS, als je aparte provider gebruikt:
    LINGALA_TTS_API_KEY: str = os.getenv("LINGALA_TTS_API_KEY", "")
    TSHILUBA_TTS_API_KEY: str = os.getenv("TSHILUBA_TTS_API_KEY", "")

    UPLOAD_DIR: Path = BASE_DIR / "data" / "uploads"
    PROCESSED_DIR: Path = BASE_DIR / "data" / "processed"

settings = Settings()
