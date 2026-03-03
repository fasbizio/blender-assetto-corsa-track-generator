"""Utility for tracking manual modifications to .blend files.

Writes a .blend.meta JSON sidecar after each generation with the SHA256 hash.
Before regenerating, compares the current hash to detect manual edits.
"""

import hashlib
import json
import os
import shutil
from datetime import datetime

_CHUNK_SIZE = 1024 * 1024  # 1 MB


def compute_sha256(path):
    """Return hex SHA256 of file at *path*, reading in 1 MB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def write_meta(blend_path):
    """Write ``<blend_path>.meta`` with the current SHA256 of *blend_path*."""
    sha = compute_sha256(blend_path)
    meta_path = blend_path + ".meta"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({"sha256": sha}, f)
    return meta_path


def is_blend_modified(blend_path):
    """Check whether *blend_path* was modified after the last generation.

    Returns:
        True  — .blend exists and differs from the recorded hash.
        False — .blend exists and matches (or meta missing: baseline created).
        None  — .blend does not exist.
    """
    if not os.path.isfile(blend_path):
        return None
    meta_path = blend_path + ".meta"
    if not os.path.isfile(meta_path):
        write_meta(blend_path)  # first time: create baseline
        return False
    try:
        with open(meta_path, encoding="utf-8") as f:
            saved = json.load(f).get("sha256", "")
    except (json.JSONDecodeError, OSError):
        return True
    return compute_sha256(blend_path) != saved


def backup_blend(blend_path):
    """Copy *blend_path* to ``<name>_YYYYMMDD_HHMMSS.blend.bak``.

    Returns the backup path.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base, ext = os.path.splitext(blend_path)
    bak_path = f"{base}_{ts}{ext}.bak"
    shutil.copy2(blend_path, bak_path)
    return bak_path
