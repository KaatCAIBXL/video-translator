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
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from .services import _elevenlabs_tts_to_bytes

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

# Cache voor subscription-correcties uit bestand
_subscription_replacements: Optional[List[Tuple[str, str]]] = None


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
# VASTE HANDMATIGE CORRECTIES (SUBSCRIPTION-CORRECTIES)
# ============================================================
def _load_subscription_replacements() -> List[Tuple[str, str]]:
    """
    Laad de vaste correcties uit een optioneel tekstbestand
    `subscription_corrections.txt` in dezelfde map als dit bestand.

    Formaat per regel:
        fout -> correct

    Regels die leeg zijn of met '#' beginnen worden genegeerd.
    Als het bestand ontbreekt, vallen we terug op de ingebouwde lijst.
    """
    # Ingebouwde defaults (zoals in je voorbeeldbestand)
    defaults: List[Tuple[str, str]] = [
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

    cfg_path = Path(__file__).resolve().parent / "subscription_corrections.txt"
    if not cfg_path.exists():
        return defaults

    replacements: List[Tuple[str, str]] = []
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
    except Exception:
        # Bij een probleem met het bestand gewoon de defaults gebruiken
        logger.exception("Kon subscription_corrections.txt niet inlezen, gebruik defaults")
        return defaults or []

    return replacements or defaults


def _apply_subscription_corrections(text: str) -> str:
    """
    Pas vaste woord-/zinsniveaucorrecties toe vóór de AI-correctie.

    Deze functie is bedoeld voor veelvoorkomende fouten in ondertitels /
    transcripties (bijv. namen of bijbelse termen) die we altijd op een
    vaste manier willen herschrijven nog vóór GPT-contextcorrectie.

    Let op:
    - De vervangingen zijn *case‑gevoelig* en worden alleen toegepast
      op exacte woord-/zinsdelen.
    - Sommige varianten (bijv. 'pape' en 'Pape') staan daarom elk apart
      in de lijst.
    """

    if not text:
        return text

    global _subscription_replacements
    if _subscription_replacements is None:
        _subscription_replacements = _load_subscription_replacements()

    # Langste patronen eerst, zodat langere zinsdelen voorrang krijgen.
    replacements = sorted(
        _subscription_replacements, key=lambda item: len(item[0]), reverse=True
    )

    result = text
    for wrong, correct in replacements:
        # Gebruik woordgrenzen zodat we geen delen van langere woorden vervangen.
        pattern = re.compile(rf"\b{re.escape(wrong)}\b")
        result = pattern.sub(correct, result)

    return result


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
    """Transcribe audio using Whisper API with optimized settings for speed."""
    try:
        if not audio_path.exists():
            logger.error(f"Audio file does not exist: {audio_path}")
            return ""
        
        if audio_path.stat().st_size == 0:
            logger.error(f"Audio file is empty: {audio_path}")
            return ""
        
        client = get_openai_client()
        if not client:
            logger.error("OpenAI client not available - check API key configuration")
            return ""
        
        with open(audio_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language=language,
                temperature=0.0,  # More deterministic, faster processing
                response_format="text"  # Simpler format, faster response
            )
            result = transcript.strip() if isinstance(transcript, str) else transcript.text.strip()
            if result:
                logger.debug(f"Transcribed {audio_path.name}: {len(result)} characters")
            else:
                logger.warning(f"Whisper API returned empty transcript for {audio_path.name}")
            return result
    except Exception as e:
        logger.error(f"Whisper API error for {audio_path}: {e}", exc_info=True)
        return ""


def transcribe_long_audio(audio_path: Path, language: str = "fr", max_workers: int = 3) -> str:
    """Split long audio and transcribe each chunk in parallel for faster processing."""
    if not audio_path.exists():
        logger.error(f"Audio file does not exist: {audio_path}")
        return ""
    
    chunks = split_audio_efficient(audio_path)
    if not chunks:
        logger.warning(f"No audio chunks created from {audio_path}")
        return ""
    
    total = len(chunks)
    logger.info(f"Transcribing {total} chunks in parallel (max {max_workers} workers) for language: {language}")
    
    # Store results with their index to maintain order
    results = {}
    full_text_parts = []
    failed_chunks = 0
    
    # Process chunks in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all transcription tasks
        future_to_chunk = {
            executor.submit(transcribe_audio_whisper_api, chunk, language): (idx, chunk)
            for idx, chunk in enumerate(chunks)
        }
        
        # Process completed tasks as they finish
        completed = 0
        for future in as_completed(future_to_chunk):
            idx, chunk = future_to_chunk[future]
            try:
                text = future.result()
                if text and text.strip():
                results[idx] = text
                completed += 1
                progress = math.ceil((completed / total) * 100)
                    logger.info(f"Chunk {idx + 1}/{total} completed ({progress}% total) - {len(text)} chars")
                else:
                    logger.warning(f"Chunk {idx + 1}/{total} returned empty text")
                    failed_chunks += 1
                    results[idx] = ""
            except Exception as e:
                logger.error(f"Error transcribing chunk {idx + 1}: {e}", exc_info=True)
                failed_chunks += 1
                results[idx] = ""
            finally:
                # Clean up chunk file
                try:
                    chunk.unlink()
                except Exception:
                    pass
    
    # Reconstruct text in correct order
    for idx in range(total):
        if idx in results and results[idx]:
            full_text_parts.append(results[idx])
    
    full_text = "\n".join(full_text_parts).strip()
    
    if failed_chunks > 0:
        logger.warning(f"{failed_chunks}/{total} chunks failed during transcription")
    
    if full_text:
        full_text = _apply_subscription_corrections(full_text)
        logger.info(f"Transcription completed: {len(full_text)} characters total")
    else:
        logger.error(f"Transcription failed: no text extracted from {total} chunks")
    
    return full_text


# ============================================================
# TEXT IMPROVEMENT (GPT)
# ============================================================
def improve_text_with_ai(text: str, language: str = "fr") -> str:
    """Improve text using GPT-4.

    We always passen eerst de vaste handmatige 'subscription'-correcties toe,
    zodat namen en vaste uitdrukkingen al juist staan vóór de AI-correctie.
    """
    # Eerst vaste handmatige vervangingen toepassen
    text = _apply_subscription_corrections(text)
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


def _remove_language_prefix(text: str, target_lang: str) -> str:
    """Remove language name prefixes like 'Tshiluba:', 'Lingala:', 'Kituba:', etc. from translated text."""
    if not text:
        return text
    
    # Get possible language names that might be used as prefixes
    language_name = BANTU_LANGUAGES.get(target_lang.lower(), target_lang.upper())
    lang_code_upper = target_lang.upper()
    
    # Patterns to remove: "Tshiluba:", "LINGALA:", "LUA:", "Kituba:", "Kikongo:", etc.
    patterns = [
        f"{language_name}:",
        f"{language_name.upper()}:",
        f"{language_name.lower()}:",
        f"{lang_code_upper}:",
        f"{target_lang.upper()}:",
    ]
    
    # Special cases: for Kituba (kg), also check for "Kituba:" and "Kikongo:" separately
    if target_lang.lower() == "kg":
        patterns.extend([
            "Kituba:",
            "KITUBA:",
            "kituba:",
            "Kikongo:",
            "KIKONGO:",
            "kikongo:",
            "Kikongo (Kituba):",
            "KIKONGO (KITUBA):",
            "kikongo (kituba):",
        ])
    
    # Special cases: for Malagasy (mg), also check for "Malagasy:" separately
    if target_lang.lower() == "mg":
        patterns.extend([
            "Malagasy:",
            "MALAGASY:",
            "malagasy:",
        ])
    
    # Special cases: for Yoruba (yo), also check for "Yoruba:" separately
    if target_lang.lower() == "yo":
        patterns.extend([
            "Yoruba:",
            "YORUBA:",
            "yoruba:",
        ])
    
    text_cleaned = text.strip()
    for pattern in patterns:
        # Remove at start of line
        if text_cleaned.startswith(pattern):
            text_cleaned = text_cleaned[len(pattern):].strip()
        # Also check for pattern at start of each line (for multi-line text)
        lines = text_cleaned.split('\n')
        cleaned_lines = []
        for line in lines:
            line_stripped = line.strip()
            for pat in patterns:
                if line_stripped.startswith(pat):
                    line_stripped = line_stripped[len(pat):].strip()
                    break
            cleaned_lines.append(line_stripped)
        text_cleaned = '\n'.join(cleaned_lines)
    
    return text_cleaned.strip()


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
            translated_text = response.choices[0].message.content.strip()
            # Remove any language prefix that the AI might have added
            translated_text = _remove_language_prefix(translated_text, target_lang)
            translated_blocks.append(translated_text)
        
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
    """Generate TTS audio using edge_tts or ElevenLabs."""
    lang = language.lower()
    
    # Voor Lingala: gebruik ElevenLabs als API key beschikbaar is
    if lang == "ln" and settings.LINGALA_TTS_API_KEY and settings.LINGALA_ELEVENLABS_VOICE_ID:
        try:
            audio_bytes = await _elevenlabs_tts_to_bytes(
                text,
                settings.LINGALA_ELEVENLABS_VOICE_ID,
                settings.LINGALA_TTS_API_KEY,
                speed_multiplier=1.0
            )
            output_path.write_bytes(audio_bytes)
            logger.info(f"ElevenLabs TTS audio generated for Lingala: {output_path}")
            return
        except Exception as e:
            logger.warning(f"ElevenLabs TTS failed for Lingala, falling back to edge-tts: {e}")
            # Fallback naar edge-tts
            pass
    
    # Voor Tshiluba: gebruik ElevenLabs als API key beschikbaar is
    if lang == "lua" and settings.TSHILUBA_TTS_API_KEY and settings.TSHILUBA_ELEVENLABS_VOICE_ID:
        try:
            audio_bytes = await _elevenlabs_tts_to_bytes(
                text,
                settings.TSHILUBA_ELEVENLABS_VOICE_ID,
                settings.TSHILUBA_TTS_API_KEY,
                speed_multiplier=1.0
            )
            output_path.write_bytes(audio_bytes)
            logger.info(f"ElevenLabs TTS audio generated for Tshiluba: {output_path}")
            return
        except Exception as e:
            logger.warning(f"ElevenLabs TTS failed for Tshiluba, falling back to edge-tts: {e}")
            # Fallback naar edge-tts
            pass
    
    # Voor Kituba: gebruik ElevenLabs als API key beschikbaar is
    if lang == "kg" and settings.KITUBA_TTS_API_KEY and settings.KITUBA_ELEVENLABS_VOICE_ID:
        try:
            audio_bytes = await _elevenlabs_tts_to_bytes(
                text,
                settings.KITUBA_ELEVENLABS_VOICE_ID,
                settings.KITUBA_TTS_API_KEY,
                speed_multiplier=1.0
            )
            output_path.write_bytes(audio_bytes)
            logger.info(f"ElevenLabs TTS audio generated for Kituba: {output_path}")
            return
        except Exception as e:
            logger.warning(f"ElevenLabs TTS failed for Kituba, falling back to edge-tts: {e}")
            # Fallback naar edge-tts
            pass
    
    # Voor Malagasy: gebruik ElevenLabs als API key beschikbaar is
    if lang == "mg" and settings.MALAGASY_TTS_API_KEY and settings.MALAGASY_ELEVENLABS_VOICE_ID:
        try:
            audio_bytes = await _elevenlabs_tts_to_bytes(
                text,
                settings.MALAGASY_ELEVENLABS_VOICE_ID,
                settings.MALAGASY_TTS_API_KEY,
                speed_multiplier=1.0
            )
            output_path.write_bytes(audio_bytes)
            logger.info(f"ElevenLabs TTS audio generated for Malagasy: {output_path}")
            return
        except Exception as e:
            logger.warning(f"ElevenLabs TTS failed for Malagasy, falling back to edge-tts: {e}")
            # Fallback naar edge-tts
            pass
    
    # Voor Yoruba: gebruik ElevenLabs als API key beschikbaar is
    if lang == "yo" and settings.YORUBA_TTS_API_KEY and settings.YORUBA_ELEVENLABS_VOICE_ID:
        try:
            audio_bytes = await _elevenlabs_tts_to_bytes(
                text,
                settings.YORUBA_ELEVENLABS_VOICE_ID,
                settings.YORUBA_TTS_API_KEY,
                speed_multiplier=1.0
            )
            output_path.write_bytes(audio_bytes)
            logger.info(f"ElevenLabs TTS audio generated for Yoruba: {output_path}")
            return
        except Exception as e:
            logger.warning(f"ElevenLabs TTS failed for Yoruba, falling back to edge-tts: {e}")
            # Fallback naar edge-tts
            pass
    
    # Standaard: gebruik edge-tts
    voice = TTS_VOICES.get(lang, TTS_VOICES["en"])
    
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

