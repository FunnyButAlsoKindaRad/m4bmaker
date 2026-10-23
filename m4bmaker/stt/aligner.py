"""Core audio alignment logic using faster-whisper and fuzzy title matching."""

from __future__ import annotations

import difflib
import io
import logging
import re
import subprocess
import threading
import unicodedata
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Union

import numpy as np

from m4bmaker.stt.models import (
    DEFAULT_MODEL,
    get_model_path,
    is_model_downloaded,
    sanitize_model_config,
)
from m4bmaker.stt.silence import snap_to_preceding_silence
from m4bmaker.utils import find_ffmpeg, subprocess_flags

_log = logging.getLogger(__name__)

# Number word mappings for chapters 1-99
_NUM_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
    11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen",
    16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen", 20: "twenty",
    21: "twenty-one", 22: "twenty-two", 23: "twenty-three", 24: "twenty-four", 25: "twenty-five",
    30: "thirty", 40: "forty", 50: "fifty", 60: "sixty", 70: "seventy", 80: "eighty", 90: "ninety",
}

_ROMAN_MAP = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
    "xi": 11, "xii": 12, "xiii": 13, "xiv": 14, "xv": 15, "xvi": 16, "xvii": 17, "xviii": 18, "xix": 19, "xx": 20,
}

_KANJI_DIGITS = {
    "〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_KANJI_TO_NUM = {k: str(v) for k, v in _KANJI_DIGITS.items()}

_WORD_TO_NUM = {v: str(k) for k, v in _NUM_WORDS.items()}
_ROMAN_TO_NUM = {k: str(v) for k, v in _ROMAN_MAP.items()}

_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")


def has_cjk(text: str) -> bool:
    """Return True if text contains Chinese, Japanese, or Korean characters."""
    return bool(_CJK_RE.search(text))


def parse_kanji_number(text: str) -> Optional[int]:
    """Parse a Japanese Kanji numeral string (e.g. '一', '十二', '二十五', '百') into an integer."""
    text = text.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text == "百":
        return 100
    if "百" in text:
        parts = text.split("百", 1)
        hundreds = _KANJI_DIGITS.get(parts[0], 1) if parts[0] else 1
        remainder = parse_kanji_number(parts[1]) if parts[1] else 0
        return hundreds * 100 + (remainder or 0)
    if "十" in text:
        parts = text.split("十", 1)
        tens = _KANJI_DIGITS.get(parts[0], 1) if parts[0] else 1
        ones = _KANJI_DIGITS.get(parts[1], 0) if parts[1] else 0
        return tens * 10 + ones
    if len(text) == 1 and text in _KANJI_DIGITS:
        return _KANJI_DIGITS[text]
    val = 0
    for ch in text:
        if ch in _KANJI_DIGITS:
            val = val * 10 + _KANJI_DIGITS[ch]
        else:
            return None
    return val if val > 0 else None


def int_to_kanji(num: int) -> str:
    """Convert integer 1-99 to standard Japanese Kanji numeral."""
    if num <= 0 or num >= 100:
        return str(num)
    kanji_ones = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九"]
    if num < 10:
        return kanji_ones[num]
    tens = num // 10
    ones = num % 10
    prefix = "十" if tens == 1 else f"{kanji_ones[tens]}十"
    return f"{prefix}{kanji_ones[ones]}"


def canonical_token(tok: str) -> str:
    """Normalize a token so numbers in word, roman, kanji, or digit form match uniformly."""
    cleaned = tok.lower().strip()
    if cleaned in _WORD_TO_NUM:
        return _WORD_TO_NUM[cleaned]
    if cleaned in _ROMAN_TO_NUM:
        return _ROMAN_TO_NUM[cleaned]
    if cleaned in _KANJI_TO_NUM:
        return _KANJI_TO_NUM[cleaned]
    return cleaned


def compute_token_match(target_tokens: list[str], window_tokens: list[str]) -> tuple[float, bool]:
    """Compare a window of tokens against target tokens with canonical number normalization.

    Returns (score, is_exact_canonical).
    """
    if len(target_tokens) != len(window_tokens):
        return (0.0, False)

    t_canon = [canonical_token(t) for t in target_tokens]
    w_canon = [canonical_token(w) for w in window_tokens]

    if t_canon == w_canon:
        return (1.0, True)

    # Check for CJK direct substring or unspaced exact match
    t_raw = "".join(target_tokens)
    w_raw = "".join(window_tokens)
    if has_cjk(t_raw) or has_cjk(w_raw):
        t_canon_raw = "".join(t_canon)
        w_canon_raw = "".join(w_canon)
        if t_canon_raw == w_canon_raw:
            return (1.0, True)
        if len(t_canon_raw) >= 2 and (t_canon_raw in w_canon_raw or w_canon_raw in t_canon_raw):
            ratio = min(len(t_canon_raw), len(w_canon_raw)) / max(len(t_canon_raw), len(w_canon_raw))
            if ratio >= 0.7:
                return (ratio, False)

    # If target contains a number token, window MUST contain that exact number token!
    # This prevents 'fourteen' from fuzzy matching 'forgotten', or 'ten' matching 'often'.
    t_numbers = {tok for tok in t_canon if tok.isdigit()}
    if t_numbers:
        w_numbers = {tok for tok in w_canon if tok.isdigit()}
        if not t_numbers.issubset(w_numbers):
            return (0.0, False)

    if len(target_tokens) == 1:
        if t_canon[0].isdigit() or w_canon[0].isdigit():
            return (0.0, False)
        ratio = difflib.SequenceMatcher(None, t_canon[0], w_canon[0]).ratio()
        return (ratio, False)

    t_str = " ".join(t_canon)
    w_str = " ".join(w_canon)
    ratio = difflib.SequenceMatcher(None, t_str, w_str).ratio()
    return (ratio, False)


@dataclass
class AlignmentResult:
    chapter_index: int
    chapter_title: str
    original_time_s: float
    aligned_time_s: float
    spoken_time_s: float
    matched_phrase: str
    transcript_snippet: str
    confidence: float
    snapped_to_silence: bool
    offset_s: float
    success: bool = True
    error_message: Optional[str] = None
    row: Optional[int] = None


def wav_bytes_to_float32(wav_bytes: bytes) -> np.ndarray:
    """Convert 16kHz mono WAV bytes to a 1D float32 numpy array normalized to [-1.0, 1.0]."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        n_frames = wf.getnframes()
        raw_pcm = wf.readframes(n_frames)

    if sampwidth == 2:
        audio = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32) / 32768.0
    elif sampwidth == 4:
        audio = np.frombuffer(raw_pcm, dtype=np.int32).astype(np.float32) / 2147483648.0
    elif sampwidth == 1:
        audio = (np.frombuffer(raw_pcm, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        audio = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32) / 32768.0

    if n_channels > 1:
        audio = audio.reshape(-1, n_channels).mean(axis=1)

    return audio


def normalize_text(text: str) -> str:
    """Lowercase and strip punctuation to space-delimited words, applying Unicode NFKC normalization."""
    normalized = unicodedata.normalize("NFKC", text)
    cleaned = re.sub(r"[^\w\s]", " ", normalized.lower())
    return " ".join(cleaned.split())


DEFAULT_HEADING_KEYWORDS = [
    "chapter", "part", "book", "chronicle", "act", "scene",
    "section", "episode", "volume", "canto", "letter", "track", "story"
]


def get_title_search_variants(
    title: str,
    index: int,
    custom_keywords: Optional[list[str]] = None,
) -> list[str]:
    """Generate search phrases for a chapter heading (e.g. 'Chronicle One: The Marked Man' -> variants)."""
    norm = normalize_text(title)
    variants = [norm]
    if has_cjk(norm):
        variants.append(norm.replace(" ", ""))

    # Combine default keywords with user custom keywords
    all_keywords = list(DEFAULT_HEADING_KEYWORDS)
    if custom_keywords:
        for kw in custom_keywords:
            c_kw = kw.strip().lower()
            if c_kw and c_kw not in all_keywords:
                all_keywords.append(c_kw)

    # Check for Japanese chapter counter patterns (e.g. "第一章", "第1章", "3話", "第二部")
    m_jp = re.search(r"第?([0-9一二三四五六七八九十百]+)([章話部幕巻節])", norm)
    if m_jp:
        num_str = m_jp.group(1)
        counter = m_jp.group(2)
        num_val = int(num_str) if num_str.isdigit() else parse_kanji_number(num_str)
        if num_val is None and index > 0:
            num_val = index

        if num_val is not None:
            kanji_num = int_to_kanji(num_val)
            variants.append(f"第{num_val}{counter}")
            variants.append(f"第{kanji_num}{counter}")
            variants.append(f"{num_val}{counter}")
            variants.append(f"{kanji_num}{counter}")

            # Subtitle without the heading counter
            sub_title = re.sub(re.escape(m_jp.group(0)), "", norm).strip()
            # Clean trailing standalone digits or kanji numbers
            sub_title_clean = re.sub(r"\b[0-9一二三四五六七八九十百]+\b", "", sub_title).strip()
            sub_title_clean = " ".join(sub_title_clean.split())

            for st in [sub_title, sub_title_clean]:
                if st and len(st) > 1:
                    variants.append(f"第{num_val}{counter} {st}")
                    variants.append(f"第{kanji_num}{counter} {st}")
                    variants.append(f"第{num_val}{counter}{st}")
                    variants.append(f"第{kanji_num}{counter}{st}")
                    variants.append(st)
                    variants.append(st.replace(" ", ""))

            variants.append(str(num_val))
    else:
        # 1. Extract number:
        # First: check digits (\b\d+\b)
        m_num = re.search(r"\b(\d+)\b", norm)
        num_val: Optional[int] = None
        if m_num:
            num_val = int(m_num.group(1))
        else:
            # Second: check number words in norm (e.g. "one", "two", "fourteen")
            for word in norm.split():
                if word in _WORD_TO_NUM:
                    num_val = int(_WORD_TO_NUM[word])
                    break
            # Third: check roman numerals (e.g. "iv", "ix")
            if num_val is None:
                for word in norm.split():
                    if word in _ROMAN_MAP:
                        num_val = _ROMAN_MAP[word]
                        break

        if num_val is None and index > 0:
            num_val = index

        # 2. Identify heading prefix keyword (e.g. "chronicle", "chapter", "book")
        matched_prefix: Optional[str] = None
        for kw in all_keywords:
            if re.search(rf"\b{re.escape(kw)}\b", norm):
                matched_prefix = kw
                break

        # 3. Extract subtitle without heading keyword, number, or roman numeral
        sub_title = norm
        if matched_prefix:
            sub_title = re.sub(rf"\b{re.escape(matched_prefix)}\b", "", sub_title)
        if num_val is not None:
            sub_title = re.sub(rf"\b{num_val}\b", "", sub_title)
            w_word = _NUM_WORDS.get(num_val)
            if w_word:
                sub_title = re.sub(rf"\b{re.escape(w_word)}\b", "", sub_title)
            for r_num, r_val in _ROMAN_MAP.items():
                if r_val == num_val:
                    sub_title = re.sub(rf"\b{re.escape(r_num)}\b", "", sub_title)
        sub_title = " ".join(sub_title.split())

        prefix = matched_prefix if matched_prefix else "chapter"
        if num_val is not None:
            word_num = _NUM_WORDS.get(num_val)

            # Primary heading prefix variants: "chronicle 1", "chronicle one"
            variants.append(f"{prefix} {num_val}")
            if word_num:
                variants.append(f"{prefix} {word_num}")

            # Subtitle combinations
            if sub_title and len(sub_title) > 2:
                variants.append(f"{prefix} {num_val} {sub_title}")
                if word_num:
                    variants.append(f"{prefix} {word_num} {sub_title}")
                variants.append(f"{num_val} {sub_title}")
                if word_num:
                    variants.append(f"{word_num} {sub_title}")

            # Standard "chapter X" fallback if prefix was different (e.g. "book 1" -> also try "chapter 1")
            if prefix != "chapter":
                variants.append(f"chapter {num_val}")
                if word_num:
                    variants.append(f"chapter {word_num}")

            # Single canonical number token as last fallback
            variants.append(str(num_val))

        if sub_title and len(sub_title) > 3:
            variants.append(sub_title)

    # Check for Japanese standalone book keywords
    for kw in ["序章", "プロローグ", "エピローグ", "終章", "あとがき", "幕間"]:
        if kw in norm:
            variants.append(kw)

    # Unique, sorted by token count descending, then length descending
    seen = set()
    res = []
    for v in sorted(variants, key=lambda x: (len(x.split()), len(x)), reverse=True):
        if v and v not in seen:
            seen.add(v)
            res.append(v)
    return res


_MODEL_CACHE: dict[tuple[str, str, str], Any] = {}
_MODEL_CACHE_LOCK = threading.Lock()


def get_cached_whisper_model(
    model_name: str = DEFAULT_MODEL,
    device: str = "cpu",
    compute_type: str = "int8",
) -> Any:
    """Return a cached WhisperModel instance, loading it into memory if not already cached."""
    key = (model_name, device, compute_type)
    with _MODEL_CACHE_LOCK:
        if key in _MODEL_CACHE:
            return _MODEL_CACHE[key]

        from faster_whisper import WhisperModel

        model_path = get_model_path(model_name)
        if model_path.is_dir():
            sanitize_model_config(model_path)

        if is_model_downloaded(model_name):
            load_target = str(model_path)
        else:
            load_target = model_name

        _log.info("Loading faster-whisper model '%s' on %s (%s)...", load_target, device, compute_type)
        try:
            model = WhisperModel(
                load_target,
                device=device,
                compute_type=compute_type,
                download_root=str(model_path.parent),
            )
        except Exception as exc:
            _log.warning(
                "Loading faster-whisper with compute_type='%s' failed (%s); falling back to 'default'",
                compute_type,
                exc,
            )
            model = WhisperModel(
                load_target,
                device=device,
                compute_type="default",
                download_root=str(model_path.parent),
            )

        # Auto-adjust feature extractor mel filters (e.g. 128 for Whisper-v3 / Kotoba vs 80 default)
        expected_mels = getattr(model.model, "n_mels", None)
        if expected_mels and hasattr(model, "feature_extractor"):
            if model.feature_extractor.mel_filters.shape[0] != expected_mels:
                from faster_whisper.feature_extractor import FeatureExtractor

                _log.info("Auto-adjusting feature extractor mel filters to %d", expected_mels)
                model.feature_extractor = FeatureExtractor(feature_size=expected_mels)

        _MODEL_CACHE[key] = model
        return model


def clear_model_cache() -> None:
    """Clear cached WhisperModel instances to free memory."""
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.clear()


class AudioAligner:
    """Manages audio slicing, faster-whisper transcription, and alignment."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: str = "cpu",
        compute_type: str = "int8",
        search_window_s: float = 180.0,
        snap_to_silence: bool = True,
        silence_thresh_db: float = -35.0,
        silence_min_dur_s: float = 0.3,
        language: str = "auto",
        **kwargs: Any,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.search_window_s = search_window_s
        self.snap_to_silence = snap_to_silence
        self.silence_thresh_db = silence_thresh_db
        self.silence_min_dur_s = silence_min_dur_s
        self.language = language
        self._model = None

    def _ensure_model(self) -> None:
        """Load the model into memory if not already cached."""
        if self._model is not None:
            return
        self._model = get_cached_whisper_model(
            model_name=self.model_name,
            device=self.device,
            compute_type=self.compute_type,
        )

    def extract_audio_slice(
        self,
        file_path: Path,
        start_s: float,
        duration_s: float,
        ffmpeg_bin: Optional[str] = None,
    ) -> bytes:
        """Extract a 16kHz mono WAV slice directly to in-memory bytes."""
        if ffmpeg_bin is None:
            ffmpeg_bin = find_ffmpeg()

        cmd = [
            ffmpeg_bin,
            "-v", "error",
            "-ss", f"{start_s:.3f}",
            "-t", f"{duration_s:.3f}",
            "-i", str(file_path),
            "-vn",
            "-ar", "16000",
            "-ac", "1",
            "-c:a", "pcm_s16le",
            "-f", "wav",
            "pipe:1",
        ]

        proc = subprocess.run(
            cmd,
            capture_output=True,
            check=True,
            **subprocess_flags(),
        )
        return proc.stdout

    def align(
        self,
        audio_path: Optional[Union[Path, str]] = None,
        chapter_title: str = "",
        chapter_index: int = 1,
        estimated_time_s: float = 0.0,
        file_path: Optional[Union[Path, str]] = None,
        file_duration_s: Optional[float] = None,
        search_window_s: Optional[float] = None,
        window_radius_s: Optional[float] = None,
        snap_to_silence: Optional[bool] = None,
        enable_silence_snap: Optional[bool] = None,
        silence_thresh_db: Optional[float] = None,
        noise_db: Optional[float] = None,
        silence_min_dur_s: Optional[float] = None,
        min_silence_s: Optional[float] = None,
        row: Optional[int] = None,
        ffmpeg_bin: Optional[str] = None,
        custom_keywords: Optional[list[str]] = None,
        custom_cue: Optional[str] = None,
        use_prompt_biasing: bool = True,
        language: Optional[str] = None,
        **kwargs: Any,
    ) -> AlignmentResult:
        """Transcribe around estimated_time_s and pinpoint the spoken chapter title."""
        self._ensure_model()

        target_file = Path(audio_path if audio_path is not None else (file_path if file_path is not None else ""))
        win_s = float(
            search_window_s
            if search_window_s is not None
            else (window_radius_s if window_radius_s is not None else self.search_window_s)
        )
        snap = bool(
            snap_to_silence
            if snap_to_silence is not None
            else (enable_silence_snap if enable_silence_snap is not None else self.snap_to_silence)
        )
        thresh_db = float(
            silence_thresh_db
            if silence_thresh_db is not None
            else (noise_db if noise_db is not None else self.silence_thresh_db)
        )
        min_sil_s = float(
            silence_min_dur_s
            if silence_min_dur_s is not None
            else (min_silence_s if min_silence_s is not None else self.silence_min_dur_s)
        )
        target_row = row if row is not None else (chapter_index - 1)

        slice_start_s = max(0.0, estimated_time_s - win_s)
        if file_duration_s is not None and file_duration_s > slice_start_s:
            slice_dur_s = min(win_s * 2.0, file_duration_s - slice_start_s)
        else:
            slice_dur_s = win_s * 2.0

        if slice_dur_s <= 0.1:
            slice_dur_s = 1.0

        try:
            wav_bytes = self.extract_audio_slice(
                file_path=target_file,
                start_s=slice_start_s,
                duration_s=slice_dur_s,
                ffmpeg_bin=ffmpeg_bin,
            )
        except Exception as exc:
            return AlignmentResult(
                chapter_index=chapter_index,
                chapter_title=chapter_title,
                original_time_s=estimated_time_s,
                aligned_time_s=estimated_time_s,
                spoken_time_s=estimated_time_s,
                matched_phrase="",
                transcript_snippet="",
                confidence=0.0,
                snapped_to_silence=False,
                offset_s=0.0,
                success=False,
                error_message=f"Audio slice extraction failed: {exc}",
                row=target_row,
            )

        active_lang = language if language is not None else self.language
        if active_lang and active_lang != "auto":
            transcribe_lang = active_lang
        elif self.model_name.endswith(".en"):
            transcribe_lang = "en"
        else:
            transcribe_lang = None

        audio_f32 = wav_bytes_to_float32(wav_bytes)

        # Contextual prompt biasing to prime Whisper decoder on chapter vocabulary
        initial_prompt = None
        if use_prompt_biasing and (chapter_title or custom_cue):
            cue_text = custom_cue if custom_cue else chapter_title
            if has_cjk(cue_text):
                initial_prompt = cue_text
            else:
                initial_prompt = f"Chapter heading: {cue_text}."

        segments, _ = self._model.transcribe(
            audio_f32,
            word_timestamps=True,
            language=transcribe_lang,
            initial_prompt=initial_prompt,
            vad_filter=False,
            condition_on_previous_text=False,
        )

        all_words = []
        full_text_parts = []
        for seg in segments:
            full_text_parts.append(seg.text)
            if seg.words:
                all_words.extend(seg.words)

        if not all_words:
            return AlignmentResult(
                chapter_index=chapter_index,
                chapter_title=chapter_title,
                original_time_s=estimated_time_s,
                aligned_time_s=estimated_time_s,
                spoken_time_s=estimated_time_s,
                matched_phrase="",
                transcript_snippet=" ".join(full_text_parts)[:100],
                confidence=0.0,
                snapped_to_silence=False,
                offset_s=0.0,
                success=False,
                error_message="No words detected in audio window.",
                row=target_row,
            )

        variants = get_title_search_variants(chapter_title, chapter_index, custom_keywords=custom_keywords)
        if custom_cue:
            clean_cue = normalize_text(custom_cue)
            if clean_cue:
                # Insert custom cue phrases at the very top of variants
                variants.insert(0, clean_cue)
                for variant in get_title_search_variants(clean_cue, chapter_index, custom_keywords=custom_keywords):
                    if variant not in variants:
                        variants.insert(1, variant)

        best_match_idx: Optional[int] = None
        best_score = 0.0
        best_phrase = ""

        # Normalize all words for sequence scanning
        clean_words = [normalize_text(w.word) for w in all_words]

        # Scan sliding windows across words
        for target_phrase in variants:
            target_tokens = target_phrase.split()
            k = len(target_tokens)
            if k == 0:
                continue

            target_compact = "".join(target_tokens)
            is_cjk_target = has_cjk(target_compact)
            is_single = (k == 1)
            min_thresh = 0.85 if is_single else 0.70

            # 1. Standard word token sliding window
            for i in range(len(clean_words) - k + 1):
                window_tokens = clean_words[i : i + k]
                ratio, is_canon = compute_token_match(target_tokens, window_tokens)
                if ratio >= min_thresh:
                    abs_time = slice_start_s + all_words[i].start
                    dist_penalty = min(0.15, abs(abs_time - estimated_time_s) / (win_s * 10))
                    len_bonus = 0.10 if not is_single else 0.0
                    adjusted_score = ratio + len_bonus - dist_penalty
                    if adjusted_score > best_score:
                        best_score = adjusted_score
                        best_match_idx = i
                        best_phrase = " ".join(window_tokens)

            # 2. CJK multi-token concatenated matching (handles Japanese unspaced tokens)
            if is_cjk_target and len(target_compact) >= 2:
                t_canon_compact = "".join(canonical_token(t) for t in target_tokens)
                max_w = min(12, len(clean_words))
                for i in range(len(clean_words)):
                    accum = []
                    for w_len in range(1, max_w + 1):
                        if i + w_len > len(clean_words):
                            break
                        accum.append(canonical_token(clean_words[i + w_len - 1]))
                        w_compact = "".join(accum)
                        if len(w_compact) > len(t_canon_compact) + 4:
                            break
                        if w_compact == t_canon_compact:
                            ratio = 1.0
                        elif len(t_canon_compact) >= 3 and (t_canon_compact in w_compact or w_compact in t_canon_compact):
                            ratio = min(len(t_canon_compact), len(w_compact)) / max(len(t_canon_compact), len(w_compact))
                        else:
                            continue

                        if ratio >= 0.75:
                            abs_time = slice_start_s + all_words[i].start
                            dist_penalty = min(0.15, abs(abs_time - estimated_time_s) / (win_s * 10))
                            adjusted_score = ratio + 0.10 - dist_penalty
                            if adjusted_score > best_score:
                                best_score = adjusted_score
                                best_match_idx = i
                                best_phrase = "".join(clean_words[i : i + w_len])

        if best_match_idx is None:
            snippet = " ".join(full_text_parts)[:150]
            return AlignmentResult(
                chapter_index=chapter_index,
                chapter_title=chapter_title,
                original_time_s=estimated_time_s,
                aligned_time_s=estimated_time_s,
                spoken_time_s=estimated_time_s,
                matched_phrase="",
                transcript_snippet=snippet,
                confidence=0.0,
                snapped_to_silence=False,
                offset_s=0.0,
                success=False,
                error_message="Chapter title not found in speech transcript.",
                row=target_row,
            )

        # Spoken time is start time of first word in match
        matched_word = all_words[best_match_idx]
        spoken_time_s = slice_start_s + matched_word.start

        # Context snippet around match
        snip_start = max(0, best_match_idx - 5)
        snip_end = min(len(all_words), best_match_idx + 12)
        snippet = " ".join(w.word for w in all_words[snip_start:snip_end]).strip()

        aligned_time_s = spoken_time_s
        snapped = False

        if snap:
            silence_mid = snap_to_preceding_silence(
                file_path=target_file,
                spoken_time_s=spoken_time_s,
                search_before_s=8.0,
                noise_db=thresh_db,
                min_duration_s=min_sil_s,
                ffmpeg_bin=ffmpeg_bin,
            )
            if silence_mid is not None and silence_mid <= spoken_time_s:
                aligned_time_s = silence_mid
                snapped = True

        offset_s = aligned_time_s - estimated_time_s

        return AlignmentResult(
            chapter_index=int(chapter_index),
            chapter_title=str(chapter_title),
            original_time_s=float(estimated_time_s),
            aligned_time_s=round(float(aligned_time_s), 3),
            spoken_time_s=round(float(spoken_time_s), 3),
            matched_phrase=str(best_phrase),
            transcript_snippet=str(snippet),
            confidence=round(float(best_score), 2),
            snapped_to_silence=bool(snapped),
            offset_s=round(float(offset_s), 3),
            success=True,
            row=int(target_row) if target_row is not None else None,
        )

    # Backward-compatible alias
    align_chapter = align
