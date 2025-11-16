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

## ffmpeg & chunking configureren

| Variabele | Default | Beschrijving |
| --- | --- | --- |
| `FFMPEG_HWACCEL_ARGS` | *(leeg)* | Optionele flags die letterlijk achter `ffmpeg -y` worden geplaatst. Handig voor `-hwaccel cuda -hwaccel_output_format cuda`, VAAPI (`-hwaccel vaapi -vaapi_device /dev/dri/renderD128`) enz. Hiermee kun je dezelfde code draaien maar wel GPU/QuickSync/VAAPI gebruiken tijdens audio-extractie én chunking. |
| `WHISPER_MAX_UPLOAD_MB` | `24` | Maximale grootte van een audiobestand dat in één keer naar Whisper wordt geüpload. Door dit te verlagen dwing je kleinere chunks af; door het gelijk te houden en lokale audio te encoderen naar 16 kHz mono PCM, weet je precies wat de bovengrens is om chunking te vermijden. |

Whisper accepteert uploads tot ±25 MB. Omdat `extract_audio` alles naar 16 kHz
mono PCM omzet, komt dat neer op ~32 kB per seconde of 1,95 MB per minuut. Met
de standaard 24 MB grens betekent dit dat elke opname korter dan ~13 minuten in
één stuk naar Whisper gaat. Wil je chunking vermijden, encodeer je bestand
vooraf naar 16 kHz mono PCM en houd de duur onder die grens, of splits de video
op in meerdere uploads. Zodra je hardwareversnelling via `FFMPEG_HWACCEL_ARGS`
hebt ingeschakeld, profiteren alle `ffmpeg`-stappen (audio extractie, chunking,
nieuw audiotrack muxen) automatisch van dezelfde GPU/VAAPI instellingen.
