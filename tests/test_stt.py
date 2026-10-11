"""Tests for speech-to-text models, audio aligner, and title matching."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from m4bmaker.stt.aligner import (
    AlignmentResult,
    AudioAligner,
    get_title_search_variants,
    normalize_text,
)
from m4bmaker.stt.models import (
    DEFAULT_MODEL,
    SUPPORTED_MODELS,
    get_model_path,
    get_models_dir,
    is_model_downloaded,
)


def test_audio_aligner_init_accepts_keyword_args() -> None:
    """Ensure AudioAligner.__init__ accepts alignment configuration args without error."""
    aligner = AudioAligner(
        model_name="tiny.en",
        search_window_s=120.0,
        snap_to_silence=False,
        silence_thresh_db=-40.0,
        silence_min_dur_s=0.5,
        extra_future_arg="ignored",
    )
    assert aligner.model_name == "tiny.en"
    assert aligner.search_window_s == 120.0
    assert aligner.snap_to_silence is False
    assert aligner.silence_thresh_db == -40.0
    assert aligner.silence_min_dur_s == 0.5


def test_audio_aligner_has_align_and_align_chapter_aliases() -> None:
    """Verify AudioAligner provides both align and align_chapter methods."""
    aligner = AudioAligner()
    assert hasattr(aligner, "align")
    assert hasattr(aligner, "align_chapter")
    assert aligner.align == aligner.align_chapter


def test_title_search_variants() -> None:
    """Verify title search variants generation."""
    # Digit chapter
    v1 = get_title_search_variants("Chapter 4: An Unexpected Journey", 4)
    assert "chapter 4" in v1
    assert "chapter four" in v1
    assert "4" in v1
    assert "an unexpected journey" in v1

    # Roman numeral chapter
    v2 = get_title_search_variants("Chapter IV", 4)
    assert "chapter 4" in v2
    assert "chapter four" in v2

    # Plain title with index fallback
    v3 = get_title_search_variants("The Shadow Over Innsmouth", 2)
    assert "the shadow over innsmouth" in v3
    assert "chapter 2" in v3


def test_normalize_text() -> None:
    assert normalize_text("Chapter 1: The Beginning!") == "chapter 1 the beginning"
    assert normalize_text("  spaces   and -- dashes ") == "spaces and dashes"


def test_is_model_downloaded_checks_all_required_files(tmp_path: Path) -> None:
    model_dir = tmp_path / "tiny.en"
    model_dir.mkdir(parents=True)

    with patch("m4bmaker.stt.models.get_model_path", return_value=model_dir):
        # Empty folder
        assert not is_model_downloaded("tiny.en")

        # Only model.bin
        bin_file = model_dir / "model.bin"
        bin_file.write_bytes(b"x" * 2000)
        assert not is_model_downloaded("tiny.en")

        # Add vocabulary
        (model_dir / "vocabulary.txt").write_text("token", encoding="utf-8")
        assert not is_model_downloaded("tiny.en")

        # Add config.json
        (model_dir / "config.json").write_text("{}", encoding="utf-8")
        assert is_model_downloaded("tiny.en")


def test_alignment_result_dataclass() -> None:
    res = AlignmentResult(
        chapter_index=1,
        chapter_title="Chapter 1",
        original_time_s=10.0,
        aligned_time_s=12.5,
        spoken_time_s=13.0,
        matched_phrase="chapter 1",
        transcript_snippet="welcome to chapter 1",
        confidence=0.92,
        snapped_to_silence=True,
        offset_s=2.5,
        row=0,
    )
    assert res.success is True
    assert res.row == 0
    assert res.aligned_time_s == 12.5


def test_logging_setup(tmp_path: Path) -> None:
    from m4bmaker import logging_config

    with patch.object(logging_config, "get_log_file_path", return_value=tmp_path / "test.log"):
        log_file = logging_config.setup_logging()
        assert log_file == tmp_path / "test.log"
        import logging

        logging.getLogger("m4bmaker.test").info("Test log entry")
        assert log_file.is_file()
        assert "Test log entry" in log_file.read_text(encoding="utf-8")


def test_wav_bytes_to_float32() -> None:
    import io
    import wave
    import numpy as np
    from m4bmaker.stt.aligner import wav_bytes_to_float32

    # Synthesize 100ms of 16kHz mono 16-bit PCM (silence or simple sine)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        # 1600 samples
        pcm_data = (np.sin(np.linspace(0, 10, 1600)) * 10000).astype(np.int16).tobytes()
        wf.writeframes(pcm_data)

    wav_bytes = buf.getvalue()
    audio_f32 = wav_bytes_to_float32(wav_bytes)

    assert isinstance(audio_f32, np.ndarray)
    assert audio_f32.dtype == np.float32
    assert len(audio_f32) == 1600
    assert np.all(audio_f32 >= -1.0) and np.all(audio_f32 <= 1.0)


def test_align_passes_numpy_to_transcribe() -> None:
    import io
    import wave
    import numpy as np
    from m4bmaker.stt.aligner import AudioAligner

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)
    fake_wav_bytes = buf.getvalue()

    aligner = AudioAligner(model_name="tiny.en")
    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([], MagicMock())
    aligner._model = mock_model

    with patch.object(aligner, "extract_audio_slice", return_value=fake_wav_bytes):
        aligner.align(
            audio_path="dummy.m4b",
            chapter_title="Chapter 1",
            chapter_index=1,
            estimated_time_s=10.0,
        )

    assert mock_model.transcribe.called
    args, kwargs = mock_model.transcribe.call_args
    passed_audio = args[0]
    # Verify passed audio is numpy array (not io.BytesIO) to bypass PyAV av.open
    assert isinstance(passed_audio, np.ndarray)
    assert passed_audio.dtype == np.float32


def test_patch_pyav_handles_unexpected_kwarg() -> None:
    from m4bmaker.stt import _patch_pyav
    import av

    # Simulate an av.open that raises TypeError when metadata_errors is passed
    def mock_av_open(*args, **kwargs):
        if "metadata_errors" in kwargs:
            raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
        return "success"

    orig_open = av.open
    try:
        av.open = mock_av_open
        _patch_pyav()
        result = av.open("fake_path", mode="r", metadata_errors="ignore")
        assert result == "success"
    finally:
        av.open = orig_open


def test_canonical_token_and_compute_token_match() -> None:
    from m4bmaker.stt.aligner import canonical_token, compute_token_match

    # Canonical conversions
    assert canonical_token("fourteen") == "14"
    assert canonical_token("14") == "14"
    assert canonical_token("xiv") == "14"
    assert canonical_token("ten") == "10"
    assert canonical_token("x") == "10"
    assert canonical_token("chapter") == "chapter"

    # Exact canonical equivalence across number representations
    ratio, is_canon = compute_token_match(["chapter", "14"], ["chapter", "fourteen"])
    assert ratio == 1.0
    assert is_canon is True

    ratio, is_canon = compute_token_match(["chapter", "ten"], ["chapter", "10"])
    assert ratio == 1.0
    assert is_canon is True

    # Number token mismatch prevention: "14" or "fourteen" vs "forgotten" MUST return 0.0
    ratio, _ = compute_token_match(["14"], ["forgotten"])
    assert ratio == 0.0

    ratio, _ = compute_token_match(["fourteen"], ["forgotten"])
    assert ratio == 0.0

    # "ten" vs "often" MUST return 0.0
    ratio, _ = compute_token_match(["ten"], ["often"])
    assert ratio == 0.0

    # Target containing number requires window to contain that same number
    ratio, _ = compute_token_match(["part", "14"], ["after", "i"])
    assert ratio == 0.0



