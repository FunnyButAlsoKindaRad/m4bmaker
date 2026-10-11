"""FFmpeg silencedetect-based pause snapping."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Optional, NamedTuple

from m4bmaker.utils import find_ffmpeg, subprocess_flags

_log = logging.getLogger(__name__)

# Pattern for: [silencedetect @ 0000...] silence_start: 12.345
_RE_SILENCE_START = re.compile(r"silence_start:\s*([0-9.]+)")
# Pattern for: [silencedetect @ 0000...] silence_end: 14.567 | silence_duration: 2.222
_RE_SILENCE_END = re.compile(r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)")


class SilenceGap(NamedTuple):
    start_s: float
    end_s: float
    duration_s: float


def detect_silence_gaps(
    file_path: Path,
    start_time_s: float,
    end_time_s: float,
    noise_db: float = -35.0,
    min_duration_s: float = 0.3,
    ffmpeg_bin: Optional[str] = None,
) -> list[SilenceGap]:
    """Detect silence periods within [start_time_s, end_time_s] of *file_path*.

    Returns a list of SilenceGap objects with absolute timestamps.
    """
    if ffmpeg_bin is None:
        ffmpeg_bin = find_ffmpeg()

    clip_start = max(0.0, start_time_s)
    clip_dur = max(0.1, end_time_s - clip_start)

    cmd = [
        ffmpeg_bin,
        "-v", "info",
        "-ss", f"{clip_start:.3f}",
        "-t", f"{clip_dur:.3f}",
        "-i", str(file_path),
        "-af", f"silencedetect=noise={noise_db:.1f}dB:d={min_duration_s:.2f}",
        "-f", "null",
        "-",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            **subprocess_flags(),
        )
    except Exception as exc:
        _log.warning("FFmpeg silencedetect failed: %s", exc)
        return []

    gaps: list[SilenceGap] = []
    current_start: Optional[float] = None

    for line in proc.stderr.splitlines():
        m_start = _RE_SILENCE_START.search(line)
        if m_start:
            current_start = float(m_start.group(1))
            continue

        m_end = _RE_SILENCE_END.search(line)
        if m_end and current_start is not None:
            rel_end = float(m_end.group(1))
            dur = float(m_end.group(2))
            # Convert relative time in clip to absolute time in file
            abs_start = clip_start + current_start
            abs_end = clip_start + rel_end
            gaps.append(SilenceGap(start_s=abs_start, end_s=abs_end, duration_s=dur))
            current_start = None

    return gaps


def snap_to_preceding_silence(
    file_path: Path,
    spoken_time_s: float,
    search_before_s: float = 8.0,
    search_after_s: float = 1.0,
    noise_db: float = -35.0,
    min_duration_s: float = 0.3,
    ffmpeg_bin: Optional[str] = None,
) -> Optional[float]:
    """Find the silence gap directly preceding *spoken_time_s* and return its midpoint.

    If a silence pause is found between [spoken_time_s - search_before_s, spoken_time_s],
    returns the center timestamp of that pause (ideal chapter cut point).
    Otherwise returns None.
    """
    gaps = detect_silence_gaps(
        file_path=file_path,
        start_time_s=max(0.0, spoken_time_s - search_before_s),
        end_time_s=spoken_time_s + search_after_s,
        noise_db=noise_db,
        min_duration_s=min_duration_s,
        ffmpeg_bin=ffmpeg_bin,
    )

    if not gaps:
        return None

    # Filter to gaps that start or end before/at spoken_time_s
    candidate_gaps = [g for g in gaps if g.start_s <= spoken_time_s]
    if not candidate_gaps:
        # Check if the closest gap starts slightly before spoken_time
        candidate_gaps = gaps

    # Pick the gap closest to spoken_time_s
    best_gap = max(candidate_gaps, key=lambda g: g.end_s if g.end_s <= spoken_time_s else -abs(g.start_s - spoken_time_s))

    # Return midpoint of silence (cleanest audio break)
    midpoint = (best_gap.start_s + best_gap.end_s) / 2.0
    return midpoint
