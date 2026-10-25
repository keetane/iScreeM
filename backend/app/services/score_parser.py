"""Parse score columns reported by Vina-family docking engines."""

from __future__ import annotations

import re


_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def _score_name(value: str, index: int) -> str:
    text = re.sub(r"\s+", " ", value.strip().lower())
    if not text or text.startswith("("):
        return f"score_{index}"
    if "rmsd" in text and ("l.b" in text or "lower" in text):
        return "rmsd_lb"
    if "rmsd" in text and ("u.b" in text or "upper" in text):
        return "rmsd_ub"
    if "cnn" in text and "affinity" in text:
        return "cnn_affinity"
    if "cnn" in text and "score" in text:
        return "cnn_score"
    if "affinity" in text:
        return "affinity"
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or f"score_{index}"


def _table_scores(output: str) -> list[dict[str, float]]:
    lines = output.splitlines()
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if "|" in line
            and any(cell.strip().lower() in {"mode", "model"} for cell in line.split("|"))
        ),
        None,
    )
    if header_index is None:
        return []

    first = [cell.strip() for cell in lines[header_index].split("|")]
    subheader: list[str] = []
    for line in lines[header_index + 1 : header_index + 4]:
        if "|" not in line:
            continue
        if re.search(r"[-+]{3,}", line):
            break
        cells = [cell.strip() for cell in line.split("|")]
        if any("rmsd" in cell.lower() or "cnn" in cell.lower() for cell in cells):
            subheader = cells
            break

    # A Vina table uses a two-line header: the second line supplies the two
    # RMSD columns while the first line supplies mode/affinity.
    labels: list[str] = []
    width = max(len(first), len(subheader))
    for index in range(width):
        candidate = subheader[index] if index < len(subheader) and subheader[index] else ""
        if not candidate and index < len(first):
            candidate = first[index]
        labels.append(candidate)
    if any("rmsd l.b" in cell.lower() for cell in subheader):
        labels = ["mode", "affinity", "rmsd l.b.", "rmsd u.b."]

    parsed: list[dict[str, float]] = []
    for line in lines[header_index + 1 :]:
        if re.search(r"[-+]{3,}", line):
            continue
        match = re.match(r"^\s*\d+\s+", line)
        if not match:
            if parsed and line.strip() and "|" not in line:
                break
            continue
        values = [float(value) for value in re.findall(_NUMBER, line[match.end() :])]
        if not values:
            continue
        row: dict[str, float] = {}
        for offset, value in enumerate(values):
            label_index = offset + 1
            label = _score_name(labels[label_index] if label_index < len(labels) else "", offset + 1)
            if label == "mode":
                continue
            if label in row:
                label = f"{label}_{offset + 1}"
            row[label] = value
        if row:
            parsed.append(row)
    return parsed


def parse_docking_scores(output: str) -> list[dict[str, float]]:
    """Return every numeric score column reported for each docking pose."""
    parsed = _table_scores(output)
    if parsed:
        return parsed
    affinity = re.findall(r"(?:Affinity|REMARK VINA RESULT:)\s*[: ]\s*(%s)" % _NUMBER, output)
    if not affinity:
        affinity = re.findall(r"^\s*\d+\s+(%s)\s+[-\d.]" % _NUMBER, output, flags=re.MULTILINE)
    return [{"affinity": float(value)} for value in affinity]
