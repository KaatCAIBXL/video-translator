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
    "pt-pt": "Portugais (Angola/Portugal)",
    "pt-br": "Portugais (Brésil)",
    "fi": "Finnois",
    "ln": "Lingala",
}

DEEPL_LANG_MAP: Dict[str, str] = {
    "en": "EN",
    "nl": "NL",
    "es": "ES",
    "fr": "FR",
    "sv": "SV",
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
    "pt-br",
    "pt-pt",
]


def get_language_options() -> List[LanguageOption]:
    options: List[LanguageOption] = []
    for code in AVAILABLE_LANGUAGE_CODES:
        provider = "deepl" if code in SUPPORTED_DEEPL else "ai"
        label = LANGUAGE_LABELS.get(code, code.upper())
        options.append(LanguageOption(code=code, label=label, provider=provider))
    return options
