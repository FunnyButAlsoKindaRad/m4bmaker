"""Core audio alignment logic using faster-whisper and fuzzy title matching."""

from __future__ import annotations

import difflib
import io
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Union

from m4bmaker.stt.models import DEFAULT_MODEL, get_model_path, is_model_downloaded
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


def normalize_text(text: str) -> str:
    """Lowercase and strip punctuation to space-delimited words."""
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return " ".join(cleaned.split())


def get_title_search_variants(title: str, index: int) -> list[str]:
    """Generate search phrases for a chapter heading (e.g. 'Chapter 4: The Inn' -> variants)."""
    norm = normalize_text(title)
    variants = [norm]

    # Extract digits or roman numerals
    m_num = re.search(r"\b(\d+)\b", norm)
    num_val: Optional[int] = None
    if m_num:
        num_val = int(m_num.group(1))
    else:
        # Check roman
        for word in norm.split():
            if word in _ROMAN_MAP:
                num_val = _ROMAN_MAP[word]
                break

    if num_val is None and index > 0:
        num_val = index

    if num_val is not None:
        word_num = _NUM_WORDS.get(num_val)
        variants.append(f"chapter {num_val}")
        if word_num:
            variants.append(f"chapter {word_num}")
            variants.append(word_num)
        variants.append(f"part {num_val}")

    # Also add stripped subtitle without "chapter X"
    sub_title = re.sub(r"^chapter\s+\w+\s*", "", norm).strip()
    if sub_title and len(sub_title) > 3:
        variants.append(sub_title)

    # Unique, sorted by length descending
    seen = set()
    res = []
    for v in sorted(variants, key=len, reverse=True):
        if v and v not in seen:
            seen.add(v)
            res.append(v)
    return res


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
        **kwargs: Any,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.search_window_s = search_window_s
        self.snap_to_silence = snap_to_silence
        self.silence_thresh_db = silence_thresh_db
        self.silence_min_dur_s = silence_min_dur_s
        self._model = None

    def _ensure_model(self) -> None:
        """Load the model into memory if not already cached."""
        if self._model is not None:
            return

        from faster_whisper import WhisperModel

        model_path = get_model_path(self.model_name)
        if is_model_downloaded(self.model_name):
            load_target = str(model_path)
        else:
            load_target = self.model_name

        _log.info("Loading faster-whisper model '%s' on %s (%s)...", load_target, self.device, self.compute_type)
        try:
            self._model = WhisperModel(
                load_target,
                device=self.device,
                compute_type=self.compute_type,
                download_root=str(model_path.parent),
            )
        except Exception as exc:
            _log.warning(
                "Loading faster-whisper with compute_type='%s' failed (%s); falling back to 'default'",
                self.compute_type,
                exc,
            )
            self._model = WhisperModel(
                load_target,
                device=self.device,
                compute_type="default",
                download_root=str(model_path.parent),
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

        transcribe_lang = "en" if self.model_name.endswith(".en") else None
        segments, _ = self._model.transcribe(
            io.BytesIO(wav_bytes),
            word_timestamps=True,
            language=transcribe_lang,
            vad_filter=True,
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

        variants = get_title_search_variants(chapter_title, chapter_index)
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

            for i in range(len(clean_words) - k + 1):
                window_tokens = clean_words[i : i + k]
                window_str = " ".join(window_tokens)
                ratio = difflib.SequenceMatcher(None, target_phrase, window_str).ratio()

                # Penalize distance from estimated time slightly so closer matches win ties
                abs_time = slice_start_s + all_words[i].start
                dist_penalty = min(0.15, abs(abs_time - estimated_time_s) / (win_s * 10))
                adjusted_score = ratio - dist_penalty

                if adjusted_score > best_score and ratio >= 0.70:
                    best_score = adjusted_score
                    best_match_idx = i
                    best_phrase = window_str

        # If strict sequence matcher didn't find high confidence, check exact substring in words
        if best_match_idx is None:
            for target_phrase in variants:
                target_tokens = target_phrase.split()
                if not target_tokens:
                    continue
                first_tok = target_tokens[0]
                for i, w_tok in enumerate(clean_words):
                    if w_tok == first_tok:
                        best_match_idx = i
                        best_score = 0.75
                        best_phrase = w_tok
                        break
                if best_match_idx is not None:
                    break

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
            chapter_index=chapter_index,
            chapter_title=chapter_title,
            original_time_s=estimated_time_s,
            aligned_time_s=round(aligned_time_s, 3),
            spoken_time_s=round(spoken_time_s, 3),
            matched_phrase=best_phrase,
            transcript_snippet=snippet,
            confidence=round(best_score, 2),
            snapped_to_silence=snapped,
            offset_s=round(offset_s, 3),
            success=True,
            row=target_row,
        )

    # Backward-compatible alias
    align_chapter = align
