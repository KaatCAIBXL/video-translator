from pathlib import Path
import sys
import pytest


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from app import services
from app.main import _build_combined_segments
from app.models import TranslationSegment, VideoMetadata


def test_select_chunk_duration_prefers_calculated_value():
    file_size = 120 * 1024 * 1024  # 80 MB
    duration_seconds = 3600  # 1 uur
    max_bytes = 24 * 1024 * 1024

    result = services._select_chunk_duration(file_size, duration_seconds, max_bytes)

    # 24 MB budget en deze bitrate komt neer op ~1080 seconden per chunk.
    assert result == 720


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

def test_select_chunk_duration_accounts_for_pcm_transcode_bitrate():
    file_size = 10 * 1024 * 1024  # 10 MB bronbestand met zeer lage bitrate
    duration_seconds = 3600  # 1 uur
    max_bytes = 24 * 1024 * 1024

    result = services._select_chunk_duration(file_size, duration_seconds, max_bytes)

    # Na re-encoderen naar 16 kHz mono PCM zijn chunks max ~786 seconden.
    assert result == 786


def test_render_vtt_content_preserves_multiline_segments():
    segments = [
        TranslationSegment(start=0.0, end=1.5, text="Line 1\nLine 2", language="combo")
    ]

    rendered = services.render_vtt_content(segments)

    assert rendered.startswith("WEBVTT")
    assert "Line 1\nLine 2" in rendered


def test_build_combined_segments_merges_languages_in_order():
    meta = VideoMetadata(
        id="vid",
        filename="example.mp4",
        original_language="en",
        sentence_pairs=[],
        translations={
            "en": [
                TranslationSegment(start=0.0, end=1.5, text="Hello world", language="en"),
                TranslationSegment(start=2.0, end=3.5, text="How are you?", language="en"),
            ],
            "nl": [
                TranslationSegment(start=0.0, end=1.6, text="Hallo\nwereld", language="nl"),
                TranslationSegment(start=2.0, end=3.2, text="Hoe gaat het?", language="nl"),
            ],
        },
    )

    combined = _build_combined_segments(meta, ["en", "nl"])

    assert len(combined) == 2
    assert combined[0].text == "EN: Hello world\nNL: Hallo wereld"
    assert combined[0].start == 0.0
    assert combined[0].end == 1.6
    assert combined[1].text.startswith("EN: How are you?")


def test_build_combined_segments_requires_two_languages():
    meta = VideoMetadata(
        id="vid",
        filename="single.mp4",
        original_language="en",
        sentence_pairs=[],
        translations={
            "en": [TranslationSegment(start=0.0, end=1.0, text="Hi", language="en")]
        },
    )

    with pytest.raises(ValueError):
        _build_combined_segments(meta, ["en"])
