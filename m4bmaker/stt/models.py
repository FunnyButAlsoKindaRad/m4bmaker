"""Model directory resolution and downloading for faster-whisper models."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Callable, Optional

from platformdirs import user_data_dir

_log = logging.getLogger(__name__)

SUPPORTED_MODELS = {
    "tiny.en": {
        "repo_id": "Systran/faster-whisper-tiny.en",
        "approx_size_mb": 75,
        "description": "Fast & Lightweight (~75 MB) - Recommended",
    },
    "base.en": {
        "repo_id": "Systran/faster-whisper-base.en",
        "approx_size_mb": 145,
        "description": "High Accuracy (~145 MB) - Great for accents & rare titles",
    },
    "tiny": {
        "repo_id": "Systran/faster-whisper-tiny",
        "approx_size_mb": 75,
        "description": "Multilingual Fast (~75 MB)",
    },
    "base": {
        "repo_id": "Systran/faster-whisper-base",
        "approx_size_mb": 145,
        "description": "Multilingual High Accuracy (~145 MB)",
    },
}

DEFAULT_MODEL = "tiny.en"


def get_models_dir() -> Path:
    """Return the directory where STT models are stored.

    Checks:
    1. Custom path set in user preferences.
    2. Portable './models' directory alongside executable or script.
    3. User data directory (%LOCALAPPDATA%/m4bmaker/models or %APPDATA%/m4bmaker/models).
    """
    from m4bmaker.gui import prefs

    custom = prefs.get("stt_models_dir")
    if custom:
        p = Path(str(custom))
        try:
            p.mkdir(parents=True, exist_ok=True)
            return p
        except Exception as exc:
            _log.warning("Could not use custom models dir '%s': %s", p, exc)

    # Check portable folder next to exe/app root
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
    else:
        # Repository root (parent of m4bmaker package)
        exe_dir = Path(__file__).resolve().parent.parent.parent

    portable_dir = exe_dir / "models"
    if portable_dir.is_dir():
        return portable_dir

    # Standard user app data
    default_dir = Path(user_data_dir("m4bmaker")) / "models"
    default_dir.mkdir(parents=True, exist_ok=True)
    return default_dir


def get_model_path(model_name: str) -> Path:
    """Return the expected directory path for a model."""
    root = get_models_dir()
    # Normalize repo name or friendly name
    clean_name = model_name.split("/")[-1].replace("faster-whisper-", "")
    return root / clean_name


def is_model_downloaded(model_name: str) -> bool:
    """Check if model files (specifically model.bin) exist on disk."""
    path = get_model_path(model_name)
    if not path.is_dir():
        return False
    # Check for core CTranslate2 weights
    has_bin = (path / "model.bin").is_file()
    has_vocab = (path / "vocabulary.txt").is_file() or (path / "vocabulary.json").is_file()
    return has_bin and has_vocab


def get_installed_model_size_mb(model_name: str) -> float:
    """Calculate the total size in megabytes of an installed model."""
    path = get_model_path(model_name)
    if not path.is_dir():
        return 0.0
    total_bytes = sum(f.stat().st_size for f in path.glob("**/*") if f.is_file())
    return round(total_bytes / (1024 * 1024), 1)


def delete_model(model_name: str) -> bool:
    """Remove a downloaded model from disk."""
    path = get_model_path(model_name)
    if path.is_dir():
        try:
            shutil.rmtree(path)
            return True
        except Exception as exc:
            _log.error("Failed to delete model '%s': %s", model_name, exc)
            return False
    return False


def download_model(
    model_name: str = DEFAULT_MODEL,
    progress_fn: Optional[Callable[[float, str], None]] = None,
) -> Path:
    """Download the specified faster-whisper model from Hugging Face Hub."""
    from huggingface_hub import snapshot_download

    target_dir = get_model_path(model_name)
    target_dir.mkdir(parents=True, exist_ok=True)

    info = SUPPORTED_MODELS.get(model_name, {})
    repo_id = info.get("repo_id", f"Systran/faster-whisper-{model_name}")

    if progress_fn:
        progress_fn(0.1, f"Downloading {model_name} from Hugging Face...")

    # snapshot_download automatically checks cache / resumes downloads
    snapshot_download(
        repo_id=repo_id,
        local_dir=str(target_dir),
        local_dir_use_symlinks=False,
    )

    if progress_fn:
        progress_fn(1.0, f"Model {model_name} ready.")

    return target_dir
