"""Parse score columns reported by Vina-family docking engines."""

from __future__ import annotations

import re
from pathlib import Path

from .sdf import split_sdf_records


_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def _score_name(value: str, index: int) -> str:
    # Column units live on their own header line, so "affinity (kcal/mol)" must
    # still be named "affinity".
    text = re.sub(r"\([^)]*\)", " ", value)
    text = re.sub(r"\s+", " ", text.strip().lower())
    if not text:
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
        if re.search(r"[-+]{3,}", line) or re.match(r"^\s*\d", line):
            break
        subheader = [cell.strip() for cell in line.split("|")]
        break

    # Vina-family tables use a two-line header. gnina splits "CNN pose score"
    # and "CNN affinity" across both lines, so the halves must be joined or the
    # two CNN columns collapse into one name.
    labels: list[str] = []
    width = max(len(first), len(subheader))
    for index in range(width):
        top = first[index] if index < len(first) else ""
        bottom = subheader[index] if index < len(subheader) else ""
        labels.append(f"{top} {bottom}".strip())
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


# gnina reports a minimisation run as free-standing "<label>: <value>" lines
# instead of the pose table, and only the first of them is named "Affinity".
_MINIMIZATION_FIELDS = {
    "rmsd": "rmsd",
    "cnnscore": "cnn_score",
    "cnnaffinity": "cnn_affinity",
    "cnnvariance": "cnn_affinity_variance",
    "cnn_vs": "cnn_vs",
}
_AFFINITY_LINE = re.compile(r"^\s*Affinity:\s*(%s)(?:\s+(%s))?" % (_NUMBER, _NUMBER))
_LABELLED_VALUE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*:\s*(%s)\s*$" % _NUMBER)


def _minimization_scores(output: str) -> list[dict[str, float]]:
    poses: list[dict[str, float]] = []
    current: dict[str, float] | None = None
    for line in output.splitlines():
        affinity = _AFFINITY_LINE.match(line)
        if affinity:
            current = {"affinity": float(affinity.group(1))}
            if affinity.group(2) is not None:
                current["intramol"] = float(affinity.group(2))
            poses.append(current)
            continue
        if current is None:
            continue
        labelled = _LABELLED_VALUE.match(line)
        if labelled:
            name = _MINIMIZATION_FIELDS.get(labelled.group(1).lower())
            if name:
                current[name] = float(labelled.group(2))
        elif line.strip():
            # Anything else ends the block that belongs to this pose.
            current = None
    return poses


def parse_docking_scores(output: str) -> list[dict[str, float]]:
    """Return every numeric score column reported for each docking pose."""
    parsed = _table_scores(output) or _minimization_scores(output)
    if parsed:
        return parsed
    affinity = re.findall(r"REMARK VINA RESULT:\s*(%s)" % _NUMBER, output)
    if not affinity:
        affinity = re.findall(r"^\s*\d+\s+(%s)\s+[-\d.]" % _NUMBER, output, flags=re.MULTILINE)
    return [{"affinity": float(value)} for value in affinity]


_SDF_SCORE_NAMES = {
    "minimizedaffinity": "affinity",
    "minimizedrmsd": "rmsd",
    "cnnscore": "cnn_score",
    "cnnaffinity": "cnn_affinity",
    "cnnaffinity_variance": "cnn_affinity_variance",
    "cnn_vs": "cnn_vs",
}
_SDF_TAG = re.compile(r"^>\s+<([^>]+)>", re.MULTILINE)


def _sdf_score_name(tag: str) -> str:
    key = tag.strip().lower()
    if key in _SDF_SCORE_NAMES:
        return _SDF_SCORE_NAMES[key]
    return re.sub(r"[^a-z0-9]+", "_", key).strip("_")


def scores_from_sdf(path: Path) -> list[dict[str, float]]:
    """Return the per-pose scores the engine wrote into its output SDF.

    smina and gnina tag every pose with its own scores, which stays correct for
    both the pose table and the minimisation output, and carries values the log
    never prints (CNN_VS, score variance).
    """
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return []
    poses: list[dict[str, float]] = []
    for record in split_sdf_records(text):
        lines = record.splitlines()
        scores: dict[str, float] = {}
        for index, line in enumerate(lines):
            tag = _SDF_TAG.match(line)
            if not tag or index + 1 >= len(lines):
                continue
            name = _sdf_score_name(tag.group(1))
            # IDOCK_* tags are our own provenance notes, never engine scores.
            if name.startswith("idock"):
                continue
            try:
                scores[name] = float(lines[index + 1].strip())
            except ValueError:
                continue
        poses.append(scores)
    return poses if any(poses) else []


def combine_scores(
    primary: list[dict[str, float]], secondary: list[dict[str, float]]
) -> list[dict[str, float]]:
    """Fill gaps in `primary` from `secondary` without overriding it."""
    if not primary:
        return secondary
    if len(primary) != len(secondary):
        return primary
    return [{**extra, **preferred} for preferred, extra in zip(primary, secondary)]
