from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


TOKYO = ZoneInfo("Asia/Tokyo")


def readable_name(label: str, *, current_time: datetime | None = None) -> str:
    """Return a filesystem-safe ``yyyymmdd_label`` name."""
    moment = current_time or datetime.now(TOKYO)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=TOKYO)
    else:
        moment = moment.astimezone(TOKYO)
    normalized = unicodedata.normalize("NFKC", label).strip()
    slug = re.sub(r"[^\w.-]+", "_", normalized, flags=re.UNICODE).strip("_.-")
    return f"{moment:%Y%m%d}_{slug or 'data'}"


def unique_database_id(db, model, label: str, *, current_time: datetime | None = None) -> str:
    base = readable_name(label, current_time=current_time)
    candidate = base
    suffix = 2
    while db.get(model, candidate) is not None:
        candidate = f"{base}_{suffix:02d}"
        suffix += 1
    return candidate


def unique_directory_name(parent: Path, label: str, *, current_time: datetime | None = None) -> str:
    base = readable_name(label, current_time=current_time)
    candidate = base
    suffix = 2
    while (parent / candidate).exists():
        candidate = f"{base}_{suffix:02d}"
        suffix += 1
    return candidate


def docking_job_root(project_root: Path, job_id: str) -> Path:
    """Use readable directories for new jobs and retain legacy UUID job paths."""
    docking_root = project_root / "docking"
    direct = docking_root / job_id
    legacy = docking_root / f"job_{job_id}"
    if legacy.exists() and not direct.exists():
        return legacy.resolve()
    return direct.resolve()
