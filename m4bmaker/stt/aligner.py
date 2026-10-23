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
        main_num = int(num_str) if num_str.isdigit() else parse_kanji_number(num_str)
        if main_num is None and index > 0:
            main_num = index

        sub_title = re.sub(re.escape(m_jp.group(0)), "", norm).strip()
        tokens = sub_title.split()

        sub_val: Optional[int] = None
        sub_counter: Optional[str] = None
        sub_title_clean = sub_title

        # Check for explicit subsection counter (e.g. "第3節", "3節", "2話")
        m_sec = re.search(r"第?([0-9一二三四五六七八九十百]+)([節話部幕])", sub_title)
        if m_sec:
            s_str = m_sec.group(1)
            sub_counter = m_sec.group(2)
            sub_val = int(s_str) if s_str.isdigit() else parse_kanji_number(s_str)
            sub_title_clean = re.sub(re.escape(m_sec.group(0)), "", sub_title).strip()
        elif tokens:
            # Trailing token in subsection title (e.g. "先輩はバニーガール 三" or "先輩はバニーガール 3")
            last_tok = tokens[-1]
            if last_tok.isdigit():
                sub_val = int(last_tok)
            else:
                k_val = parse_kanji_number(last_tok)
                if k_val is not None:
                    sub_val = k_val
                elif last_tok.lower() in _ROMAN_MAP:
                    sub_val = _ROMAN_MAP[last_tok.lower()]
                elif last_tok.lower() in _WORD_TO_NUM:
                    sub_val = int(_WORD_TO_NUM[last_tok.lower()])

            if sub_val is not None:
                sub_title_clean = " ".join(tokens[:-1]).strip()

        sub_title_clean = re.sub(r"\b[0-9一二三四五六七八九十百]+\b", "", sub_title_clean).strip()
        sub_title_clean = " ".join(sub_title_clean.split())

        # 1. Subsection variants (e.g. "3", "三", "3.", "第3節", "第三節")
        if sub_val is not None:
            sub_kanji = int_to_kanji(sub_val)
            s_cnt = sub_counter if sub_counter else "節"
            variants.append(f"第{sub_val}{s_cnt}")
            variants.append(f"第{sub_kanji}{s_cnt}")
            variants.append(f"{sub_val}{s_cnt}")
            variants.append(f"{sub_kanji}{s_cnt}")
            variants.append(f"第{sub_val}章")
            variants.append(f"第{sub_kanji}章")
            variants.append(f"{sub_val}章")
            variants.append(f"{sub_kanji}章")
            variants.append(f"第{sub_val}話")
            variants.append(f"第{sub_kanji}話")
            variants.append(f"{sub_val}話")
            variants.append(f"{sub_kanji}話")
            variants.append(f"{sub_val}.")
            # Only add standalone single digit/kanji if sub_val > 1, or main_num == 1
            if sub_val > 1 or main_num == 1:
                variants.append(str(sub_val))
                variants.append(sub_kanji)

            if sub_title_clean and len(sub_title_clean) > 1:
                variants.append(f"{sub_title_clean} {sub_val}")
                variants.append(f"{sub_title_clean} {sub_kanji}")
                variants.append(f"{sub_title_clean}{sub_val}")
                variants.append(f"{sub_title_clean}{sub_kanji}")
                if main_num is not None:
                    kanji_main = int_to_kanji(main_num)
                    variants.append(f"第{main_num}{counter} {sub_title_clean} {sub_kanji}")
                    variants.append(f"第{kanji_main}{counter} {sub_title_clean} {sub_kanji}")
                    variants.append(f"第{main_num}{counter}{sub_title_clean}{sub_kanji}")
                    variants.append(f"第{kanji_main}{counter}{sub_title_clean}{sub_kanji}")
                variants.append(sub_title_clean)
                variants.append(sub_title_clean.replace(" ", ""))

        # 2. Main chapter variants
        if main_num is not None:
            kanji_num = int_to_kanji(main_num)
            # Only add standalone main chapter numbers/counters if this is not a subsequent subsection
            if sub_val is None or sub_val == 1:
                variants.append(f"第{main_num}{counter}")
                variants.append(f"第{kanji_num}{counter}")
                variants.append(f"{main_num}{counter}")
                variants.append(f"{kanji_num}{counter}")
                if sub_val is None:
                    variants.append(str(main_num))
                    variants.append(kanji_num)

            for st in [sub_title, sub_title_clean]:
                if st and len(st) > 1:
                    variants.append(f"第{main_num}{counter} {st}")
                    variants.append(f"第{kanji_num}{counter} {st}")
                    variants.append(f"第{main_num}{counter}{st}")
                    variants.append(f"第{kanji_num}{counter}{st}")
                    variants.append(st)
                    variants.append(st.replace(" ", ""))
    else:
        # Check for sequential main heading and optional secondary subsection
        m_main = re.search(r"\b(chapter|part|book|chronicle|act|section|volume|track)\s+(\d+|[a-z]+)\b", norm)
        sub_val: Optional[int] = None
        sub_kw: Optional[str] = None
        matched_prefix: Optional[str] = None
        num_val: Optional[int] = None

        if m_main:
            matched_prefix = m_main.group(1)
            main_tok = m_main.group(2)
            if main_tok.isdigit():
                num_val = int(main_tok)
            elif main_tok in _WORD_TO_NUM:
                num_val = int(_WORD_TO_NUM[main_tok])
            elif main_tok in _ROMAN_MAP:
                num_val = _ROMAN_MAP[main_tok]

            rem = norm[m_main.end():]
            m_sub = re.search(r"\b(part|section|scene|act|episode|canto)\s+(\d+|[a-z]+)\b", rem)
            if m_sub:
                sub_kw = m_sub.group(1)
                s_tok = m_sub.group(2)
                if s_tok.isdigit():
                    sub_val = int(s_tok)
                elif s_tok in _WORD_TO_NUM:
                    sub_val = int(_WORD_TO_NUM[s_tok])
                elif s_tok in _ROMAN_MAP:
                    sub_val = _ROMAN_MAP[s_tok]
            else:
                rem_digits = re.findall(r"\b\d+\b", rem)
                if rem_digits:
                    sub_val = int(rem_digits[-1])
        else:
            for kw in all_keywords:
                if re.search(rf"\b{re.escape(kw)}\b", norm):
                    matched_prefix = kw
                    break

            all_digits = re.findall(r"\b\d+\b", norm)
            if len(all_digits) >= 2:
                num_val = int(all_digits[0])
                sub_val = int(all_digits[-1])
            elif all_digits:
                num_val = int(all_digits[0])
            else:
                for word in norm.split():
                    if word in _WORD_TO_NUM:
                        num_val = int(_WORD_TO_NUM[word])
                        break
                if num_val is None:
                    for word in norm.split():
                        if word in _ROMAN_MAP:
                            num_val = _ROMAN_MAP[word]
                            break

        if num_val is None and index > 0:
            num_val = index

        prefix = matched_prefix if matched_prefix else "chapter"

        if sub_val is not None:
            sub_word = _NUM_WORDS.get(sub_val)
            s_prefix = sub_kw if sub_kw else "part"
            variants.append(f"{s_prefix} {sub_val}")
            if sub_word:
                variants.append(f"{s_prefix} {sub_word}")
            variants.append(f"part {sub_val}")
            if sub_word:
                variants.append(f"part {sub_word}")
            variants.append(f"section {sub_val}")
            if sub_word:
                variants.append(f"section {sub_word}")
            variants.append(f"scene {sub_val}")
            if sub_word:
                variants.append(f"scene {sub_word}")
            variants.append(str(sub_val))
            if sub_word:
                variants.append(sub_word)
            if num_val is not None:
                variants.append(f"{prefix} {num_val} {s_prefix} {sub_val}")
                variants.append(f"{prefix} {num_val} {sub_val}")

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
        if sub_kw:
            sub_title = re.sub(rf"\b{re.escape(sub_kw)}\b", "", sub_title)
        if sub_val is not None:
            sub_title = re.sub(rf"\b{sub_val}\b", "", sub_title)
            s_word = _NUM_WORDS.get(sub_val)
            if s_word:
                sub_title = re.sub(rf"\b{re.escape(s_word)}\b", "", sub_title)
        sub_title = " ".join(sub_title.split())

        if num_val is not None:
            word_num = _NUM_WORDS.get(num_val)
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

            # Single canonical number token as last fallback (only if no sub_val or sub_val == num_val)
            if sub_val is None or sub_val == num_val:
                variants.append(str(num_val))
                if word_num:
                    variants.append(word_num)

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

        active_lang = language if language is not None else self.language
        if active_lang and active_lang != "auto":
            transcribe_lang = active_lang
        elif self.model_name.endswith(".en"):
            transcribe_lang = "en"
        else:
            transcribe_lang = None

        # Contextual prompt biasing to prime Whisper decoder on chapter vocabulary
        initial_prompt = None
        if use_prompt_biasing and (chapter_title or custom_cue):
            cue_text = custom_cue if custom_cue else chapter_title
            if has_cjk(cue_text):
                initial_prompt = cue_text
            else:
                initial_prompt = f"Chapter heading: {cue_text}."

        variants = get_title_search_variants(chapter_title, chapter_index, custom_keywords=custom_keywords)
        if custom_cue:
            clean_cue = normalize_text(custom_cue)
            if clean_cue:
                # Insert custom cue phrases at the very top of variants
                variants.insert(0, clean_cue)
                for variant in get_title_search_variants(clean_cue, chapter_index, custom_keywords=custom_keywords):
                    if variant not in variants:
                        variants.insert(1, variant)

        # Staged search slices:
        # 1. Focused window centered on estimated_time_s with anti-hallucination filter
        # 2. Focused window without anti-hallucination filter (fallback for quiet/short chapter numbers)
        # 3. Full search window fallback
        stages: list[tuple[float, float, Optional[float]]] = []
        if win_s > 15.0:
            focused_start = max(0.0, estimated_time_s - 5.0)
            focused_dur = 20.0
            if file_duration_s is not None and file_duration_s > focused_start:
                focused_dur = min(20.0, file_duration_s - focused_start)
            stages.append((focused_start, focused_dur, 2.0))
            stages.append((focused_start, focused_dur, None))

        full_start_s = max(0.0, estimated_time_s - win_s)
        if file_duration_s is not None and file_duration_s > full_start_s:
            full_dur_s = min(win_s * 2.0, file_duration_s - full_start_s)
        else:
            full_dur_s = win_s * 2.0
        if full_dur_s <= 0.1:
            full_dur_s = 1.0
        stages.append((full_start_s, full_dur_s, None))

        last_error = "Chapter title not found in speech transcript."
        last_snippet = ""

        for slice_start_s, slice_dur_s, hal_thresh in stages:
            try:
                wav_bytes = self.extract_audio_slice(
                    file_path=target_file,
                    start_s=slice_start_s,
                    duration_s=slice_dur_s,
                    ffmpeg_bin=ffmpeg_bin,
                )
            except Exception as exc:
                last_error = f"Audio slice extraction failed: {exc}"
                continue

            audio_f32 = wav_bytes_to_float32(wav_bytes)

            transcribe_kwargs: dict[str, Any] = {
                "word_timestamps": True,
                "language": transcribe_lang,
                "initial_prompt": initial_prompt,
                "vad_filter": False,
                "condition_on_previous_text": False,
            }
            if hal_thresh is not None:
                transcribe_kwargs["hallucination_silence_threshold"] = hal_thresh
            else:
                transcribe_kwargs["repetition_penalty"] = 1.15
                transcribe_kwargs["no_repeat_ngram_size"] = 3

            try:
                segments, _ = self._model.transcribe(audio_f32, **transcribe_kwargs)
            except TypeError:
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
                last_snippet = " ".join(full_text_parts)[:100]
                continue

            best_match_idx: Optional[int] = None
            best_score = 0.0
            best_phrase = ""

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

            if best_match_idx is not None:
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
                    confidence=round(float(best_score), 3),
                    snapped_to_silence=snapped,
                    offset_s=round(float(offset_s), 3),
                    success=True,
                    row=target_row,
                )

            last_snippet = " ".join(full_text_parts)[:150]

        return AlignmentResult(
            chapter_index=chapter_index,
            chapter_title=chapter_title,
            original_time_s=estimated_time_s,
            aligned_time_s=estimated_time_s,
            spoken_time_s=estimated_time_s,
            matched_phrase="",
            transcript_snippet=last_snippet,
            confidence=0.0,
            snapped_to_silence=False,
            offset_s=0.0,
            success=False,
            error_message=last_error,
            row=target_row,
        )

    # Backward-compatible alias
    align_chapter = align
