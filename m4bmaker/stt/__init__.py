"""Smart Chapter Alignment via Local Speech-to-Text (faster-whisper)."""


def _patch_pyav() -> None:
    """Monkey-patch av.open if PyAV removed the metadata_errors parameter.

    faster-whisper <= 1.1.1 calls av.open(..., metadata_errors="ignore"). Newer PyAV
    versions (such as av >= 15 / 19.x) removed metadata_errors, causing a TypeError.
    """
    try:
        import av

        if hasattr(av, "open"):
            _orig_open = av.open

            def _safe_open(*args, **kwargs):
                try:
                    return _orig_open(*args, **kwargs)
                except TypeError as exc:
                    if "metadata_errors" in str(exc) and "metadata_errors" in kwargs:
                        kwargs.pop("metadata_errors", None)
                        return _orig_open(*args, **kwargs)
                    raise

            av.open = _safe_open
    except Exception:
        pass


_patch_pyav()
