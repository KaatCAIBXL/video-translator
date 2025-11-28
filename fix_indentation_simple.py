#!/usr/bin/env python3
"""
Eenvoudige versie: Vervang alleen de problematische regels met gegarandeerd correcte versie
"""

import re
import shutil
from pathlib import Path
from datetime import datetime

def fix_main_py():
    """Vervang de get_original_video functie met een gegarandeerd correcte versie"""
    
    main_py = Path(__file__).parent / "app" / "main.py"
    
    if not main_py.exists():
        print(f"❌ Bestand niet gevonden: {main_py}")
        return False
    
    # Backup
    backup_path = main_py.with_suffix(f".py.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(main_py, backup_path)
    print(f"✓ Backup: {backup_path.name}")
    
    # Lees bestand
    with open(main_py, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # CORRECTE VERSIE VAN DE FUNCTIE
    correct_function = '''@app.get("/videos/{video_id}/original")
async def get_original_video(request: Request, video_id: str):
    # First try to find as a video directory
    video_dir = _find_video_directory(video_id)
    if video_dir and video_dir.exists():
        # Check privacy (video itself or parent folder)
        info = _load_video_info(video_dir)
        video_is_private = info.get("is_private", False) or _is_folder_private(info.get("folder_path"))
        if video_is_private and not is_editor(request):
            return JSONResponse({"error": "Accès refusé"}, status_code=403)

        original_path = _find_original_video(video_dir)
        if original_path is not None:
            meta = _load_video_metadata(video_dir)
            filename = meta.filename if meta else original_path.name
            return FileResponse(original_path, filename=filename)
    
    # If not found as video directory, try to find as loose video file
    loose_file = _find_loose_file(video_id)
    if loose_file and loose_file.exists() and loose_file.is_file():
        # Check if it's a video file
        if loose_file.suffix.lower() in [".mp4", ".avi", ".mov", ".mkv", ".webm"]:
            # Check privacy based on folder
            try:
                rel_path = loose_file.parent.relative_to(settings.PROCESSED_DIR)
                folder_path = str(rel_path) if str(rel_path) != "." else None
            except ValueError:
                folder_path = None
            
            if not is_editor(request) and _is_folder_private(folder_path):
                return JSONResponse({"error": "Accès refusé"}, status_code=403)
            
            return FileResponse(loose_file, filename=loose_file.name)
    
    return JSONResponse({"error": "Vidéo non trouvée"}, status_code=404)
'''
    
    # Vervang de functie met regex
    # Zoek naar de functie vanaf @app.get decorator tot de volgende functie/decorator
    pattern = r'@app\.get\("/videos/\{video_id\}/original"\)\s+async def get_original_video\([^)]+\):.*?(?=\n@app\.|\nasync def |\ndef |\Z)'
    
    # Probeer eerst met multiline
    new_content = re.sub(pattern, correct_function.rstrip(), content, flags=re.DOTALL)
    
    # Als dat niet werkt, probeer een meer specifieke match
    if new_content == content:
        # Zoek naar de functie start
        start_match = re.search(r'@app\.get\("/videos/\{video_id\}/original"\)', content)
        if start_match:
            start_pos = start_match.start()
            # Zoek naar het einde (volgende @app. of functie definitie)
            end_match = re.search(r'\n@app\.|\nasync def [^g]|\ndef [^_]', content[start_pos + 100:])
            if end_match:
                end_pos = start_pos + 100 + end_match.start()
                # Vervang
                new_content = content[:start_pos] + correct_function.rstrip() + '\n\n' + content[end_pos:]
            else:
                # Als einde niet gevonden, vervang tot einde van bestand
                new_content = content[:start_pos] + correct_function.rstrip() + '\n'
        else:
            print("❌ Functie niet gevonden in bestand")
            return False
    else:
        print("✓ Functie gevonden en vervangen")
    
    # Schrijf terug
    with open(main_py, 'w', encoding='utf-8', newline='\n') as f:
        f.write(new_content)
    
    # Valideer
    try:
        compile(open(main_py, 'r', encoding='utf-8').read(), str(main_py), 'exec')
        print("✓ Syntax validatie: OK")
        return True
    except SyntaxError as e:
        print(f"❌ Syntax error: {e}")
        print(f"   Regel {e.lineno}")
        # Herstel backup
        shutil.copy2(backup_path, main_py)
        print(f"⚠️  Backup hersteld")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("EENVOUDIGE INDENTATIE FIX")
    print("=" * 60)
    print()
    
    if fix_main_py():
        print()
        print("✅ SUCCES!")
        print("\nTest met: python -m py_compile app/main.py")
    else:
        print()
        print("❌ FOUT - Backup is hersteld")



