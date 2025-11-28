#!/usr/bin/env python3
"""
FORCE FIX voor indentatie problemen - Vervang de hele rename_video functie
en converteer alle line endings naar LF
"""

import re
from pathlib import Path

def fix_rename_video_function():
    """Vervang de rename_video functie met een gegarandeerd correcte versie"""
    
    main_py = Path(__file__).parent / "app" / "main.py"
    
    # Lees het bestand met expliciete encoding
    with open(main_py, 'rb') as f:
        content_bytes = f.read()
    
    # Converteer naar string en normaliseer line endings
    content = content_bytes.decode('utf-8')
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    
    # CORRECTE VERSIE - met exacte indentatie (alleen spaties, geen tabs)
    correct_function = '''@app.put("/api/videos/{video_id}/rename")
async def rename_video(request: Request, video_id: str, new_filename: str = Form(...)):
    """Rename a video, audio, or text file."""
    if not is_editor(request):
        return JSONResponse({"error": "Seuls les éditeurs peuvent renommer des fichiers."}, status_code=403)
    
    # First try to find as a video directory
    def _find_video_dir(directory: Path) -> Optional[Path]:
        for item in directory.iterdir():
            if item.is_dir():
                meta = _load_video_metadata(item)
                if meta and meta.id == video_id:
                    return item
                found = _find_video_dir(item)
                if found:
                    return found
        return None
    
    video_dir = _find_video_dir(settings.PROCESSED_DIR)
    if video_dir:
        # This is a processed video directory
        try:
            meta_path = video_dir / "metadata.json"
            meta = load_metadata(meta_path)
            meta.filename = new_filename
            save_metadata(meta, meta_path)
            return JSONResponse({"message": "Fichier renommé avec succès."})
        except Exception as e:
            logger.exception("Failed to rename video")
            return JSONResponse({"error": f"Impossible de renommer le fichier: {e}"}, status_code=500)
    
    # If not found as video directory, try to find as loose file
    loose_file = _find_loose_file(video_id)
    if loose_file and loose_file.exists() and loose_file.is_file():
        try:
            # Rename the file
            new_file_path = loose_file.parent / new_filename
            if new_file_path.exists():
                return JSONResponse({"error": "Un fichier avec ce nom existe déjà."}, status_code=400)
            
            loose_file.rename(new_file_path)
            return JSONResponse({"message": "Fichier renommé avec succès."})
        except Exception as e:
            logger.exception("Failed to rename loose file")
            return JSONResponse({"error": f"Impossible de renommer le fichier: {e}"}, status_code=500)
    
    # Also check for audio/text files in directories with info.json
    def _find_file_dir(directory: Path) -> Optional[Path]:
        for item in directory.iterdir():
            if item.is_dir():
                info = _load_video_info(item)
                # Check if this directory has the same ID (directory name)
                if item.name == video_id and (item / "info.json").exists():
                    return item
                found = _find_file_dir(item)
                if found:
                    return found
        return None
    
    file_dir = _find_file_dir(settings.PROCESSED_DIR)
    if file_dir:
        # This is an audio/text file directory
        try:
            info_path = file_dir / "info.json"
            info = json.loads(info_path.read_text(encoding="utf-8"))
            # Update the original filename in info.json
            # The actual file is named "original.ext", so we need to update the stored filename
            info["filename"] = new_filename
            info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
            return JSONResponse({"message": "Fichier renommé avec succès."})
        except Exception as e:
            logger.exception("Failed to rename file directory")
            return JSONResponse({"error": f"Impossible de renommer le fichier: {e}"}, status_code=500)
    
    return JSONResponse({"error": "Fichier non trouvé."}, status_code=404)
'''
    
    # Zoek de functie start
    start_pattern = r'@app\.put\("/api/videos/\{video_id\}/rename"\)'
    start_match = re.search(start_pattern, content)
    
    if not start_match:
        print("❌ Start marker niet gevonden!")
        return False
    
    start_pos = start_match.start()
    
    # Zoek het einde - volgende functie of decorator
    remaining = content[start_pos:]
    # Zoek naar de volgende @app decorator of async def die niet bij deze functie hoort
    end_pattern = r'\n\n@app\.|\n@app\.|\n\nasync def [^r]|\n\n@[a-z]'
    end_match = re.search(end_pattern, remaining[100:])  # Skip eerste 100 chars om false positives te vermijden
    
    if end_match:
        end_pos = start_pos + 100 + end_match.start()
    else:
        # Als we geen einde vinden, zoek naar de laatste return statement + 2 newlines
        end_pattern2 = r'return JSONResponse\(\{"error": "Fichier non trouvé\."\}, status_code=404\)\n'
        end_match2 = re.search(end_pattern2, remaining)
        if end_match2:
            end_pos = start_pos + end_match2.end()
        else:
            print("❌ Einde niet gevonden!")
            return False
    
    # Vervang de functie
    new_content = content[:start_pos] + correct_function.rstrip() + '\n\n' + content[end_pos:].lstrip()
    
    # Normaliseer alle line endings naar LF
    new_content = new_content.replace('\r\n', '\n').replace('\r', '\n')
    
    # Schrijf terug met LF line endings
    with open(main_py, 'wb') as f:
        f.write(new_content.encode('utf-8'))
    
    print("✓ Functie vervangen en line endings genormaliseerd")
    
    # Valideer syntax
    try:
        compile(new_content, str(main_py), 'exec')
        print("✓ Syntax validatie: OK")
        return True
    except SyntaxError as e:
        print(f"❌ Syntax error: {e}")
        print(f"   Regel {e.lineno}: {e.text}")
        return False

if __name__ == "__main__":
    if fix_rename_video_function():
        print("\n✅ SUCCES! Commit en push nu:")
        print("  git add app/main.py")
        print("  git commit -m 'Fix: Force fix indentatie regel 1413 - volledige functie vervangen + LF line endings'")
        print("  git push")
    else:
        print("\n❌ FOUT")

