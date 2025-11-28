# Waar vind je Redeploy/Rebuild in Northflank?

## Locatie van Redeploy/Rebuild

### Optie 1: Via de Service Overview
1. Ga naar je **video-translator** service in Northflank
2. Je ziet een overzichtspagina met je service
3. Kijk naar de **rechterbovenhoek** of **bovenaan de pagina**
4. Er zou een knop moeten staan zoals:
   - **"Redeploy"** 
   - **"Deploy"**
   - **"Rebuild"**
   - Of een **⚙️ Settings** icoon

### Optie 2: Via Settings → Build & Deploy
1. Klik op je **video-translator** service
2. Klik op **"Settings"** (of het ⚙️ icoon) in het menu links
3. Klik op **"Build & Deploy"** in het submenu
4. Scroll naar beneden
5. Je zou moeten zien:
   - **"Redeploy"** knop
   - **"Rebuild"** knop
   - Of **"Deploy Latest"** knop

### Optie 3: Via de Deployments Tab
1. Klik op je service
2. Klik op **"Deployments"** tab (bovenaan)
3. Je ziet een lijst van deployments
4. Klik op de **"..."** (drie puntjes) menu naast een deployment
5. Of klik op **"New Deployment"** / **"Redeploy"** knop

### Optie 4: Via de Actions Menu
1. In je service overview
2. Kijk naar een **"Actions"** of **"..."** menu (meestal rechtsboven)
3. Klik erop
4. Je zou **"Redeploy"** of **"Rebuild"** moeten zien

## Als je de knoppen niet ziet:

### Alternatief: Forceer via Git
Northflank zou automatisch moeten deployen als je naar GitHub pusht. Ik heb net een commit gepusht, dus:

1. **Wacht 1-2 minuten** - Northflank detecteert meestal automatisch nieuwe commits
2. Check de **"Deployments"** tab - je zou een nieuwe deployment moeten zien
3. Als er geen nieuwe deployment komt, zie hieronder

### Forceer via Settings
1. Ga naar **Settings** → **Build & Deploy**
2. Zoek naar **"Auto Deploy"** of **"Deploy on Push"**
3. Zorg dat dit **aan** staat
4. Als het uit staat, zet het **aan** en sla op
5. Dit zou automatisch een nieuwe deployment moeten triggeren

## Nog steeds niet gevonden?

### Check deze locaties:
- **Bovenaan de service pagina**: Kijk naar de header/toolbar
- **Rechtsboven**: Vaak een actie menu
- **Settings menu**: Links in het menu, dan "Build & Deploy"
- **Deployments tab**: Bovenaan naast "Overview", "Logs", etc.

### Screenshot beschrijving:
In Northflank zie je meestal:
- Links: Menu met Overview, Logs, Settings, etc.
- Midden: Service details
- Rechtsboven: Actie knoppen (Redeploy, Settings, etc.)

## Snelle Fix zonder Redeploy knop:

Als je de knop echt niet kunt vinden, kan je ook:

1. **Verander een setting** (bijv. Build Method van Buildpack naar Dockerfile)
2. **Sla op** - Dit triggert vaak automatisch een rebuild
3. Of **verander iets in de configuratie** en sla op

## Belangrijkste:
**Check eerst de "Deployments" tab** - daar zie je of er al een nieuwe deployment bezig is na mijn laatste commit (`9fb94f3`). Als er een nieuwe deployment is, wacht tot die klaar is en check dan de logs!



