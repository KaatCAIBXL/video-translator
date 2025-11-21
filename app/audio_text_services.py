"""
Services voor audio transcriptie, tekst vertaling en text-to-speech.
Gebaseerd op de referentiecode uit Transcriberen.
"""
import asyncio
import json
import logging
import math
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import deepl
except ImportError:
    deepl = None

import edge_tts
from openai import OpenAI
from pydub import AudioSegment

from .config import settings

logger = logging.getLogger(__name__)

# Bantu-talen mapping
BANTU_LANGUAGES = {
    "ln": "LINGALA",
    "lua": "TSHILUBA/LUBA-KASAI",
    "kg": "KITUBA",
    "mg": "MALAGASY",
}

# TTS stemmen mapping
TTS_VOICES: Dict[str, str] = {
    "nl": "nl-NL-MaartenNeural",
    "fr": "fr-FR-DeniseNeural",
    "pt": "pt-BR-AntonioNeural",
    "pt-br": "pt-BR-AntonioNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "es": "es-ES-AlvaroNeural",
    "en": "en-US-GuyNeural",
    "en-us": "en-US-GuyNeural",
    "fi": "fi-FI-HarriNeural",
    "sw": "sw-KE-RafikiNeural",
    "sv": "sv-SE-MattiasNeural",
    "de": "de-DE-ConradNeural",
    "it": "it-IT-GiuseppeNeural",
    # Voor Bantu-talen gebruiken we Franse stemmen als fallback
    "ln": "fr-FR-DeniseNeural",
    "lua": "fr-FR-DeniseNeural",
    "kg": "fr-FR-DeniseNeural",
    "mg": "fr-FR-DeniseNeural",
}

# DeepL taal mapping
DEEPL_LANG_MAP = {
    "nl": "NL",
    "fr": "FR",
    "en": "EN-US",
    "es": "ES",
    "pt": "PT-BR",
    "de": "DE",
    "it": "IT",
    "sv": "SV",
    "fi": "FI",
    "zh": "ZH",
}

# Initialize clients
_openai_client: Optional[OpenAI] = None
_deepl_translator: Optional[deepl.Translator] = None


def get_openai_client() -> OpenAI:
    """Get or create OpenAI client."""
    global _openai_client
    if _openai_client is None:
        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY not configured")
        _openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _openai_client


def get_deepl_translator() -> Optional[deepl.Translator]:
    """Get or create DeepL translator."""
    global _deepl_translator
    if deepl is None:
        return None
    if _deepl_translator is None:
        if settings.DEEPL_API_KEY:
            try:
                _deepl_translator = deepl.Translator(settings.DEEPL_API_KEY)
            except Exception as e:
                logger.warning(f"Failed to initialize DeepL translator: {e}")
                return None
    return _deepl_translator


# ============================================================
# AUDIO SPLITTING
# ============================================================
def split_audio_efficient(audio_path: Path, chunk_length_ms: int = 60000) -> List[Path]:
    """Split audio into chunks of 60 seconds without loading everything into RAM."""
    logger.info("Splitting audio into chunks...")
    
    try:
        sound = AudioSegment.from_file(str(audio_path))
    except MemoryError:
        logger.error("Not enough memory to load audio")
        return []
    except Exception as e:
        logger.error(f"Cannot open audio: {e}")
        return []
    
    chunks = []
    total_length = len(sound)
    num_chunks = math.ceil(total_length / chunk_length_ms)
    
    temp_dir = Path(tempfile.gettempdir())
    for i in range(0, total_length, chunk_length_ms):
        chunk = sound[i:i + chunk_length_ms]
        chunk_name = temp_dir / f"chunk_{i // chunk_length_ms}_{int(time.time())}.wav"
        chunk.export(str(chunk_name), format="wav")
        chunks.append(chunk_name)
    
    logger.info(f"Created {len(chunks)} chunks")
    del sound
    return chunks


# ============================================================
# TRANSCRIPTION (Whisper)
# ============================================================
def transcribe_audio_whisper_api(audio_path: Path, language: str = "fr") -> str:
    """Transcribe audio using Whisper API."""
    try:
        client = get_openai_client()
        with open(audio_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language=language
            )
            return transcript.text.strip()
    except Exception as e:
        logger.error(f"Whisper API error for {audio_path}: {e}")
        return ""


def transcribe_long_audio(audio_path: Path, language: str = "fr") -> str:
    """Split long audio and transcribe each chunk."""
    chunks = split_audio_efficient(audio_path)
    if not chunks:
        return ""
    
    full_text = ""
    total = len(chunks)
    
    for idx, chunk in enumerate(chunks):
        logger.info(f"Processing chunk {idx + 1}/{total}...")
        text = transcribe_audio_whisper_api(chunk, language=language)
        full_text += text + "\n"
        
        # Clean up chunk
        try:
            chunk.unlink()
        except Exception:
            pass
        
        progress = math.ceil(((idx + 1) / total) * 100)
        logger.info(f"{progress}% complete")
    
    return full_text.strip()


# ============================================================
# TEXT IMPROVEMENT (GPT)
# ============================================================
def improve_text_with_ai(text: str, language: str = "fr") -> str:
    """Improve text using GPT-4."""
    prompt = (
        f"Améliore le texte suivant en {language} sans changer le sens. "
        "Corrige grammaire, style, cohérence. "
        "Assurez-vous que les phrases illogiques deviennent logiques. "
        "Les mots étranges ou qui ne correspondent pas au contexte peuvent être remplacés par des mots qui correspondent au contexte et qui ont la même sonorité. "
        "Conserve un ton chrétien, respectueux et spirituel.\n\n"
        f"Texte original:\n{text}\n\nTexte amélioré:"
    )
    
    try:
        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": f"Tu es un correcteur professionnel pour textes religieux en {language}."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.4
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Error improving text: {e}")
        return text


# ============================================================
# TEXT SPLITTING
# ============================================================
def split_text_into_blocks(text: str, max_length: int = 2000) -> List[str]:
    """Split text into blocks of approximately max_length characters, without splitting sentences."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    
    blocks = []
    current_block = ""
    
    for sentence in sentences:
        if len(current_block) + len(sentence) + 1 <= max_length:
            current_block += sentence + " "
        else:
            if current_block.strip():
                blocks.append(current_block.strip())
            current_block = sentence + " "
    
    if current_block.strip():
        blocks.append(current_block.strip())
    
    return blocks


# ============================================================
# TRANSLATION
# ============================================================
def read_bantu_instructions(target_lang: str) -> str:
    """Load optional instruction file per language."""
    mapping = {
        "ln": "instructies_lingala.txt",
        "lua": "instructies_tshiluba.txt",
        "kg": "instructies_kituba.txt",
        "mg": "instructies_malagasy.txt",
    }
    
    fname = mapping.get(target_lang.lower())
    if not fname:
        return ""
    
    # Look for instruction files in the project root or data directory
    possible_paths = [
        Path(__file__).parent.parent / fname,
        settings.PROCESSED_DIR.parent / fname,
    ]
    
    for path in possible_paths:
        if path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except Exception:
                pass
    
    return ""


def translate_with_deepl(text: str, source_lang: str, target_lang: str) -> str:
    """Translate text using DeepL."""
    if deepl is None:
        raise ValueError("DeepL package not installed")
    translator = get_deepl_translator()
    if not translator:
        raise ValueError("DeepL API key not configured")
    
    source_code = DEEPL_LANG_MAP.get(source_lang.lower(), source_lang.upper())
    target_code = DEEPL_LANG_MAP.get(target_lang.lower(), target_lang.upper())
    
    if not target_code:
        raise ValueError(f"Unsupported target language for DeepL: {target_lang}")
    
    blocks = split_text_into_blocks(text)
    translated_blocks = []
    
    for i, block in enumerate(blocks):
        try:
            result = translator.translate_text(
                block,
                source_lang=source_code,
                target_lang=target_code
            )
            translated_blocks.append(result.text)
        except Exception as e:
            logger.error(f"Error translating block {i+1}: {e}")
            translated_blocks.append(block)  # fallback: original text
    
    return "\n\n".join(translated_blocks)


def translate_with_openai(text: str, source_lang: str, target_lang: str) -> str:
    """Translate text using OpenAI (for Bantu languages or when DeepL is not available)."""
    client = get_openai_client()
    language_name = BANTU_LANGUAGES.get(target_lang.lower(), target_lang.upper())
    instructions = read_bantu_instructions(target_lang)
    
    prompt = f"""
Je suis un traducteur professionnel vers la langue: {language_name}.

RÈGLES DE TRADUCTION:
---
{instructions}
---

TÂCHES:
1. Traduisez le texte de {source_lang} vers {language_name}.
2. Utilisez un {language_name} naturel.
3. Donnez UNIQUEMENT la traduction. Pas d'explication, pas d'exemples.

TEXTE:
{text}
"""
    
    try:
        blocks = split_text_into_blocks(text)
        translated_blocks = []
        
        for i, block in enumerate(blocks):
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "Tu es un traducteur professionnel spécialisé dans les langues bantoues (Kituba, Lingála, Tshiluba) et Malagasy. Tu n'utilises PAS de langue mixte. Tu traduis toujours en pur, naturel et correct dans la langue demandée."
                    },
                    {"role": "user", "content": prompt.replace("{text}", block)}
                ],
                temperature=0.3,
            )
            translated_blocks.append(response.choices[0].message.content.strip())
        
        return "\n\n".join(translated_blocks)
    except Exception as e:
        logger.error(f"Error translating with OpenAI: {e}")
        raise


def translate_text(text: str, source_lang: str, target_lang: str) -> str:
    """Translate text using DeepL if available, otherwise OpenAI."""
    target_lang_lower = target_lang.lower()
    
    # Use OpenAI for Bantu languages
    if target_lang_lower in BANTU_LANGUAGES:
        return translate_with_openai(text, source_lang, target_lang)
    
    # Try DeepL first
    translator = get_deepl_translator()
    if translator:
        try:
            return translate_with_deepl(text, source_lang, target_lang)
        except Exception as e:
            logger.warning(f"DeepL translation failed, falling back to OpenAI: {e}")
    
    # Fallback to OpenAI
    return translate_with_openai(text, source_lang, target_lang)


# ============================================================
# TEXT-TO-SPEECH
# ============================================================
async def generate_tts_audio(text: str, language: str, output_path: Path) -> None:
    """Generate TTS audio using edge_tts."""
    voice = TTS_VOICES.get(language.lower(), TTS_VOICES["en"])
    
    try:
        communicate = edge_tts.Communicate(text=text, voice=voice)
        await communicate.save(str(output_path))
        logger.info(f"TTS audio generated: {output_path}")
    except Exception as e:
        logger.error(f"Error generating TTS audio: {e}")
        raise


async def generate_long_tts_audio(text: str, language: str, output_path: Path) -> None:
    """Generate TTS audio for long texts by splitting into blocks."""
    blocks = split_text_into_blocks(text, max_length=5000)  # Smaller blocks for TTS
    
    temp_dir = Path(tempfile.gettempdir())
    audio_segments = []
    
    for i, block in enumerate(blocks):
        if not block.strip():
            continue
        
        temp_audio = temp_dir / f"tts_chunk_{i}_{int(time.time())}.mp3"
        await generate_tts_audio(block, language, temp_audio)
        audio_segments.append(temp_audio)
    
    # Combine all audio segments
    if audio_segments:
        combined = AudioSegment.empty()
        for segment_path in audio_segments:
            segment = AudioSegment.from_file(str(segment_path))
            combined += segment
            # Clean up
            try:
                segment_path.unlink()
            except Exception:
                pass
        
        combined.export(str(output_path), format="mp3")
        logger.info(f"Combined TTS audio saved: {output_path}")
    else:
        raise ValueError("No audio segments generated")

