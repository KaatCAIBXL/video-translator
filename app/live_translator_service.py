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


# ==================== TEXT NORMALIZATION & BLACKLIST ====================

def _strip_diacritics(value: str) -> str:
    """Remove diacritics from text for normalization."""
    normalized = unicodedata.normalize("NFD", value)
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


def _normalize_blacklist_text(value: str) -> str:
    """Normalize text for blacklist matching."""
    stripped = _strip_diacritics(value.lower())
    stripped = re.sub(r"[^a-z0-9]+", " ", stripped)
    return stripped.strip()


NORMALIZED_BLACKLIST = [
    _normalize_blacklist_text(fragment)
    for fragment in ONGEWENSTE_TRANSCRIPTIES
    if _normalize_blacklist_text(fragment)
]

BLACKLIST_TOKEN_COMBOS = [
    ("sous titres", "amara"),
    ("sous titres", "communaut"),
    ("subtitles", "amara"),
    ("subtitles", "community"),
    ("subtitulos", "amara"),
    ("subtitulos", "comunidad"),
    ("ondertitels", "amara"),
    ("merci", "regarde"),
    ("merci", "video"),
]


def _contains_emoji(text: str) -> bool:
    """Check if text contains emoji characters."""
    import unicodedata
    for char in text:
        if unicodedata.category(char) == "So":
            return True
    return False


def _has_meaningful_transcript_content(text: str) -> bool:
    """Check if text has meaningful content for transcription."""
    if not text or not text.strip():
        return False
    
    # Filter out emoji-only or emoji-heavy content
    if _contains_emoji(text):
        # Allow text with emoji if there's substantial text content
        text_only = re.sub(r'[^\w\s]', '', text)
        if len(text_only.strip()) < 3:
            return False
    
    # Check if text is mostly punctuation or whitespace
    text_chars = re.sub(r'[\s\W]', '', text)
    if len(text_chars) < 2:
        return False
    
    return True


def verwijder_ongewenste_transcripties(tekst: str) -> str:
    """Remove unwanted transcriptions (subtitles metadata, closing phrases, etc.)."""
    if not tekst:
        return ""
    
    tekst_lower = tekst.lower()
    
    # Check for subtitle-related phrases
    if "ondertiteld" in tekst_lower or "ondertiteling" in tekst_lower or ("ondertitels" in tekst_lower and "amara" in tekst_lower):
        return ""
    
    # Check for common closing phrases
    tekst_stripped = tekst_lower.strip()
    # French closing phrases
    if tekst_stripped in ["merci.", "merci", "merci. au revoir.", "merci. au revoir", "ciao !", "ciao!", "ciao", "a bientôt", "a bientot", "à bientôt", "à bientot", "merci et à la prochaine fois !", "merci et à la prochaine fois", "merci et à la prochaine fois.", "merci beaucoup", "merci beaucoup.", "je vous remercie", "je vous remercie.", "je te remercie", "je te remercie."]:
        return ""
    # Dutch closing phrases
    if tekst_stripped in ["bedankt.", "bedankt", "bedankt. tot ziens.", "bedankt. tot ziens", "tot ziens.", "tot ziens", "dag!", "dag", "dankjewel en tot de volgende keer!", "dankjewel en tot de volgende keer", "dank u wel.", "dank u wel", "dank u"]:
        return ""
    # Check for "501" (common subtitle error code)
    if tekst_stripped == "501":
        return ""
    # Check for "Sous-titrage ST" or "Ondertiteling ST"
    if "sous-titrage st" in tekst_lower or "ondertiteling st" in tekst_lower:
        return ""
    
    # Check for single-word sentences
    tekst_zonder_punctuatie = re.sub(r'[^\w\s]', '', tekst_stripped).strip()
    woorden = tekst_zonder_punctuatie.split()
    if len(woorden) == 1:
        return ""
    
    opgeschoond = tekst
    for fragment in ONGEWENSTE_TRANSCRIPTIES:
        patroon = re.compile(
            rf"\s*['\"\"'']*{re.escape(fragment)}['\"\"'']*\s*",
            flags=re.IGNORECASE,
        )
        opgeschoond = patroon.sub(" ", opgeschoond)
    
    opgeschoond = re.sub(r"\s{2,}", " ", opgeschoond).strip()
    if not _has_meaningful_transcript_content(opgeschoond):
        return ""
    
    normalized_content = _normalize_blacklist_text(opgeschoond)
    for fragment_norm in NORMALIZED_BLACKLIST:
        if fragment_norm and fragment_norm in normalized_content:
            return ""
    
    for needle_a, needle_b in BLACKLIST_TOKEN_COMBOS:
        if needle_a in normalized_content and needle_b in normalized_content:
            return ""
    
    # Aggressive check: if "sous" + "titres" appear together in any form, reject
    if "sous" in normalized_content and "titres" in normalized_content:
        return ""
    
    return opgeschoond


# ==================== CONTEXT-AWARE CORRECTION ====================

def corrigeer_zin_met_context(nieuwe_zin: str, vorige_zinnen: List[str]) -> str:
    """Context-aware text correction using GPT with full prompt like in traductionimpact3."""
    if not nieuwe_zin.strip():
        return nieuwe_zin
    
    context = " ".join(vorige_zinnen[-3:]) if vorige_zinnen else ""
    
    # Try to load correction instructions file
    instructies_correctie = "(Geen instructies gevonden.)"
    try:
        instructies_path = Path(__file__).resolve().parent.parent / "instructies_correctie.txt"
        if instructies_path.exists():
            instructies_correctie = instructies_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"Kon instructies_correctie.txt niet laden: {e}")
    
    prompt = f"""

BELANGRIJK: Als de originele zin een correcte zin is, mag je die niet veranderen.
ENKEL als je merkt dat iets onlogisch is, incorrect is, of als je een Bijbelvers tegenkomt, mag je aanpassingen doen.

KRITIEK: HERHAAL NOOIT tekst uit de context! De context is alleen bedoeld om te begrijpen wat er gezegd wordt, NIET om tekst uit de context opnieuw te gebruiken of toe te voegen aan de nieuwe zin.

Opdracht 1: Als je een Bijbeltekst uit een erkende vertaling herkent, herstel die nauwkeurig.

Opdracht 2: Als de zin een gebed bevat, pas de regels toe uit:
{instructies_correctie}

Opdracht 3: Als je een zin tegenkomt met "Ondertitels ..." of "...bedankt om te ..." in eender welke taal,
vervang dit door een lege string "". Met andere woorden: dit moet weg.

Opdracht 4: Als je een '.' tegenkomt, laat die staan. Voeg nooit extra zinnen toe!

Opdracht 5: CRITIEK - Onlogische zinnen en speech-to-text fouten:
- Als de zin HEEL onlogisch is en duidelijk niet past in de context, dan heeft speech-to-text het waarschijnlijk verkeerd verstaan.
- Als je onlogische woorden tegenkomt die niet in de context passen, probeer deze te vervangen door woorden met dezelfde klanken die WEL in de context passen.
- Gebruik de context ALLEEN om te begrijpen wat er bedoeld werd, NIET om tekst uit de context te kopiëren of herhalen.
- Pas dit toe op ALLE onlogische woorden.
- Als je echt niet kunt raden wat er bedoeld werd en de zin is compleet onlogisch, geef dan een lege string "" terug (wis de zin volledig).
- HERHAAL NOOIT woorden of zinnen die al in de context staan, ook niet als je denkt dat het logisch is.

Geef alleen de gecorrigeerde zin terug die natuurlijk klinkt, zonder uitleg. Gebruik ALLEEN de woorden uit de nieuwe zin, niet uit de context.

Geef NOOIT opmerkingen. Enkel vertaling of niets.
Context: "{context}"
Nieuwe zin: "{nieuwe_zin}"
"""
    
    client = get_audio_openai_client()
    if client is None:
        logger.warning("Geen OpenAI-client beschikbaar voor contextuele correctie.")
        return nieuwe_zin
    
    try:
        response = client.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Fout bij contextuele correctie: {e}")
        return nieuwe_zin


# ==================== DUPLICATE DETECTION ====================

def _normalize_text_for_dedup(text: str) -> str:
    """Normalize text for duplicate detection."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text.lower())
    # Remove punctuation and normalize whitespace
    cleaned = re.sub(r"[^\w\s]", "", normalized)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _is_duplicate_transcription(recognized: str, corrected: str) -> bool:
    """Check if this transcription has been seen before."""
    global _seen_transcriptions
    
    norm_recognized = _normalize_text_for_dedup(recognized)
    norm_corrected = _normalize_text_for_dedup(corrected)
    
    if not norm_recognized and not norm_corrected:
        return False
    
    # Check exact matches
    if norm_recognized and norm_recognized in _seen_transcriptions:
        return True
    if norm_corrected and norm_corrected != norm_recognized and norm_corrected in _seen_transcriptions:
        return True
    
    # Check substring matches (overlap detection)
    for seen in _seen_transcriptions:
        if norm_recognized and len(norm_recognized) > 10 and norm_recognized in seen:
            return True
        if norm_corrected and len(norm_corrected) > 10 and norm_corrected in seen:
            return True
    
    return False


# ==================== AUDIO PREPROCESSING ====================

def _to_float(value: Optional[str], default: float) -> float:
    """Convert string to float with default fallback."""
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _to_int(value: Optional[str], default: int) -> int:
    """Convert string to int with default fallback."""
    try:
        return int(float(value)) if value is not None else default
    except (TypeError, ValueError):
        return default


def _to_bool(value: Optional[str], default: bool) -> bool:
    """Convert string to bool with default fallback."""
    if value is None:
        return default
    value = value.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass
class AudioPreprocessingConfig:
    """Configuration options for preparing raw microphone uploads."""
    
    normalize_audio: bool = True
    low_pass_cutoff: Optional[int] = 3000
    silence_threshold_dbfs: float = -50.0  # Minder agressief: -50 dBFS in plaats van -40 (luistert beter naar zachte spraak)
    min_silence_duration_ms: int = 500  # Langere stilte nodig voordat we trimmen (was 300ms)
    silence_padding_ms: int = 200  # Meer padding om spraak niet weg te snijden (was 120ms)
    
    @classmethod
    def from_request(cls, form_data) -> "AudioPreprocessingConfig":
        """Create config from request form data."""
        normalize_value = form_data.get("normalize")
        if normalize_value is None:
            normalize_value = form_data.get("normalizeAudio")
        cutoff_value = form_data.get("lowPassCutoff")
        cutoff = _to_int(cutoff_value, 3000) if cutoff_value not in (None, "") else 3000
        if cutoff is not None and cutoff <= 0:
            cutoff = None
        return cls(
            normalize_audio=_to_bool(normalize_value, True),
            low_pass_cutoff=cutoff,
            silence_threshold_dbfs=_to_float(form_data.get("silenceThreshold"), -50.0),  # Minder agressief
            min_silence_duration_ms=_to_int(form_data.get("minSilenceMs"), 500),  # Langere stilte nodig
            silence_padding_ms=_to_int(form_data.get("silencePaddingMs"), 200),  # Meer padding
        )


def _trim_silence(segment: AudioSegment, config: AudioPreprocessingConfig) -> AudioSegment:
    """Return segment without leading/trailing silence based on config settings.
    
    Uses more conservative trimming at the end to preserve speech that might be
    quieter or have trailing silence, improving transcription quality.
    """
    nonsilent_ranges = detect_nonsilent(
        segment,
        min_silence_len=max(config.min_silence_duration_ms, 1),
        silence_thresh=config.silence_threshold_dbfs,
    )
    if not nonsilent_ranges:
        return AudioSegment.silent(duration=0)
    
    # Use standard padding at the start
    start = max(nonsilent_ranges[0][0] - config.silence_padding_ms, 0)
    
    # Use more generous padding at the end to preserve trailing speech
    # This helps with transcription quality at the end of segments
    end_padding = config.silence_padding_ms * 2  # Double padding at the end
    end = min(nonsilent_ranges[-1][1] + end_padding, len(segment))
    
    if start >= end:
        return AudioSegment.silent(duration=0)
    
    # Ensure minimum segment length to help Whisper with transcription
    MIN_SEGMENT_LENGTH_MS = 800  # Minimum 800ms voor betere transcriptie (was 500ms)
    trimmed = segment[start:end]
    if len(trimmed) < MIN_SEGMENT_LENGTH_MS and len(segment) >= MIN_SEGMENT_LENGTH_MS:
        # If trimmed segment is too short but original has enough content,
        # use a more conservative trim (keep more of the original)
        center = (start + end) // 2
        half_min = MIN_SEGMENT_LENGTH_MS // 2
        start = max(0, center - half_min)
        end = min(len(segment), center + half_min)
        trimmed = segment[start:end]
    
    # Als de trimmed segment nog steeds te kort is, gebruik het originele segment (geen trimming)
    # Dit voorkomt dat we spraak verliezen
    if len(trimmed) < 300 and len(segment) >= 300:
        return segment  # Geen trimming als het te kort wordt
    
    return trimmed


def _preprocess_audio_file(path: str, config: AudioPreprocessingConfig) -> bool:
    """Apply normalization, filtering and silence trimming to path.
    
    Returns True when speech remains after trimming, otherwise False.
    """
    try:
        sound = AudioSegment.from_file(path)
        if config.normalize_audio:
            sound = normalize(sound)
        if config.low_pass_cutoff:
            sound = low_pass_filter(sound, cutoff=config.low_pass_cutoff)
        
        trimmed = _trim_silence(sound, config)
        if len(trimmed) == 0:
            return False
        
        trimmed.export(path, format="wav")
        return True
    except Exception as e:
        logger.error(f"Error preprocessing audio file {path}: {e}")
        return False


# ==================== SPEAKER/INTERPRETER FILTERING ====================

def _should_filter_interpreter_segment(
    detected_language: Optional[str],
    interpreter_lang_hint: Optional[str],
    ruwe_tekst: str,
    segment_timestamp: Optional[float] = None
) -> bool:
    """Determine if a segment should be filtered as interpreter speech.
    
    Uses both language-based and timing/frequency heuristics.
    """
    is_interpreter_segment = False
    
    # 1) Strong filter: detected language = interpreter language
    if detected_language and interpreter_lang_hint:
        detected_lang_normalized = detected_language.lower().strip()
        interpreter_lang_normalized = interpreter_lang_hint.lower().strip()
        if detected_lang_normalized == interpreter_lang_normalized:
            is_interpreter_segment = True
    
    # 2) Extra heuristic based on timing/frequency
    #    If there's no language info or it seems unreliable, use interval.
    global _last_speaker_timestamp
    if not is_interpreter_segment and _last_speaker_timestamp is not None and segment_timestamp:
        delta = segment_timestamp - _last_speaker_timestamp
        # Short, choppy sentences close together are often interpreter fragments.
        # We only filter very short fragments to avoid losing real content.
        word_count = len(ruwe_tekst.split())
        if 0.3 <= delta <= 4.0 and word_count <= 8:
            is_interpreter_segment = True
    
    if is_interpreter_segment:
        logger.info(
            f"Segment gefilterd als tolk-fragment "
            f"(interpreter_lang={interpreter_lang_hint}, detected={detected_language})"
        )
        return True
    
    # This is considered speaker speech; update reference time
    if segment_timestamp:
        _last_speaker_timestamp = segment_timestamp
    
    return False

