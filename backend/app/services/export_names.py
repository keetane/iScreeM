from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo


def export_filename(
    receptors: list[str], extension: str, *, current_time: datetime | None = None
) -> str:
    """Build yyyymmdd_hhmm_receptor filenames in the app's Japan timezone."""
    timestamp = (current_time or datetime.now(ZoneInfo("Asia/Tokyo"))).strftime("%Y%m%d_%H%M")
    cleaned = []
    for receptor in receptors:
        slug = re.sub(r"[^\w.-]+", "_", receptor.strip(), flags=re.UNICODE).strip("_.-")
        if slug and slug not in cleaned:
            cleaned.append(slug)
    receptor_part = "-".join(cleaned) or "receptor"
    return f"{timestamp}_{receptor_part}.{extension.lstrip('.')}"
