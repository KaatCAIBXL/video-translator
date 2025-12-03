"""
Live translator service - volledige integratie van traductionimpact3 logica.
"""
import asyncio
import logging
import os
import re
import tempfile
import textwrap
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

try:
    import deepl
except ImportError:
    deepl = None

try:
    import whisper
except ImportError:
    whisper = None

import edge_tts
from edge_tts import exceptions as edge_tts_exceptions
from openai import OpenAI
from pydub import AudioSegment
from pydub.exceptions import CouldntDecodeError
from pydub.effects import normalize, low_pass_filter
from pydub.silence import detect_nonsilent

from .config import settings
from .audio_text_services import get_openai_client as get_audio_openai_client, get_deepl_translator as get_audio_deepl_translator

logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================

ONGEWENSTE_TRANSCRIPTIES = [
    "ondertitels ingediend door de amara.org gemeenschap",
    "Sous-titres soumis par la communauté amara.org.",
    "Merci. Au revoir.",
    "Bedankt. Tot ziens.",
    # ... (alle andere uit traductionimpact3)
]

DEFAULT_STEM = "en-US-AriaNeural"
STEMMAP = {
    "nl": "nl-NL-ColetteNeural",
    "fr": "fr-FR-DeniseNeural",
    "en": DEFAULT_STEM,
    "es": "es-ES-ElviraNeural",
    "pt": "pt-BR-FranciscaNeural",
    "fi": "fi-FI-SelmaNeural",
    "sv": "sv-SE-SofieNeural",
}

SUPPORTED_WHISPER_EXTENSIONS = {
    ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm", ".aac", ".amr"
}

WHISPER_LANGUAGE_OVERRIDES = {
    "lingala": "ln",
    "kituba": "kg",
    "kikongo": "kg",
    "tshiluba": "lu",
    "tshi-luba": "lu",
    "baloue": None,
    "dioula": None,
}

DEFAULT_GPT_TRANSLATION_MODEL = "gpt-4"
GPT_TRANSLATION_MODEL_OVERRIDES = {
    "kituba": "gpt-4.1",
    "lingala": "gpt-4.1",
    "tshiluba": "gpt-4.1",
    "malagasy": "gpt-4.1",
}

# ==================== GLOBAL STATE ====================

# Session state per gebruiker (kan later uitgebreid worden met session management)
_session_state: Dict[str, Dict] = {}
_last_speaker_timestamp: Optional[float] = None
_seen_transcriptions: set = set()

# ==================== HELPER FUNCTIONS ====================

def _normalize_language_key(code: Optional[str]) -> str:
    if not code:
        return ""
    normalized = unicodedata.normalize("NFKD", code)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    return ascii_only.strip().lower()


def map_whisper_language_hint(code: Optional[str]) -> Optional[str]:
    key = _normalize_language_key(code)
    if not key:
        return None
    if key in WHISPER_LANGUAGE_OVERRIDES:
        return WHISPER_LANGUAGE_OVERRIDES[key]
    if len(key) == 2:
        return key
    if key.startswith("en-"):
        return "en"
    return None


def select_gpt_translation_model(target_language: Optional[str]) -> str:
    key = _normalize_language_key(target_language)
    if key in GPT_TRANSLATION_MODEL_OVERRIDES:
        return GPT_TRANSLATION_MODEL_OVERRIDES[key]
    return DEFAULT_GPT_TRANSLATION_MODEL


def map_vertaling_taalcode_deepl(taalcode: str) -> str:
    code = taalcode.lower()
    if code in ["en", "en-us"]:
        return "EN-US"
    elif code in ["pt", "pt-br"]:
        return "PT-BR"
    elif code in ["zh", "zh-cn", "zh-hans"]:
        return "ZH"
    else:
        return code.upper()


def _sanitize_tts_text(tekst: Optional[str]) -> str:
    if tekst is None:
        return ""
    return unicodedata.normalize("NFC", tekst).strip()


def _select_stem(taalcode: str) -> str:
    return STEMMAP.get(taalcode.lower(), DEFAULT_STEM)


# ==================== SUBSCRIPTION CORRECTIONS ====================

_subscription_replacements: Optional[List[Tuple[str, str]]] = None


def _load_subscription_replacements() -> List[Tuple[str, str]]:
    """Load corrections from subscription_corrections.txt or use defaults."""
    defaults = [
        ("pape", "Pasteur Anaclet"),
        ("Pape", "Pasteur Anaclet"),
        ("passeur", "pasteur"),
        ("à la clé", "Anaclat"),
        ("anacaap", "Anaclat"),
        ("Anacaap", "Anaclat"),
        ("Aposodic", "Apostolique"),
        ("cette tombe de Dieu", "cet homme de Dieu"),
        ("la piscine de silhouette", "la piscine de Siloé"),
        ("piscine de soirée", "piscine de Siloé"),
        ("Sarah", "Chara"),
        ("Sara", "Chara"),
        ("Beta", "BETACH"),
        ("bèta", "BETACH"),
        ("bêta", "BETACH"),
        ("Bêta", "BETACH"),
        ("Betah", "BETACH"),
        ("Béthane", "BETACH"),
        ("Béthame", "BETACH"),
        ("d'Ecky", "de Bétach"),
        ("D'Ecky", "de Bétach"),
        ("enchantement", "changement"),
    ]
    
    cfg_path = Path(__file__).resolve().parent.parent / "subscription_corrections.txt"
    if not cfg_path.exists():
        return defaults
    
    replacements = []
    try:
        for raw_line in cfg_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "->" not in line:
                continue
            wrong, correct = line.split("->", 1)
            wrong = wrong.strip()
            correct = correct.strip()
            if wrong and correct:
                replacements.append((wrong, correct))
    except Exception as exc:
        logger.warning(f"Kon subscription_corrections.txt niet inlezen, gebruik defaults: {exc}")
        return defaults
    
    return replacements or defaults


def _apply_subscription_corrections(tekst: str) -> str:
    """Apply subscription corrections before AI processing."""
    if not tekst:
        return tekst
    
    global _subscription_replacements
    if _subscription_replacements is None:
        _subscription_replacements = _load_subscription_replacements()
    
    vervangingen = sorted(_subscription_replacements, key=lambda item: len(item[0]), reverse=True)
    resultaat = tekst
    for fout, correct in vervangingen:
        patroon = re.compile(rf"\b{re.escape(fout)}\b")
        resultaat = patroon.sub(correct, resultaat)
    
    return resultaat

# ... (meer functies volgen in volgende edit)

