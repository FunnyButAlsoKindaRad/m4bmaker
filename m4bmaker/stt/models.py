"""Model directory resolution and downloading for faster-whisper models."""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import ssl
import sys
import urllib.error
import urllib.request
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
}

DEFAULT_MODEL = "tiny.en"


def _create_ssl_context() -> ssl.SSLContext:
    """Create an SSL context prioritizing certifi if available, then system CA."""
    ctx = ssl.create_default_context()
    try:
        import certifi

        ca = certifi.where()
        if os.path.isfile(ca):
            ctx.load_verify_locations(ca)
    except Exception:
        pass
    return ctx


def _download_file_stream(
    url: str,
    dest_path: Path,
    chunk_size: int = 128 * 1024,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Download a file with HTTP streaming into a temporary file before atomic rename."""
    temp_path = dest_path.with_name(dest_path.name + ".download")
    headers = {"User-Agent": "m4Bookmaker/1.0 (https://github.com/FunnyButAlsoKindaRad/m4bmaker)"}
    req = urllib.request.Request(url, headers=headers)
    ctx = _create_ssl_context()

    try:
        resp = urllib.request.urlopen(req, context=ctx, timeout=30)
    except ssl.SSLCertVerificationError:
        _log.warning("SSL verification failed for %s, retrying with unverified context", url)
        unverified_ctx = ssl._create_unverified_context()
        resp = urllib.request.urlopen(req, context=unverified_ctx, timeout=30)

    try:
        total_size = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(temp_path, "wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    progress_callback(downloaded, total_size)
        resp.close()
        temp_path.replace(dest_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


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

    if getattr(sys, "frozen", False):
        try:
            portable_dir.mkdir(parents=True, exist_ok=True)
            return portable_dir
        except Exception:
            pass

    # Standard user app data
    default_dir = Path(user_data_dir("m4bmaker")) / "models"
    default_dir.mkdir(parents=True, exist_ok=True)
    return default_dir


def get_model_path(model_name: str) -> Path:
    """Return the expected directory path for a model."""
    # If model_name is already an absolute directory path
    p = Path(model_name)
    if p.is_dir():
        return p

    clean_name = model_name.split("/")[-1].replace("faster-whisper-", "")
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
    else:
        exe_dir = Path(__file__).resolve().parent.parent.parent

    portable_path = exe_dir / "models" / clean_name
    if portable_path.is_dir():
        return portable_path

    # Check portable path with full last segment
    full_name = model_name.split("/")[-1]
    if (exe_dir / "models" / full_name).is_dir():
        return exe_dir / "models" / full_name

    models_dir = get_models_dir()
    if (models_dir / clean_name).is_dir():
        return models_dir / clean_name
    if (models_dir / full_name).is_dir():
        return models_dir / full_name

    return models_dir / clean_name


def validate_local_model(folder: Path) -> tuple[bool, str]:
    """Check if a local directory contains a valid CTranslate2 Faster-Whisper model."""
    if not folder.is_dir():
        return (False, f"Folder does not exist: {folder}")
    has_bin = (folder / "model.bin").is_file() and (folder / "model.bin").stat().st_size > 1000
    has_config = (folder / "config.json").is_file() and (folder / "config.json").stat().st_size > 0
    if has_bin and has_config:
        return (True, "Valid CTranslate2 model")

    # Check if this is a PyTorch/Safetensors repo
    has_safetensors = any(folder.glob("*.safetensors")) or (folder / "pytorch_model.bin").is_file()
    if has_safetensors:
        return (
            False,
            f"The directory '{folder.name}' contains PyTorch/Safetensors weights, not CTranslate2 format.\n\n"
            "Faster-Whisper requires CTranslate2 format containing 'model.bin' and 'config.json'.\n\n"
            "You can convert this model using:\n"
            f"ct2-transformers-converter --model \"{folder}\" --output_dir \"{folder.parent / (folder.name + '-ct2')}\" --quantization int8"
        )
    return (False, f"Directory '{folder.name}' is missing 'model.bin' and/or 'config.json'.")


def validate_hf_repo_ct2(repo_id: str) -> tuple[bool, str]:
    """Validate via Hugging Face API whether a repository contains a CTranslate2 model.bin."""
    clean_repo = repo_id.strip().rstrip("/")
    if not clean_repo or "/" not in clean_repo:
        return (
            False,
            f"Invalid Hugging Face repo ID '{repo_id}'. Expected format: 'username/model-name' (e.g. 'Systran/faster-whisper-small').",
        )

    api_url = f"https://huggingface.co/api/models/{clean_repo}"
    headers = {"User-Agent": "m4Bookmaker/1.0 (https://github.com/FunnyButAlsoKindaRad/m4bmaker)"}
    req = urllib.request.Request(api_url, headers=headers)
    ctx = _create_ssl_context()

    try:
        try:
            resp = urllib.request.urlopen(req, context=ctx, timeout=10)
        except ssl.SSLCertVerificationError:
            unverified_ctx = ssl._create_unverified_context()
            resp = urllib.request.urlopen(req, context=unverified_ctx, timeout=10)

        raw_data = resp.read().decode("utf-8")
        resp.close()
        data = json.loads(raw_data)
        siblings = data.get("siblings", [])
        filenames = {s.get("rfilename", "") for s in siblings if isinstance(s, dict)}

        if "model.bin" in filenames:
            return (True, "Valid CTranslate2 model repository")

        # Check if PyTorch/Safetensors
        is_safetensors = any(fn.endswith(".safetensors") for fn in filenames) or "pytorch_model.bin" in filenames
        if is_safetensors:
            suggested = f"Systran/faster-whisper-{clean_repo.split('/')[-1].replace('whisper-', '')}"
            return (
                False,
                f"Hugging Face repository '{clean_repo}' is in PyTorch/Safetensors format.\n\n"
                "Faster-Whisper requires CTranslate2 format containing 'model.bin'.\n\n"
                f"Consider checking for a pre-converted CTranslate2 version (such as '{suggested}' or '{clean_repo}-ct2') "
                "or convert it locally using:\n"
                f"ct2-transformers-converter --model {clean_repo} --output_dir models/{clean_repo.split('/')[-1]}-ct2 --quantization int8",
            )

        return (False, f"Repository '{clean_repo}' does not contain 'model.bin'. It does not appear to be a CTranslate2 model.")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return (False, f"Hugging Face repository '{clean_repo}' was not found (HTTP 404). Please verify the username and repository name.")
        return (False, f"Could not verify Hugging Face repository '{clean_repo}': HTTP {exc.code} {exc.reason}")
    except Exception as exc:
        _log.warning("Could not query Hugging Face API for %s: %s (will allow attempt)", clean_repo, exc)
        return (True, f"Could not verify online ({exc})")


def scan_local_models() -> dict[str, dict]:
    """Scan models directories for all valid CTranslate2 models."""
    discovered: dict[str, dict] = {}
    dirs_to_check = []

    # Portable models dir
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
    else:
        exe_dir = Path(__file__).resolve().parent.parent.parent
    portable_models = exe_dir / "models"
    if portable_models.is_dir():
        dirs_to_check.append(portable_models)

    # User data models dir
    user_models = get_models_dir()
    if user_models.is_dir() and user_models not in dirs_to_check:
        dirs_to_check.append(user_models)

    for base_dir in dirs_to_check:
        try:
            for sub in sorted(base_dir.iterdir()):
                if sub.is_dir():
                    valid, _ = validate_local_model(sub)
                    if valid:
                        name = sub.name
                        sz = sum(f.stat().st_size for f in sub.glob("**/*") if f.is_file())
                        sz_mb = round(sz / (1024 * 1024), 1)
                        discovered[name] = {
                            "path": sub,
                            "approx_size_mb": sz_mb,
                            "description": f"Local Model ({sz_mb} MB)",
                        }
        except Exception as exc:
            _log.warning("Error scanning models directory %s: %s", base_dir, exc)

    return discovered


def sanitize_model_config(folder: Path) -> None:
    """Sanitize config.json in model directory to prevent CTranslate2 crashes.

    Distilled models (such as kotoba-whisper or distil-whisper) frequently retain
    large-v3's alignment_heads referencing decoder layers up to 25. Because distilled
    models only have 2 decoder layers, CTranslate2 segfaults when word_timestamps=True.
    This safely resets alignment_heads to valid layers.
    """
    cfg_file = folder / "config.json"
    if not cfg_file.is_file():
        return
    try:
        with open(cfg_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        heads = cfg.get("alignment_heads")
        if heads and isinstance(heads, list):
            has_high_layer = any(isinstance(h, list) and len(h) >= 1 and h[0] >= 6 for h in heads)
            bin_file = folder / "model.bin"
            is_distilled = any(k in str(folder).lower() for k in ["distil", "kotoba"])
            is_small = bin_file.is_file() and bin_file.stat().st_size < 1_800_000_000
            if (is_distilled or is_small) and has_high_layer:
                _log.warning("Sanitizing corrupted alignment_heads in %s to prevent CTranslate2 crash", cfg_file)
                cfg["alignment_heads"] = [[0, 0], [1, 0]]
                with open(cfg_file, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2)
    except Exception as exc:
        _log.warning("Could not sanitize config.json in %s: %s", folder, exc)


def is_model_downloaded(model_name: str) -> bool:
    """Check if model files exist on disk."""
    path = get_model_path(model_name)
    if not path.is_dir():
        return False
    # Check for core CTranslate2 weights and vocabulary
    has_bin = (path / "model.bin").is_file() and (path / "model.bin").stat().st_size > 1000
    has_vocab = (
        ((path / "vocabulary.txt").is_file() and (path / "vocabulary.txt").stat().st_size > 0)
        or ((path / "vocabulary.json").is_file() and (path / "vocabulary.json").stat().st_size > 0)
    )
    has_config = (path / "config.json").is_file() and (path / "config.json").stat().st_size > 0
    return has_bin and has_vocab and has_config


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
    """Download the specified faster-whisper model from Hugging Face Hub.

    Uses direct HTTP streaming first (no Hugging Face token or API key required),
    falling back to snapshot_download if needed.
    """
    target_dir = get_model_path(model_name)
    target_dir.mkdir(parents=True, exist_ok=True)

    info = SUPPORTED_MODELS.get(model_name, {})
    repo_id = info.get("repo_id", model_name if "/" in model_name else f"Systran/faster-whisper-{model_name}")

    if progress_fn:
        progress_fn(0.02, f"Validating Hugging Face repository ({repo_id})...")

    # Validate CT2 format before attempting download
    is_valid, val_msg = validate_hf_repo_ct2(repo_id)
    if not is_valid:
        raise ValueError(val_msg)

    if progress_fn:
        progress_fn(0.05, f"Connecting to Hugging Face ({repo_id})...")

    # Strategy 1: Direct HTTP streaming download
    files_to_download = [
        "config.json",
        "preprocessor_config.json",
        "tokenizer.json",
        "vocabulary.txt",
        "model.bin",
    ]
    optional_files = {"preprocessor_config.json"}
    approx_mb = info.get("approx_size_mb", 150)
    approx_bytes = approx_mb * 1024 * 1024

    direct_failed = False
    try:
        total_downloaded = 0
        for fname in files_to_download:
            dest_file = target_dir / fname
            if dest_file.is_file() and dest_file.stat().st_size > 0:
                total_downloaded += dest_file.stat().st_size
                continue

            url = f"https://huggingface.co/{repo_id}/resolve/main/{fname}"

            def on_chunk(file_bytes: int, file_total: int, _fn: str = fname) -> None:
                overall = min(0.98, max(0.05, (total_downloaded + file_bytes) / approx_bytes))
                curr_mb = (total_downloaded + file_bytes) / (1024 * 1024)
                if progress_fn:
                    progress_fn(overall, f"Downloading {_fn} ({curr_mb:.1f} MB / ~{approx_mb} MB)...")

            try:
                _download_file_stream(url, dest_file, progress_callback=on_chunk)
                total_downloaded += dest_file.stat().st_size
            except urllib.error.HTTPError as http_err:
                if fname == "vocabulary.txt" and http_err.code == 404:
                    alt_fname = "vocabulary.json"
                    alt_url = f"https://huggingface.co/{repo_id}/resolve/main/{alt_fname}"
                    alt_dest = target_dir / alt_fname
                    _download_file_stream(alt_url, alt_dest)
                    total_downloaded += alt_dest.stat().st_size
                elif fname in optional_files and http_err.code == 404:
                    _log.debug("Optional model file %s not found in %s, skipping", fname, repo_id)
                else:
                    raise

        sanitize_model_config(target_dir)

        if is_model_downloaded(model_name):
            # Save custom model to preferences if not preset
            if model_name not in SUPPORTED_MODELS:
                from m4bmaker.gui import prefs
                custom_models = list(prefs.get("stt_custom_models") or [])
                if model_name not in custom_models and target_dir.name not in custom_models:
                    custom_models.append(target_dir.name)
                    prefs.set("stt_custom_models", custom_models)

            if progress_fn:
                progress_fn(1.0, f"Model '{model_name}' ready.")
            return target_dir
    except Exception as exc:
        _log.warning(
            "Direct download for %s failed (%s), falling back to snapshot_download",
            repo_id,
            exc,
        )
        direct_failed = True

    # Strategy 2: huggingface_hub snapshot_download fallback
    if direct_failed or not is_model_downloaded(model_name):
        try:
            if progress_fn:
                progress_fn(0.2, f"Downloading {repo_id} via Hugging Face Hub...")
            from huggingface_hub import snapshot_download

            snapshot_download(
                repo_id=repo_id,
                local_dir=str(target_dir),
                local_dir_use_symlinks=False,
            )
        except Exception as exc:
            _log.error("All download methods failed for %s: %s", model_name, exc)
            raise RuntimeError(
                f"Could not download model '{model_name}': {exc}.\n\n"
                f"Note: Faster-Whisper models require CTranslate2 format.\n"
                f"You can also download or place files manually into:\n{target_dir}"
            ) from exc

    if model_name not in SUPPORTED_MODELS:
        from m4bmaker.gui import prefs
        custom_models = list(prefs.get("stt_custom_models") or [])
        if model_name not in custom_models and target_dir.name not in custom_models:
            custom_models.append(target_dir.name)
            prefs.set("stt_custom_models", custom_models)

    if progress_fn:
        progress_fn(1.0, f"Model '{model_name}' ready.")
    return target_dir
