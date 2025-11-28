#!/usr/bin/env python3
"""
FORCE FIX voor regel 1413 - Vervang de hele rename_video functie met gegarandeerd correcte versie
"""

import re
from pathlib import Path

def force_fix_rename_video():
    """Vervang de rename_video functie met een gegarandeerd correcte versie"""
    
    main_py = Path(__file__).parent / "app" / "main.py"
    
    with open(main_py, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # CORRECTE VERSIE - met exacte indentatie
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
    
    # Vervang de functie - zoek naar de start
    pattern = r'@app\.put\("/api/videos/\{video_id\}/rename"\).*?return JSONResponse\(\{"error": "Fichier non trouvé\."\}, status_code=404\)'
    
    new_content = re.sub(pattern, correct_function.rstrip(), content, flags=re.DOTALL)
    
    if new_content == content:
        print("⚠️  Functie niet gevonden met regex, probeer handmatige vervanging...")
        # Handmatige vervanging
        start_marker = '@app.put("/api/videos/{video_id}/rename")'
        end_marker = 'return JSONResponse({"error": "Fichier non trouvé."}, status_code=404)'
        
        start_pos = content.find(start_marker)
        if start_pos == -1:
            print("❌ Start marker niet gevonden!")
            return False
        
        # Zoek einde - volgende @app decorator of functie
        remaining = content[start_pos + len(start_marker):]
        end_match = re.search(r'\n@app\.|\nasync def [^r]|\ndef [^_]', remaining)
        
        if end_match:
            end_pos = start_pos + len(start_marker) + end_match.start()
            new_content = content[:start_pos] + correct_function.rstrip() + '\n\n' + content[end_pos:]
        else:
            print("❌ Einde niet gevonden!")
            return False
    
    # Schrijf terug met LF line endings
    with open(main_py, 'w', encoding='utf-8', newline='\n') as f:
        f.write(new_content)
    
    print("✓ Functie vervangen")
    
    # Valideer
    try:
        compile(open(main_py, 'r', encoding='utf-8').read(), str(main_py), 'exec')
        print("✓ Syntax validatie: OK")
        return True
    except SyntaxError as e:
        print(f"❌ Syntax error: {e}")
        return False

if __name__ == "__main__":
    if force_fix_rename_video():
        print("\n✅ SUCCES! Commit en push nu:")
        print("  git add app/main.py")
        print("  git commit -m 'Fix: Force fix indentatie regel 1413 - volledige functie vervangen'")
        print("  git push")
    else:
        print("\n❌ FOUT")



