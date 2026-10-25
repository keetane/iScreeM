from __future__ import annotations

import re


def _property_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "Score"


def append_sdf_properties(record: str, properties: dict[str, object]) -> str:
    """Append metadata fields to one mol block/SDF record."""
    clean = record.rstrip()
    blocks = []
    for key, value in properties.items():
        if value is None or value == "":
            continue
        blocks.append(f">  <{_property_name(key)}>\n{value}\n")
    return clean + "\n" + "\n".join(blocks) + "\n$$$$\n"
