---
layout: default
title: Video Translator
---

# Video Translator & TTS Library

Welkom bij de documentatie voor de Video Translator applicatie. Deze applicatie maakt het mogelijk om video’s te uploaden, audio te transcriberen, vertalingen te genereren en ondertitels of dubbing te maken.

## Functionaliteiten
- Upload video’s en transcribeer de audio met Whisper (OpenAI).
- Groepeer zinnen per twee inclusief timestamps.
- Vertaal naar maximaal twee talen via DeepL of AI.
- Genereer ondertitels in VTT-formaat.
- Maak optioneel nieuwe audio via text-to-speech (dubbing).
- Bekijk verwerkte video’s in een eenvoudige bibliotheek.

## Installatie
Volg de stappen uit de README om de applicatie lokaal te installeren:

```bash
git clone <jouw-repo-url>
cd video-translator
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Applicatie starten
Start de applicatie lokaal met:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Voor cloud-omgevingen die gebruikmaken van Cloud Native Buildpacks (zoals Azure App Service of Azure Container Apps) is een `Procfile` aanwezig waarmee de applicatie automatisch met het juiste commando start.

