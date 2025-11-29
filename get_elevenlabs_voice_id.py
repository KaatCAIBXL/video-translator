#!/usr/bin/env python3
"""
Script om de ElevenLabs voice ID te vinden voor Lingala.
Gebruik: python get_elevenlabs_voice_id.py
"""

import os
import sys
from dotenv import load_dotenv
from pathlib import Path

# Load .env file
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

API_KEY = os.getenv("LINGALA_TTS_API_KEY", "")

if not API_KEY:
    print("❌ LINGALA_TTS_API_KEY niet gevonden in .env file")
    print("Voeg toe aan .env: LINGALA_TTS_API_KEY=sk_...")
    sys.exit(1)

try:
    from app.services import get_elevenlabs_voices
    
    print("🔍 Ophalen van beschikbare voices van ElevenLabs...")
    voices = get_elevenlabs_voices(API_KEY)
    
    print(f"\n✅ {len(voices)} voice(s) gevonden:\n")
    for voice in voices:
        voice_id = voice.get("voice_id", "N/A")
        name = voice.get("name", "N/A")
        print(f"  Voice ID: {voice_id}")
        print(f"  Name: {name}")
        print()
    
    print("\n💡 Kopieer de voice_id van je Lingala stem en voeg toe aan .env:")
    print("   LINGALA_ELEVENLABS_VOICE_ID=...")
    
except Exception as e:
    print(f"❌ Fout: {e}")
    sys.exit(1)










