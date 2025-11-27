"""Language configuration shared between services and frontend."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class LanguageOption:
    code: str
    label: str
    provider: str  # "deepl" or "ai"


LANGUAGE_LABELS: Dict[str, str] = {
    "en": "Anglais",
    "nl": "Néerlandais",
    "es": "Espagnol",
    "fr": "Français",
    "sv": "Suédois",
    "fi": "Finnois",
    "pt-pt": "Portugais (Angola/Portugal)",
    "pt-br": "Portugais (Brésil)",
    "ln": "Lingala",
    "lua": "Tshiluba",
    "kg": "Kikongo (Kituba)",
    "mg": "Malagasy",
}

DEEPL_LANG_MAP: Dict[str, str] = {
    "en": "EN",
    "nl": "NL",
    "es": "ES",
    "fr": "FR",
    "sv": "SV",
    "fi": "FI",
    "pt-pt": "PT-PT",
    "pt-br": "PT-BR",
}

SUPPORTED_DEEPL = set(DEEPL_LANG_MAP.keys())

# volgorde waarin de frontend de keuzes toont
AVAILABLE_LANGUAGE_CODES: List[str] = [
    "en",
    "nl",
    "fr",
    "es",
    "sv",
    "fi",
    "pt-br",
    "pt-pt",
    "ln",
    "lua",
    "kg",
    "mg",
]


LANGUAGES_WITHOUT_DUBBING = set()  # All Bantu languages (Lingala, Tshiluba, Kituba, Malagasy) now supported via ElevenLabs

def get_language_options() -> List[LanguageOption]:
    options: List[LanguageOption] = []
    for code in AVAILABLE_LANGUAGE_CODES:
        provider = "deepl" if code in SUPPORTED_DEEPL else "ai"
        label = LANGUAGE_LABELS.get(code, code.upper())
        options.append(LanguageOption(code=code, label=label, provider=provider))
    return options
