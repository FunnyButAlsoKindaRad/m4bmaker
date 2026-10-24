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

def test_chronicle_heading_search_variants() -> None:
    """Verify chapter titles like 'Chronicle One: The Marked Man' correctly generate variants."""
    v = get_title_search_variants("Chronicle One: The Marked Man", 1)
    assert "chronicle 1" in v
    assert "chronicle one" in v
    assert "chronicle 1 the marked man" in v
    assert "chronicle one the marked man" in v
    assert "the marked man" in v


def test_custom_heading_keywords() -> None:
    """Verify custom heading keywords generate expected variants."""
    v = get_title_search_variants("Canticle 5: The Song", 5, custom_keywords=["canticle"])
    assert "canticle 5" in v
    assert "canticle five" in v


def test_validate_local_model(tmp_path: Path) -> None:
    from m4bmaker.stt.models import validate_local_model

    # Non-existent folder
    is_valid, msg = validate_local_model(tmp_path / "nonexistent")
    assert not is_valid

    # Empty folder
    m_dir = tmp_path / "test_model"
    m_dir.mkdir()
    is_valid, msg = validate_local_model(m_dir)
    assert not is_valid
    assert "missing 'model.bin'" in msg

    # Safetensors folder
    (m_dir / "model.safetensors").write_bytes(b"x" * 2000)
    is_valid, msg = validate_local_model(m_dir)
    assert not is_valid
    assert "PyTorch/Safetensors" in msg

    # Valid CT2 model
    (m_dir / "model.bin").write_bytes(b"x" * 2000)
    (m_dir / "config.json").write_text("{}", encoding="utf-8")
    is_valid, msg = validate_local_model(m_dir)
    assert is_valid
    assert "Valid CTranslate2" in msg


def test_validate_hf_repo_ct2() -> None:
    from unittest.mock import MagicMock, patch
    from m4bmaker.stt.models import validate_hf_repo_ct2

    # Invalid repo string
    is_valid, msg = validate_hf_repo_ct2("invalid-repo-without-slash")
    assert not is_valid
    assert "Expected format" in msg

    # Valid repo response mock
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"siblings": [{"rfilename": "model.bin"}, {"rfilename": "config.json"}]}'
    with patch("urllib.request.urlopen", return_value=mock_resp):
        is_valid, msg = validate_hf_repo_ct2("Systran/faster-whisper-tiny.en")
        assert is_valid
        assert "Valid CTranslate2" in msg

    # Safetensors response mock
    mock_st_resp = MagicMock()
    mock_st_resp.read.return_value = b'{"siblings": [{"rfilename": "model.safetensors"}, {"rfilename": "config.json"}]}'
    with patch("urllib.request.urlopen", return_value=mock_st_resp):
        is_valid, msg = validate_hf_repo_ct2("openai/whisper-large-v3")
        assert not is_valid
        assert "PyTorch/Safetensors" in msg


def test_prefs_reset_stt_defaults() -> None:
    from m4bmaker.gui import prefs

    prefs.set("stt_window_min", 9)
    prefs.set("stt_pre_chapter_title", "Custom Intro")
    assert prefs.get("stt_window_min") == 9
    assert prefs.get("stt_pre_chapter_title") == "Custom Intro"

    prefs.reset_stt_defaults()
    assert prefs.get("stt_window_min") == 3
    assert prefs.get("stt_pre_chapter_title") == "Chapter 0"



def test_stt_dialog_ui_components(qtbot=None) -> None:
    from PySide6.QtWidgets import QApplication
    from m4bmaker.gui.stt_dialog import SttSettingsDialog

    app = QApplication.instance() or QApplication([])
    dlg = SttSettingsDialog()

    # Active model dropdown strictly contains ONLY tiny.en and base.en
    assert dlg._model_combo.count() == 2
    model_keys = [dlg._model_combo.itemData(i) for i in range(dlg._model_combo.count())]
    assert model_keys == ["tiny.en", "base.en"]

    # Download progress bars have text hidden (no centered percentage number)
    assert dlg._progress_bar.objectName() == "sttDownloadProgress"
    assert dlg._progress_bar.minimumHeight() == 20
    assert not dlg._progress_bar.isTextVisible()
    assert dlg._adv_progress_bar.objectName() == "sttDownloadProgress"
    assert dlg._adv_progress_bar.minimumHeight() == 20
    assert not dlg._adv_progress_bar.isTextVisible()

    # Advanced tab custom model override
    assert hasattr(dlg, "_adv_custom_combo")
    assert dlg._adv_custom_combo.itemData(0) == ""
    assert "None" in dlg._adv_custom_combo.itemText(0)

    dlg._on_download_progress(0.75, "Downloading model.bin...")
    assert dlg._progress_bar.value() == 75
    assert dlg._adv_progress_bar.value() == 75
    assert dlg._adv_status_lbl.text() == "Downloading model.bin..."

    app.clipboard().setText("Systran/faster-whisper-small")
    dlg._on_paste_repo_id()
    assert dlg._hf_repo_edit.text() == "Systran/faster-whisper-small"
    assert not dlg._paste_action.isEnabled()
    assert dlg._paste_action.toolTip() == "Pasted from clipboard"

    dlg._hf_repo_edit.textEdited.emit("Systran/faster-whisper-tiny")
    assert dlg._paste_action.isEnabled()
    assert dlg._paste_action.toolTip() == "Paste from clipboard"

    dlg.close()


def test_kanji_number_parsing() -> None:
    from m4bmaker.stt.aligner import parse_kanji_number, int_to_kanji

    assert parse_kanji_number("一") == 1
    assert parse_kanji_number("二") == 2
    assert parse_kanji_number("三") == 3
    assert parse_kanji_number("十") == 10
    assert parse_kanji_number("十一") == 11
    assert parse_kanji_number("二十") == 20
    assert parse_kanji_number("二十三") == 23
    assert parse_kanji_number("百") == 100
    assert parse_kanji_number("12") == 12

    assert int_to_kanji(1) == "一"
    assert int_to_kanji(3) == "三"
    assert int_to_kanji(10) == "十"
    assert int_to_kanji(15) == "十五"
    assert int_to_kanji(21) == "二十一"


def test_japanese_chapter_search_variants() -> None:
    from m4bmaker.stt.aligner import get_title_search_variants

    variants = get_title_search_variants("第一章～先輩はバニーガール・一", 3)
    assert "第1章" in variants
    assert "第一章" in variants
    assert "1章" in variants
    assert "一章" in variants
    assert "先輩はバニーガール" in variants
    assert "1" in variants

    # Digits with Japanese counter
    variants2 = get_title_search_variants("第12話 最終決戦", 12)
    assert "第12話" in variants2
    assert "第十二話" in variants2
    assert "12話" in variants2
    assert "最終決戦" in variants2


    # Japanese subsection with sub_val != main_num (e.g. Chapter 5)
    variants_sub = get_title_search_variants("第一章～先輩はバニーガール・三", 5)
    assert "3" in variants_sub
    assert "三" in variants_sub
    assert "3." in variants_sub
    assert "第3節" in variants_sub
    assert "第三節" in variants_sub
    assert "先輩はバニーガール" in variants_sub
    # Crucially, main number '1' must NOT be in variants to prevent false kanji matches
    assert "1" not in variants_sub
    assert "一" not in variants_sub
    assert "第一章" not in variants_sub
    assert "第1章" not in variants_sub

    # English subsection with sub_val != main_num
    variants_en_sub = get_title_search_variants("Chapter 1, Part 3", 3)
    assert "part 3" in variants_en_sub
    assert "part three" in variants_en_sub
    assert "section 3" in variants_en_sub
    assert "3" in variants_en_sub
    assert "three" in variants_en_sub
    assert "1" not in variants_en_sub
    assert "one" not in variants_en_sub

    # Act and scene
    variants_act = get_title_search_variants("Act 2, Scene 4", 4)
    assert "scene 4" in variants_act
    assert "scene four" in variants_act
    assert "4" in variants_act
    assert "four" in variants_act
    assert "2" not in variants_act
    assert "two" not in variants_act


def test_unicode_nfkc_normalization() -> None:
    from m4bmaker.stt.aligner import normalize_text

    # Fullwidth digits and characters
    norm = normalize_text("第１章～先輩")
    assert "1" in norm
    assert "章" in norm


def test_parse_audible_input() -> None:
    from m4bmaker.providers import parse_audible_input

    # Raw ASIN
    asin, reg = parse_audible_input("B0841ZVX5R")
    assert asin == "B0841ZVX5R"
    assert reg is None

    # Japanese URL
    asin_jp, reg_jp = parse_audible_input("https://www.audible.co.jp/pd/B0841ZVX5R?qid=123")
    assert asin_jp == "B0841ZVX5R"
    assert reg_jp == "jp"

    # German URL
    asin_de, reg_de = parse_audible_input("https://www.audible.de/pd/B0841ZVX5R")
    assert asin_de == "B0841ZVX5R"
    assert reg_de == "de"


def test_sanitize_model_config(tmp_path: Path) -> None:
    import json
    from m4bmaker.stt.models import sanitize_model_config

    model_dir = tmp_path / "kotoba-whisper-test"
    model_dir.mkdir()
    # Create config with corrupted large-v3 alignment heads referencing layer 25
    cfg = {"alignment_heads": [[7, 0], [10, 17], [25, 6]]}
    (model_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (model_dir / "model.bin").write_bytes(b"x" * 5000)

    sanitize_model_config(model_dir)

    sanitized = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    assert sanitized["alignment_heads"] == [[0, 0], [1, 0]]

