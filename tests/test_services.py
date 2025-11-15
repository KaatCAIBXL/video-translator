from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from app import services


def test_select_chunk_duration_prefers_calculated_value():
    file_size = 80 * 1024 * 1024  # 80 MB
    duration_seconds = 3600  # 1 uur
    max_bytes = 24 * 1024 * 1024

    result = services._select_chunk_duration(file_size, duration_seconds, max_bytes)

    # 24 MB budget en deze bitrate komt neer op ~1080 seconden per chunk.
    assert result == 1080


def test_select_chunk_duration_handles_high_bitrate_audio():
    file_size = 120 * 1024 * 1024  # 120 MB
    duration_seconds = 60
    max_bytes = 24 * 1024 * 1024

    result = services._select_chunk_duration(file_size, duration_seconds, max_bytes)

    # Hoge bitrate -> korte chunks om onder de limiet te blijven.
    assert result == 12


def test_select_chunk_duration_falls_back_when_duration_unknown():
    file_size = 120 * 1024 * 1024
    duration_seconds = 0
    max_bytes = 24 * 1024 * 1024

    result = services._select_chunk_duration(file_size, duration_seconds, max_bytes)

    # Geen duur -> hanteer default waarde (600 seconden).
    assert result == 600
