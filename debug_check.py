#!/usr/bin/env python3
"""
Debug script voor video-translator applicatie.
Controleert op syntax errors, import errors, en configuratie problemen.
"""

import sys
import ast
from pathlib import Path

def check_syntax(file_path: Path) -> tuple[bool, str]:
    """Controleer of een Python bestand geldige syntax heeft."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            source = f.read()
        ast.parse(source, filename=str(file_path))
        return True, ""
    except SyntaxError as e:
        return False, f"Syntax error op regel {e.lineno}: {e.msg}"
    except Exception as e:
        return False, f"Error: {str(e)}"

def check_imports(module_name: str) -> tuple[bool, str]:
    """Controleer of een module geïmporteerd kan worden."""
    try:
        __import__(module_name)
        return True, ""
    except ImportError as e:
        return False, f"Import error: {str(e)}"
    except Exception as e:
        return False, f"Error: {str(e)}"

def main():
    print("=" * 60)
    print("VIDEO-TRANSLATOR DEBUG CHECK")
    print("=" * 60)
    print()
    
    app_dir = Path(__file__).parent / "app"
    errors = []
    warnings = []
    
    # 1. Controleer syntax van belangrijke bestanden
    print("1. Syntax controle...")
    important_files = [
        app_dir / "main.py",
        app_dir / "services.py",
        app_dir / "audio_text_services.py",
        app_dir / "config.py",
        app_dir / "models.py",
        app_dir / "auth.py",
    ]
    
    for file_path in important_files:
        if file_path.exists():
            is_valid, error = check_syntax(file_path)
            if is_valid:
                print(f"   ✓ {file_path.name}")
            else:
                print(f"   ✗ {file_path.name}: {error}")
                errors.append(f"{file_path.name}: {error}")
        else:
            print(f"   ⚠ {file_path.name}: Bestand niet gevonden")
            warnings.append(f"{file_path.name}: Bestand niet gevonden")
    
    print()
    
    # 2. Controleer Python dependencies
    print("2. Dependency controle...")
    dependencies = [
        "fastapi",
        "uvicorn",
        "pydantic",
        "openai",
        "deepl",
        "pydub",
        "edge_tts",
        "ffmpeg_python",
    ]
    
    for dep in dependencies:
        is_valid, error = check_imports(dep)
        if is_valid:
            print(f"   ✓ {dep}")
        else:
            print(f"   ✗ {dep}: {error}")
            warnings.append(f"{dep}: {error}")
    
    print()
    
    # 3. Controleer configuratie bestanden
    print("3. Configuratie controle...")
    config_files = [
        Path(__file__).parent / "requirements.txt",
        Path(__file__).parent / ".env",
    ]
    
    for config_file in config_files:
        if config_file.exists():
            print(f"   ✓ {config_file.name} bestaat")
        else:
            print(f"   ⚠ {config_file.name}: Bestand niet gevonden")
            if config_file.name == ".env":
                warnings.append(f"{config_file.name}: .env bestand niet gevonden (mogelijk nodig voor API keys)")
    
    print()
    
    # 4. Controleer belangrijke directories
    print("4. Directory structuur controle...")
    important_dirs = [
        app_dir,
        app_dir / "static",
        app_dir / "templates",
    ]
    
    for dir_path in important_dirs:
        if dir_path.exists() and dir_path.is_dir():
            print(f"   ✓ {dir_path.name}/ bestaat")
        else:
            print(f"   ✗ {dir_path.name}/: Directory niet gevonden")
            errors.append(f"{dir_path.name}/: Directory niet gevonden")
    
    print()
    
    # 5. Samenvatting
    print("=" * 60)
    print("SAMENVATTING")
    print("=" * 60)
    
    if errors:
        print(f"\n❌ ERRORS ({len(errors)}):")
        for error in errors:
            print(f"   - {error}")
        print("\n⚠️  Deze errors moeten worden opgelost voordat de applicatie kan draaien.")
    else:
        print("\n✓ Geen kritieke errors gevonden!")
    
    if warnings:
        print(f"\n⚠️  WARNINGS ({len(warnings)}):")
        for warning in warnings:
            print(f"   - {warning}")
        print("\n💡 Deze warnings kunnen problemen veroorzaken, maar zijn niet kritiek.")
    else:
        print("\n✓ Geen warnings gevonden!")
    
    print()
    print("=" * 60)
    
    # Return exit code
    return 1 if errors else 0

if __name__ == "__main__":
    sys.exit(main())



