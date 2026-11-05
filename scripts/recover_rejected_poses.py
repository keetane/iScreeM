#!/usr/bin/env python
"""Re-process docking poses a finished job rejected, without docking again.

Before salts were stripped from the ligand input, every multi-component entry
in a catalogue library failed its heavy-atom comparison: the engine docks the
largest fragment and drops the counter-ion, so the pose never matched the
template. The same comparison also rejected amidines and guanidines, whose
polar hydrogens survive RDKit's RemoveHs.

Those runs still hold everything needed on disk -- docked.raw.sdf and the
engine log -- so this re-runs the normalization and folds the recovered poses
back into scores.csv instead of spending GPU time on a second docking run.

Run it against a finished job directory:

    python scripts/recover_rejected_poses.py <job-directory> [--apply]

Without --apply it only reports what it would change. The job must not be
running: scores.csv is written once, when the job ends.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.ligand_normalization import normalize_docked_sdf  # noqa: E402
from app.services.score_parser import (  # noqa: E402
    combine_scores,
    parse_docking_scores,
    scores_from_sdf,
)

FIXED_COLUMNS = ["rank", "ligand_code", "ligand_id", "ligand_name", "smiles", "pose"]
RECOVERABLE = "does not match the prepared ligand heavy-atom graph"


def rejected_tasks(job: Path):
    for error_file in sorted(job.glob("*/error.json")):
        try:
            message = json.loads(error_file.read_text()).get("error", "")
        except (OSError, ValueError):
            continue
        if RECOVERABLE not in message:
            continue
        task = error_file.parent
        raw = task / "docked.raw.sdf"
        if raw.is_file() and raw.stat().st_size:
            yield task, raw


def recover(task: Path, raw: Path, scratch: Path | None) -> list[dict]:
    """Rebuild docked.sdf and the score rows for one rejected ligand.

    A dry run writes into `scratch` so the job directory stays untouched.
    """
    template = task / "ligand.prepared.sdf"
    target = (scratch / f"{task.name}.sdf") if scratch else (task / "docked.sdf")
    report = normalize_docked_sdf(raw, template, target)

    log = task / "docking.stdout.log"
    scores = combine_scores(
        scores_from_sdf(raw),
        parse_docking_scores(log.read_text()) if log.is_file() else [],
    )
    if len(scores) != report["pose_count"]:
        raise ValueError(
            f"{len(scores)} score rows for {report['pose_count']} poses"
        )
    name = task.name
    for index, record in enumerate(scores, start=1):
        record.update(
            {
                "ligand_code": name,
                "ligand_id": "",
                "ligand_name": _ligand_name(task, name),
                "smiles": report["canonical_smiles"],
                "pose": index,
            }
        )
    return scores


def _ligand_name(task: Path, fallback: str) -> str:
    uploaded = task / "ligand.uploaded.sdf"
    if uploaded.is_file():
        first = uploaded.read_text(errors="replace").splitlines()
        if first and first[0].strip():
            return first[0].strip()
    return fallback


def merge_scores(csv_path: Path, extra: list[dict]) -> tuple[int, int]:
    """Append the recovered rows and re-rank the file by affinity."""
    existing: list[dict] = []
    fieldnames: list[str] = []
    if csv_path.is_file():
        with csv_path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            existing = [dict(row) for row in reader]

    score_columns = [name for name in fieldnames if name not in FIXED_COLUMNS]
    for row in extra:
        for key in row:
            if key not in FIXED_COLUMNS and key not in score_columns:
                score_columns.append(key)
    if "affinity" in score_columns:
        score_columns.remove("affinity")
        score_columns.insert(0, "affinity")

    rows = existing + [{k: v for k, v in row.items()} for row in extra]

    def affinity(row):
        try:
            return (0, float(row.get("affinity")))
        except (TypeError, ValueError):
            return (1, 0.0)

    rows.sort(key=affinity)
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank

    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=[*FIXED_COLUMNS, *score_columns], extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)
    return len(existing), len(rows)


def update_job_counts(database: Path, job_id: str, recovered: int) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE docking_jobs SET success_count = success_count + ?, "
            "failed_count = MAX(0, failed_count - ?) WHERE id = ?",
            (recovered, recovered, job_id),
        )
        connection.commit()
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path, help="docking job directory")
    parser.add_argument("--apply", action="store_true", help="write the changes")
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / "data" / "molecular_docking.db",
        help="database holding the job row",
    )
    arguments = parser.parse_args()
    job = arguments.job.resolve()
    if not job.is_dir():
        parser.error(f"not a directory: {job}")

    csv_path = job / "scores.csv"
    if not csv_path.is_file():
        parser.error(
            f"{csv_path} is missing. scores.csv is written when the job ends, "
            "so let the job finish before recovering."
        )

    recovered_rows: list[dict] = []
    recovered_tasks = 0
    failures: list[str] = []
    scratch = None if arguments.apply else Path(tempfile.mkdtemp(prefix="iscreem-recover-"))
    for task, raw in rejected_tasks(job):
        try:
            scores = recover(task, raw, scratch)
        except Exception as exc:  # tool boundary: report and keep going
            failures.append(f"{task.name}: {exc}")
            continue
        recovered_rows.extend(scores)
        recovered_tasks += 1
        if arguments.apply:
            (task / "error.json").unlink(missing_ok=True)
            (task / "docked.sdf.tmp").unlink(missing_ok=True)

    print(f"ligands recovered : {recovered_tasks}")
    print(f"pose rows added   : {len(recovered_rows)}")
    print(f"still failing     : {len(failures)}")
    for line in failures[:10]:
        print(f"  {line}")

    if scratch is not None:
        shutil.rmtree(scratch, ignore_errors=True)
        print("\ndry run; the job directory was not touched. Re-run with --apply.")
        return 0

    before, after = merge_scores(csv_path, recovered_rows)
    print(f"scores.csv        : {before} -> {after} rows")
    update_job_counts(arguments.database, job.name, recovered_tasks)
    print("job counts updated; the result index rebuilds on the next request.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
