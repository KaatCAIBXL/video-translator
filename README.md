# Video Translator & TTS Library

Deze app laat je:
- een video uploaden
- audio transcriberen via Whisper (OpenAI)
- zinnen per 2 groeperen met timestamps
- vertalen naar max. 2 gekozen talen (via DeepL of AI)
- ondertitels (VTT) genereren
- optioneel: dubbing (audio vervangen) via TTS
- video’s bekijken in een eenvoudige bibliotheek

## Installatie

```bash
git clone <jouw-repo-url>
cd video-translator

python -m venv .venv
source .venv/bin/activate  # op Windows: .venv\Scripts\activate

pip install -r requirements.txt


## Starten

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Voor omgevingen die [Cloud Native Buildpacks](https://buildpacks.io/) gebruiken (zoals
Azure App Service of Azure Container Apps met source-based deployment) is een
`Procfile` toegevoegd. Dit zorgt ervoor dat het platform de applicatie kan
starten met het juiste commando.

## Asynchrone verwerking

Uploads worden onmiddellijk geaccepteerd waarna de zware stappen (ffmpeg,
Whisper, vertaling, TTS) op de achtergrond draaien. Je krijgt direct een
`id` terug uit `POST /api/upload` en kunt de voortgang ophalen via
`GET /api/jobs/{id}`. De response bevat de status (`pending`, `processing`,
`completed`, `failed`), eventuele waarschuwingen en fouten. Zodra de job is
afgerond verschijnen de resultaten automatisch in de videolijst en kun je de
bestaande download-endpoints blijven gebruiken.
