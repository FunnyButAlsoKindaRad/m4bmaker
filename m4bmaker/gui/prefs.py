"""Persistent user preferences for the m4Bookmaker GUI.

Preferences are stored as JSON in the platform-appropriate config directory:
  - macOS:   ~/Library/Application Support/m4bmaker/prefs.json
  - Linux:   ~/.config/m4bmaker/prefs.json
  - Windows: %APPDATA%\\m4bmaker\\prefs.json

Only light-weight scalar values are stored here. The file is created on first
write and silently ignored if it cannot be read or written.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from platformdirs import user_config_dir

_log = logging.getLogger(__name__)

_APP_NAME = "m4bmaker"
_PREFS_FILE = "prefs.json"

# Defaults ─ returned when the file is absent or a key is missing.
_DEFAULTS: dict[str, object] = {
    "dark_mode": False,
    "check_for_updates": True,
    "google_books_api_key": "",
    "stt_model": "tiny.en",
    "stt_standard_model": "tiny.en",
    "stt_custom_model_override": "",
    "stt_window_min": 3,
    "stt_search_window_s": 180.0,
    "stt_silence_snap": True,
    "stt_snap_silence": True,
    "stt_noise_db": -35,
    "stt_silence_thresh_db": -35.0,
    "stt_min_silence_s": 0.3,
    "stt_silence_min_dur_s": 0.3,
    "stt_auto_pre_chapter": True,
    "stt_pre_chapter_title": "Chapter 0",
    "stt_pre_chapter_threshold_s": 3.0,
    "stt_custom_models": [],
    "stt_heading_keywords": "chronicle, book, act, section, canto",
    "stt_prompt_biasing": True,
    "stt_language": "auto",
}


def reset_stt_defaults() -> None:
    """Reset all STT-related preferences to their factory defaults."""
    stt_keys = [k for k in _DEFAULTS if k.startswith("stt_") and k != "stt_custom_models"]
    p = load()
    for k in stt_keys:
        p[k] = _DEFAULTS[k]
    save(p)


def _prefs_path() -> Path:
    return Path(user_config_dir(_APP_NAME)) / _PREFS_FILE


def load() -> dict[str, object]:
    """Return the stored preferences dict, falling back to defaults on any error."""
    path = _prefs_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("prefs file root is not a JSON object")
        return {**_DEFAULTS, **data}
    except FileNotFoundError:
        return dict(_DEFAULTS)
    except Exception as exc:  # corrupt JSON, permission error, etc.
        _log.warning("Could not read preferences from %s: %s", path, exc)
        return dict(_DEFAULTS)


def save(prefs: dict[str, object]) -> None:
    """Write *prefs* to disk, silently ignoring any I/O errors."""
    path = _prefs_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    except Exception as exc:
        _log.warning("Could not write preferences to %s: %s", path, exc)


def get(key: str) -> object:
    """Return a single preference value by key."""
    return load().get(key, _DEFAULTS.get(key))


def set(key: str, value: object) -> None:
    """Update a single preference key and persist immediately."""
    prefs = load()
    prefs[key] = value
    save(prefs)
