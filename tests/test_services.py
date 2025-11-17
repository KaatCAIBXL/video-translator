from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest
from openai import InternalServerError


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

    combined = _build_combined_segments(meta.translations, ["en", "nl"])

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
        _build_combined_segments(meta.translations, ["en"])
def _build_dummy_client(create_fn):
    return SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(create=create_fn)
        )
    )


def _make_internal_error():
    return InternalServerError(
        "boom",
        response=httpx.Response(500, request=httpx.Request("POST", "https://api.openai.com")),
        body=None,
    )


def test_transcribe_whisper_file_retries_on_internal_error(tmp_path, monkeypatch):
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"dummy")

    attempts = {"count": 0}

    def fake_create(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise _make_internal_error()

        class DummyResponse:
            def to_dict(self):
                return {"text": "ok"}

        return DummyResponse()

    client = _build_dummy_client(fake_create)
    monkeypatch.setattr(services, "_TRANSCRIBE_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(services, "_TRANSCRIBE_INITIAL_BACKOFF", 0)
    monkeypatch.setattr(services.time, "sleep", lambda *_: None)

    result = services._transcribe_whisper_file(client, audio_path)

    assert result["text"] == "ok"
    assert attempts["count"] == 3


def test_transcribe_whisper_file_raises_after_retries(tmp_path, monkeypatch):
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"dummy")

    attempts = {"count": 0}

    def fake_create(*args, **kwargs):
        attempts["count"] += 1
        raise _make_internal_error()

    client = _build_dummy_client(fake_create)
    monkeypatch.setattr(services, "_TRANSCRIBE_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(services, "_TRANSCRIBE_INITIAL_BACKOFF", 0)
    monkeypatch.setattr(services.time, "sleep", lambda *_: None)

    with pytest.raises(RuntimeError):
        services._transcribe_whisper_file(client, audio_path)

    assert attempts["count"] == 2

def test_edge_rate_from_speed_handles_small_adjustments():
    assert services._edge_rate_from_speed(1.05) == "+5%"
    assert services._edge_rate_from_speed(0.97) == "-3%"
    assert services._edge_rate_from_speed(1.0) is None
