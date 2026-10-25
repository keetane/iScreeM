#!/usr/bin/env python3
"""Migrate a legacy project and its generated directories to readable IDs.

The migration mirrors the application's ``yyyymmdd_label`` naming policy,
updates every database foreign-key/JSON reference, and preserves the original
project tree plus database in ``data/backups`` before applying any change.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


TOKYO = ZoneInfo("Asia/Tokyo")
def parse_time(value: str | None, fallback: datetime | None = None) -> datetime:
    if value:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(TOKYO)
    if fallback is not None:
        return fallback.astimezone(TOKYO)
    return datetime.now(TOKYO)


def readable(label: str, moment: datetime) -> str:
    normalized = label.strip()
    slug = re.sub(r"[^\w.-]+", "_", normalized, flags=re.UNICODE).strip("_.-")
    return f"{moment.astimezone(TOKYO):%Y%m%d}_{slug or 'data'}"


def unique_id(label: str, moment: datetime, used: set[str]) -> str:
    base = readable(label, moment)
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix:02d}"
        suffix += 1
    used.add(candidate)
    return candidate


def rows(connection: sqlite3.Connection, query: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(query, parameters)]


def json_value(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def transform(value: Any, replacements: list[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        for old, new in replacements:
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [transform(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: transform(item, replacements) for key, item in value.items()}
    return value


def move(source: Path, destination: Path, moves: list[tuple[Path, Path]]) -> None:
    if not source.exists():
        return
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.rename(destination)
    moves.append((source, destination))


def build_mappings(connection: sqlite3.Connection, project: dict[str, Any]) -> dict[str, dict[str, str]]:
    project_time = parse_time(project["created_at"])
    project_id = project["id"]
    mappings: dict[str, dict[str, str]] = {
        "projects": {project_id: readable(project["name"], project_time)},
        "proteins": {},
        "ligands": {},
        "pockets": {},
        "grids": {},
        "jobs": {},
    }

    proteins = rows(connection, "SELECT * FROM proteins WHERE project_id=? ORDER BY created_at, rowid", (project_id,))
    protein_rows = {item["id"]: item for item in proteins}
    used: set[str] = set()
    for item in proteins:
        mappings["proteins"][item["id"]] = unique_id(item["name"], parse_time(item["created_at"], project_time), used)

    jobs = rows(connection, "SELECT * FROM docking_jobs WHERE project_id=? ORDER BY created_at, rowid", (project_id,))
    used = set()
    pocket_job_times: dict[str, list[datetime]] = defaultdict(list)
    for item in jobs:
        parameters = json_value(item["parameters"], {})
        protein = protein_rows.get(parameters.get("protein_id"), {})
        protein_name = protein.get("name", "receptor")
        if item["kind"] == "RECEPTOR_PREPARATION":
            label = f"{protein_name}_receptor_prep"
        elif item["kind"] == "POCKET_PREDICTION":
            label = f"{protein_name}_pocket_prediction"
            if protein:
                pocket_job_times[protein["id"]].append(parse_time(item["created_at"], project_time))
        elif item["kind"] == "LIGAND_PREPARATION":
            label = "ligand_preparation"
        else:
            label = item["name"]
        mappings["jobs"][item["id"]] = unique_id(label, parse_time(item["created_at"], project_time), used)

    used = set()
    for item in rows(connection, "SELECT * FROM ligands WHERE project_id=? ORDER BY ligand_code, rowid", (project_id,)):
        mappings["ligands"][item["id"]] = unique_id(
            f"{item['name']}_{item['ligand_code']}", project_time, used
        )

    used = set()
    for item in rows(connection, "SELECT rowid AS source_rowid, * FROM pockets WHERE project_id=? ORDER BY protein_id, rank, rowid", (project_id,)):
        protein = protein_rows[item["protein_id"]]
        prediction_times = pocket_job_times.get(item["protein_id"], [])
        moment = max(prediction_times) if prediction_times else parse_time(protein["created_at"], project_time)
        mappings["pockets"][item["id"]] = unique_id(
            f"{protein['name']}_pocket_{item['rank']}", moment, used
        )

    used = set()
    for item in rows(connection, "SELECT * FROM docking_grids WHERE project_id=? ORDER BY created_at, rowid", (project_id,)):
        mappings["grids"][item["id"]] = unique_id(item["name"], parse_time(item["created_at"], project_time), used)
    return mappings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/molecular_docking.db"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--project", required=True, help="Existing project name or ID")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    database = args.database.resolve()
    data_dir = args.data_dir.resolve()
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    project_row = connection.execute(
        "SELECT * FROM projects WHERE id=? OR name=? ORDER BY created_at DESC LIMIT 1",
        (args.project, args.project),
    ).fetchone()
    if project_row is None:
        raise SystemExit(f"Project not found: {args.project}")
    project = dict(project_row)
    mappings = build_mappings(connection, project)
    print(json.dumps(mappings, ensure_ascii=False, indent=2))
    if not args.apply:
        print("Dry run only; pass --apply to migrate.")
        return 0

    old_project_id = project["id"]
    new_project_id = mappings["projects"][old_project_id]
    old_project_root = data_dir / "projects" / old_project_id
    new_project_root = data_dir / "projects" / new_project_id
    if not old_project_root.is_dir():
        raise SystemExit(f"Project directory not found: {old_project_root}")
    if new_project_root.exists():
        raise SystemExit(f"Destination already exists: {new_project_root}")

    backup_root = data_dir / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_base = backup_root / f"{new_project_id}_before_readable_names"
    suffix = 2
    while backup_base.with_suffix(".zip").exists() or backup_base.with_suffix(".db").exists():
        backup_base = backup_root / f"{new_project_id}_before_readable_names_{suffix:02d}"
        suffix += 1
    shutil.make_archive(str(backup_base), "zip", root_dir=old_project_root.parent, base_dir=old_project_root.name)
    backup_connection = sqlite3.connect(backup_base.with_suffix(".db"))
    connection.backup(backup_connection)
    backup_connection.close()

    job_rows = rows(connection, "SELECT * FROM docking_jobs WHERE project_id=? ORDER BY created_at, rowid", (old_project_id,))
    protein_rows = rows(connection, "SELECT * FROM proteins WHERE project_id=? ORDER BY created_at, rowid", (old_project_id,))
    moves: list[tuple[Path, Path]] = []
    move(old_project_root, new_project_root, moves)

    replacements: list[tuple[str, str]] = [
        (str(old_project_root), str(new_project_root)),
        (f"data/projects/{old_project_id}", f"data/projects/{new_project_id}"),
    ]

    receptor_root = new_project_root / "receptor"
    for item in protein_rows:
        old_id = item["id"]
        new_id = mappings["proteins"][old_id]
        move(receptor_root / old_id, receptor_root / new_id, moves)
        replacements.extend(
            [
                (f"/receptor/{old_id}", f"/receptor/{new_id}"),
                (f"/pockets/{old_id}_pocket", f"/pockets/{new_id}_pocket"),
            ]
        )

    legacy_owner = next(
        (item for item in protein_rows if Path(item["original_path"]).parent.name == "receptor"),
        protein_rows[0] if protein_rows else None,
    )
    if legacy_owner:
        owner_root = receptor_root / mappings["proteins"][legacy_owner["id"]]
        owner_root.mkdir(parents=True, exist_ok=True)
        for source in sorted(receptor_root.iterdir()):
            if not source.is_file():
                continue
            destination = owner_root / source.name
            if destination.exists():
                destination = owner_root / f"legacy_{source.name}"
            move(source, destination, moves)
            if source.name == Path(legacy_owner["original_path"]).name:
                replacements.append((str(source), str(destination)))
                replacements.append(
                    (
                        f"data/projects/{new_project_id}/receptor/{source.name}",
                        f"data/projects/{new_project_id}/receptor/{mappings['proteins'][legacy_owner['id']]}/{destination.name}",
                    )
                )

    pocket_jobs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in job_rows:
        if item["kind"] != "POCKET_PREDICTION":
            continue
        parameters = json_value(item["parameters"], {})
        if parameters.get("protein_id") in mappings["proteins"]:
            pocket_jobs[parameters["protein_id"]].append(item)

    pockets_root = new_project_root / "pockets"
    for old_protein_id, related_jobs in pocket_jobs.items():
        new_protein_id = mappings["proteins"][old_protein_id]
        canonical = pockets_root / f"{new_protein_id}_pocket"
        candidates: list[tuple[dict[str, Any], Path]] = []
        for job in related_jobs:
            direct = pockets_root / job["id"]
            nested = pockets_root / old_protein_id / job["id"]
            source = direct if direct.exists() else nested
            if source.exists():
                candidates.append((job, source))
        completed = [pair for pair in candidates if pair[0]["status"] == "COMPLETED"]
        current = max(completed, key=lambda pair: pair[0]["created_at"]) if completed else None
        if current:
            move(current[1], canonical, moves)
            replacements.extend(
                [
                    (str(current[1]), str(canonical)),
                    (current[1].as_posix(), canonical.as_posix()),
                ]
            )
        for job, source in candidates:
            if current and job["id"] == current[0]["id"]:
                continue
            destination = canonical / "history" / mappings["jobs"][job["id"]]
            move(source, destination, moves)
            replacements.extend([(str(source), str(destination)), (source.as_posix(), destination.as_posix())])
        legacy_container = pockets_root / old_protein_id
        if legacy_container.is_dir() and not any(legacy_container.iterdir()):
            legacy_container.rmdir()

    docking_root = new_project_root / "docking"
    for item in job_rows:
        old_id = item["id"]
        new_id = mappings["jobs"][old_id]
        legacy = docking_root / f"job_{old_id}"
        direct = docking_root / old_id
        source = legacy if legacy.exists() else direct
        if source.exists():
            destination = docking_root / new_id
            move(source, destination, moves)
            replacements.extend([(str(source), str(destination)), (source.as_posix(), destination.as_posix())])

    all_ids: dict[str, str] = {}
    for group in mappings.values():
        all_ids.update(group)
    replacements.extend(sorted(all_ids.items(), key=lambda pair: len(pair[0]), reverse=True))
    replacements = sorted(dict(replacements).items(), key=lambda pair: len(pair[0]), reverse=True)

    # Rename any UUID directory nested inside generated tool output.
    for directory in sorted((path for path in new_project_root.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True):
        replacement = all_ids.get(directory.name)
        if replacement:
            destination = directory.with_name(replacement)
            move(directory, destination, moves)
        elif directory.name.startswith("job_") and directory.name[4:] in mappings["jobs"]:
            destination = directory.with_name(mappings["jobs"][directory.name[4:]])
            move(directory, destination, moves)

    for path in new_project_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".gz", ".zip", ".db"}:
            continue
        try:
            original = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        updated = transform(original, replacements)
        if updated != original:
            path.write_text(updated)

    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("BEGIN IMMEDIATE")
    try:
        for old, new in mappings["projects"].items():
            connection.execute("UPDATE projects SET id=? WHERE id=?", (new, old))
            for table in ("proteins", "ligands", "pockets", "docking_grids", "docking_jobs"):
                connection.execute(f"UPDATE {table} SET project_id=? WHERE project_id=?", (new, old))
        for old, new in mappings["proteins"].items():
            connection.execute("UPDATE proteins SET id=? WHERE id=?", (new, old))
            connection.execute("UPDATE pockets SET protein_id=? WHERE protein_id=?", (new, old))
        for old, new in mappings["pockets"].items():
            connection.execute("UPDATE pockets SET id=? WHERE id=?", (new, old))
            connection.execute("UPDATE docking_grids SET pocket_id=? WHERE pocket_id=?", (new, old))
        for old, new in mappings["ligands"].items():
            connection.execute("UPDATE ligands SET id=? WHERE id=?", (new, old))
        for old, new in mappings["grids"].items():
            connection.execute("UPDATE docking_grids SET id=? WHERE id=?", (new, old))
        for old, new in mappings["jobs"].items():
            connection.execute("UPDATE docking_jobs SET id=? WHERE id=?", (new, old))

        for item in rows(connection, "SELECT * FROM proteins WHERE project_id=?", (new_project_id,)):
            connection.execute(
                "UPDATE proteins SET original_path=?, prepared_path=?, prepared_receptor_path=?, stats=? WHERE id=?",
                (
                    transform(item["original_path"], replacements),
                    transform(item["prepared_path"], replacements),
                    transform(item["prepared_receptor_path"], replacements),
                    json.dumps(transform(json_value(item["stats"], {}), replacements)),
                    item["id"],
                ),
            )
        for item in rows(connection, "SELECT * FROM ligands WHERE project_id=?", (new_project_id,)):
            connection.execute(
                "UPDATE ligands SET prepared_sdf_path=?, prepared_pdbqt_path=?, descriptors=?, error=? WHERE id=?",
                (
                    transform(item["prepared_sdf_path"], replacements),
                    transform(item["prepared_pdbqt_path"], replacements),
                    json.dumps(transform(json_value(item["descriptors"], {}), replacements)),
                    transform(item["error"], replacements),
                    item["id"],
                ),
            )
        for item in rows(connection, "SELECT * FROM docking_jobs WHERE project_id=?", (new_project_id,)):
            connection.execute(
                "UPDATE docking_jobs SET parameters=?, ligand_ids=?, error=? WHERE id=?",
                (
                    json.dumps(transform(json_value(item["parameters"], {}), replacements)),
                    json.dumps(transform(json_value(item["ligand_ids"], []), replacements)),
                    transform(item["error"], replacements),
                    item["id"],
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")

    violations = list(connection.execute("PRAGMA foreign_key_check"))
    if violations:
        raise RuntimeError(f"Foreign-key violations after migration: {violations}")
    manifest = {
        "migrated_at": datetime.now(TOKYO).isoformat(),
        "database_backup": str(backup_base.with_suffix(".db")),
        "project_backup": str(backup_base.with_suffix(".zip")),
        "mappings": mappings,
        "moves": [{"from": str(old), "to": str(new)} for old, new in moves],
    }
    manifest_path = backup_root / f"{new_project_id}_readable_names_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"Migrated project to {new_project_id}")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
