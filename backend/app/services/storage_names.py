from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TOKYO = ZoneInfo("Asia/Tokyo")


def _tokyo_moment(current_time: datetime | None) -> datetime:
    moment = current_time or datetime.now(TOKYO)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=TOKYO)
    return moment.astimezone(TOKYO)


def _safe_slug(label: str, *, fallback: str = "data") -> str:
    normalized = unicodedata.normalize("NFKC", label).strip()
    slug = re.sub(r"[^\w.-]+", "_", normalized, flags=re.UNICODE).strip("_.-")
    return slug or fallback


def readable_name(label: str, *, current_time: datetime | None = None) -> str:
    """Return a filesystem-safe ``yyyymmdd_label`` name."""
    return f"{_tokyo_moment(current_time):%Y%m%d}_{_safe_slug(label)}"


def readable_docking_name(
    label: str,
    *,
    comment: str = "",
    current_time: datetime | None = None,
) -> str:
    """Return a safe ``yyyymmddhhmm_label[_comment]`` docking name."""
    prefix = f"{_tokyo_moment(current_time):%Y%m%d%H%M}_{_safe_slug(label)}"
    comment_slug = _safe_slug(comment, fallback="") if comment.strip() else ""
    return f"{prefix}_{comment_slug}" if comment_slug else prefix


def unique_docking_job_id(
    db,
    model,
    label: str,
    *,
    comment: str = "",
    current_time: datetime | None = None,
) -> str:
    """Create a unique docking id while keeping an optional comment last."""
    base = readable_docking_name(label, current_time=current_time)
    comment_slug = _safe_slug(comment, fallback="") if comment.strip() else ""
    candidate = f"{base}_{comment_slug}" if comment_slug else base
    suffix = 2
    while db.get(model, candidate) is not None:
        candidate = (
            f"{base}_{suffix:02d}_{comment_slug}"
            if comment_slug
            else f"{base}_{suffix:02d}"
        )
        suffix += 1
    return candidate


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
