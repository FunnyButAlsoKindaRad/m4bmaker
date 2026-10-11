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
    assert "four" in v1
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
