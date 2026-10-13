"""Per-creator storage: profile, credit wallet and ad library.

Pilot-grade: one pickle file per creator on local disk. On Streamlit Community Cloud the disk survives
reruns and page reloads but is reset when the app restarts or redeploys; production would use a database
and object storage (e.g. Postgres + S3/GCS).
"""
from __future__ import annotations

import os
import pickle
import re
import tempfile
from pathlib import Path

DATA_DIR = Path(os.environ.get("RISUS_DATA_DIR") or Path(__file__).parent / "data") / "users"


def user_id(identity: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", identity.lower()).strip("-")[:60] or "creator"


def _path(uid: str) -> Path:
    return DATA_DIR / f"{user_id(uid)}.pkl"


def exists(uid: str) -> bool:
    return _path(uid).exists()


def load(uid: str) -> dict | None:
    try:
        with open(_path(uid), "rb") as f:
            return pickle.load(f)
    except (FileNotFoundError, EOFError, pickle.UnpicklingError):
        return None


def save(uid: str, state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
    with os.fdopen(fd, "wb") as f:
        pickle.dump(state, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, _path(uid))  # atomic: a crash never leaves a half-written library
