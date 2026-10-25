#!/usr/bin/env python3
"""Update persisted absolute paths after renaming an iScreeM installation."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


DATABASE_COLUMNS = {
    "proteins": ("original_path", "prepared_path", "prepared_receptor_path", "stats"),
    "ligands": ("prepared_sdf_path", "prepared_pdbqt_path", "descriptors"),
    "docking_jobs": ("parameters",),
}
TEXT_SUFFIXES = {".json", ".csv", ".log", ".txt"}


def migrate_database(database: Path, old_root: str, new_root: str) -> int:
    if not database.is_file():
        return 0
    changed = 0
    connection = sqlite3.connect(database)
    try:
        for table, columns in DATABASE_COLUMNS.items():
            for column in columns:
                cursor = connection.execute(
                    f'UPDATE "{table}" SET "{column}" = replace("{column}", ?, ?) '
                    f'WHERE "{column}" LIKE ?',
                    (old_root, new_root, f"%{old_root}%"),
                )
                changed += max(cursor.rowcount, 0)
        connection.commit()
    finally:
        connection.close()
    return changed


def migrate_artifacts(data_dir: Path, old_root: str, new_root: str) -> int:
    changed = 0
    if not data_dir.is_dir():
        return changed
    for path in data_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            original = path.read_text()
        except (OSError, UnicodeError):
            continue
        updated = original.replace(old_root, new_root)
        if updated != original:
            path.write_text(updated)
            changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("old_root")
    parser.add_argument("new_root")
    args = parser.parse_args()
    old_root = str(Path(args.old_root).expanduser().resolve())
    new_root = str(Path(args.new_root).expanduser().resolve())
    root = Path(new_root)
    database_changes = migrate_database(
        root / "data" / "molecular_docking.db", old_root, new_root
    )
    artifact_changes = migrate_artifacts(root / "data", old_root, new_root)
    print(
        f"Updated {database_changes} database values and {artifact_changes} artifact files"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
