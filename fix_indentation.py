#!/usr/bin/env python3
"""
Automatisch indentatie fix script voor app/main.py
Dit script controleert en corrigeert indentatie problemen, vooral rond regel 789.
"""

import re
import shutil
from pathlib import Path
from datetime import datetime

def fix_indentation():
    """Fix indentatie problemen in app/main.py"""
    
    main_py = Path(__file__).parent / "app" / "main.py"
    
    if not main_py.exists():
        print(f"❌ Bestand niet gevonden: {main_py}")
        return False
    
    # Maak backup
    backup_path = main_py.with_suffix(f".py.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(main_py, backup_path)
    print(f"✓ Backup gemaakt: {backup_path.name}")
    
    # Lees het bestand
    with open(main_py, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    print(f"✓ Bestand gelezen: {len(lines)} regels")
    
    # Fix de get_original_video functie (rond regel 789)
    fixed_lines = []
    i = 0
    in_get_original = False
    fixed_count = 0
    
    while i < len(lines):
        line = lines[i]
        line_num = i + 1
        
        # Detecteer start van get_original_video functie
        if '@app.get("/videos/{video_id}/original")' in line or 'async def get_original_video' in line:
            in_get_original = True
            fixed_lines.append(line)
            i += 1
            continue
        
        # Detecteer einde van functie (volgende @app decorator of functie definitie)
        if in_get_original and (line.strip().startswith('@app.') or 
                                (line.strip().startswith('async def ') and 'get_original_video' not in line) or
                                (line.strip().startswith('def ') and not line.strip().startswith('def _'))):
            in_get_original = False
        
        # Fix indentatie binnen get_original_video functie
        if in_get_original and line_num >= 789 and line_num <= 820:
            original_line = line
            stripped = line.lstrip()
            leading_spaces = len(line) - len(stripped)
            
            # Regel 789: if video_dir and video_dir.exists():
            if line_num == 789:
                if 'if video_dir and video_dir.exists():' in line:
                    # Zorg dat deze regel 4 spaties heeft
                    if leading_spaces != 4:
                        line = '    ' + stripped
                        fixed_count += 1
                        print(f"  ✓ Regel {line_num}: Indentatie gecorrigeerd (was {leading_spaces}, nu 4)")
            
            # Regel 790-800: Code binnen if blok moet 8 spaties hebben
            elif line_num >= 790 and line_num <= 800:
                if stripped and not stripped.startswith('#'):
                    # Als het binnen het if blok hoort (niet een nieuwe functie of decorator)
                    if not (stripped.startswith('@') or 
                           (stripped.startswith('async def ') and 'get_original_video' not in stripped) or
                           (stripped.startswith('def ') and not stripped.startswith('def _'))):
                        
                        # Regel 790-796: Moet 8 spaties hebben (binnen if video_dir)
                        if line_num <= 796:
                            if leading_spaces < 8:
                                line = '        ' + stripped
                                fixed_count += 1
                                print(f"  ✓ Regel {line_num}: Indentatie gecorrigeerd naar 8 spaties")
                        
                        # Regel 797-800: if original_path moet 8 spaties hebben, binnen code 12
                        elif line_num == 797:
                            if 'if original_path is not None:' in line:
                                if leading_spaces != 8:
                                    line = '        ' + stripped
                                    fixed_count += 1
                                    print(f"  ✓ Regel {line_num}: Indentatie gecorrigeerd naar 8 spaties")
                        elif line_num >= 798 and line_num <= 800:
                            if leading_spaces < 12:
                                line = '            ' + stripped
                                fixed_count += 1
                                print(f"  ✓ Regel {line_num}: Indentatie gecorrigeerd naar 12 spaties")
            
            # Regel 802+: Buiten if blok, moet 4 spaties hebben
            elif line_num >= 802 and line_num <= 819:
                if stripped and not stripped.startswith('#'):
                    if not (stripped.startswith('@') or 
                           (stripped.startswith('async def ') and 'get_original_video' not in stripped)):
                        if leading_spaces < 4:
                            line = '    ' + stripped
                            fixed_count += 1
                            print(f"  ✓ Regel {line_num}: Indentatie gecorrigeerd naar 4 spaties")
        
        fixed_lines.append(line)
        i += 1
    
    # Schrijf het gefixte bestand
    with open(main_py, 'w', encoding='utf-8', newline='\n') as f:
        f.writelines(fixed_lines)
    
    print(f"\n✓ Bestand opgeslagen")
    print(f"✓ {fixed_count} regels gecorrigeerd")
    
    # Valideer syntax
    try:
        compile(open(main_py, 'r', encoding='utf-8').read(), str(main_py), 'exec')
        print(f"✓ Syntax validatie: OK")
        return True
    except SyntaxError as e:
        print(f"❌ Syntax error na fix: {e}")
        print(f"   Regel {e.lineno}: {e.text}")
        print(f"\n⚠️  Herstel backup: {backup_path}")
        return False

def fix_rename_video_function():
    """Fix ook de rename_video functie (rond regel 1413)"""
    
    main_py = Path(__file__).parent / "app" / "main.py"
    
    with open(main_py, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Fix pattern voor rename_video functie
    # Zoek naar: if video_dir:\n    # This is...\n    try:
    pattern1 = r'(    if video_dir:\n        # This is a processed video directory\n)    try:'
    replacement1 = r'\1        try:'
    
    content = re.sub(pattern1, replacement1, content)
    
    # Fix return statements binnen try/except
    pattern2 = r'(            save_metadata\(meta, meta_path\)\n)            return JSONResponse'
    replacement2 = r'\1            return JSONResponse'
    
    content = re.sub(pattern2, replacement2, content)
    
    pattern3 = r'(            logger\.exception\("Failed to rename video"\)\n)            return JSONResponse'
    replacement3 = r'\1            return JSONResponse'
    
    content = re.sub(pattern3, replacement3, content)
    
    with open(main_py, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)
    
    print("✓ rename_video functie gecontroleerd")

def main():
    print("=" * 60)
    print("AUTOMATISCHE INDENTATIE FIX")
    print("=" * 60)
    print()
    
    if fix_indentation():
        fix_rename_video_function()
        print()
        print("=" * 60)
        print("✅ SUCCES!")
        print("=" * 60)
        print("\nHet bestand is gecorrigeerd. Test nu:")
        print("  python -m py_compile app/main.py")
        print("\nOf commit en push naar GitHub:")
        print("  git add app/main.py")
        print("  git commit -m 'Fix: Automatische indentatie correctie'")
        print("  git push")
        return 0
    else:
        print()
        print("=" * 60)
        print("❌ FOUT")
        print("=" * 60)
        print("\nEr is een probleem opgetreden. Controleer de backup.")
        return 1

if __name__ == "__main__":
    import sys
    sys.exit(main())



