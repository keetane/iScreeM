import csv
import json
import re
import sqlite3
from pathlib import Path

from .chemotypes import (
    chemotype_from_smiles,
    molecular_properties_from_smiles,
    normalized_score_fields,
)

PAGE_SIZE = 100
INDEX_VERSION = 2
FIXED_FIELDS = {
    "rank",
    "ligand_code",
    "ligand_id",
    "ligand_name",
    "smiles",
    "chemotype_id",
    "chemotype_smiles",
    "pose",
    "",
}
SORT_COLUMNS = {
    "rank": "r.rank",
    "receptor_name": "r.receptor_name COLLATE NOCASE",
    "ligand_name": "r.ligand_name COLLATE NOCASE",
    "pose": "r.pose",
    "affinity": "r.affinity",
    "engine": "r.engine COLLATE NOCASE",
    "job_name": "r.job_name COLLATE NOCASE",
}


def open_results_index(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS results (
            result_key TEXT PRIMARY KEY,
            rank INTEGER NOT NULL,
            job_id TEXT NOT NULL,
            job_name TEXT NOT NULL,
            engine TEXT,
            created_at TEXT NOT NULL,
            protein_id TEXT,
            receptor_name TEXT NOT NULL,
            ligand_id TEXT,
            ligand_code TEXT NOT NULL,
            ligand_name TEXT NOT NULL,
            smiles TEXT NOT NULL,
            chemotype_id TEXT NOT NULL DEFAULT '',
            chemotype_smiles TEXT NOT NULL DEFAULT '',
            pose INTEGER NOT NULL,
            affinity REAL,
            scores_json TEXT NOT NULL,
            sdf_path TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS results_rank_idx ON results(rank);
        CREATE INDEX IF NOT EXISTS results_affinity_idx ON results(affinity);
        CREATE INDEX IF NOT EXISTS results_ligand_name_idx ON results(ligand_name);
        CREATE TABLE IF NOT EXISTS indexed_jobs (
            job_id TEXT PRIMARY KEY,
            source_mtime_ns INTEGER NOT NULL,
            score_fields_json TEXT NOT NULL,
            index_version INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS hidden_ligands (
            job_id TEXT NOT NULL,
            ligand_code TEXT NOT NULL,
            PRIMARY KEY(job_id, ligand_code)
        );
        CREATE TABLE IF NOT EXISTS hidden_jobs (
            job_id TEXT PRIMARY KEY
        );
        """
    )
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(results)").fetchall()
    }
    migrated = False
    for name in ("chemotype_id", "chemotype_smiles"):
        if name not in columns:
            connection.execute(
                f"ALTER TABLE results ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
            )
            migrated = True
    if migrated:
        # Re-read legacy score summaries so their chemotypes are backfilled.
        connection.execute("DELETE FROM indexed_jobs")
        connection.execute("DELETE FROM results")
        connection.commit()
    indexed_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(indexed_jobs)").fetchall()
    }
    if "index_version" not in indexed_columns:
        connection.execute(
            "ALTER TABLE indexed_jobs ADD COLUMN index_version INTEGER NOT NULL DEFAULT 0"
        )
        connection.commit()
    return connection


def _number(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def index_job_csv(
    connection: sqlite3.Connection,
    *,
    job: dict,
    csv_path: Path,
    job_root: Path,
    ligand_names: dict[str, str],
    ligand_smiles: dict[str, str],
) -> None:
    source_mtime_ns = csv_path.stat().st_mtime_ns
    indexed = connection.execute(
        "SELECT source_mtime_ns, index_version FROM indexed_jobs WHERE job_id = ?",
        (job["job_id"],),
    ).fetchone()
    if (
        indexed
        and indexed["source_mtime_ns"] == source_mtime_ns
        and indexed["index_version"] == INDEX_VERSION
    ):
        return

    score_fields: set[str] = set()
    connection.execute("DELETE FROM results WHERE job_id = ?", (job["job_id"],))
    chemotype_cache: dict[str, dict[str, str]] = {}
    property_cache: dict[str, dict[str, float | int]] = {}
    with csv_path.open(newline="", errors="replace") as handle:
        for fallback_rank, item in enumerate(csv.DictReader(handle), 1):
            ligand_code = str(item.get("ligand_code") or "").strip()
            if not ligand_code:
                continue
            pose_file = job_root / ligand_code / "docked.sdf"
            if not pose_file.is_file():
                continue
            try:
                pose = int(item.get("pose") or 1)
            except ValueError:
                continue
            try:
                rank = int(item.get("rank") or fallback_rank)
            except ValueError:
                rank = fallback_rank
            scores = {
                key: value
                for key, raw in item.items()
                if key not in FIXED_FIELDS
                and (value := _number(raw)) is not None
            }
            affinity = _number(item.get("affinity"))
            if affinity is not None:
                scores["affinity"] = affinity
                score_fields.add("affinity")
            ligand_id = str(item.get("ligand_id") or "").strip()
            ligand_name = str(item.get("ligand_name") or "").strip()
            ligand_name = ligand_name or ligand_names.get(ligand_id, ligand_code)
            smiles = str(item.get("smiles") or "").strip()
            smiles = smiles or ligand_smiles.get(ligand_id, "")
            properties = property_cache.setdefault(
                smiles, molecular_properties_from_smiles(smiles)
            )
            for key, value in properties.items():
                scores.setdefault(key, value)
            for key, value in normalized_score_fields(
                affinity, scores.get("HAC")
            ).items():
                scores.setdefault(key, value)
            score_fields.update(scores)
            chemotype = {
                "chemotype_id": str(item.get("chemotype_id") or "").strip(),
                "chemotype_smiles": str(
                    item.get("chemotype_smiles") or ""
                ).strip(),
            }
            calculated_chemotype = chemotype_cache.setdefault(
                smiles, chemotype_from_smiles(smiles)
            )
            if calculated_chemotype["chemotype_id"] != "CT-UNKNOWN":
                chemotype = calculated_chemotype
            elif not chemotype["chemotype_id"]:
                chemotype = calculated_chemotype
            result_key = f"{job['job_id']}:{ligand_code}:{pose}"
            connection.execute(
                """
                INSERT INTO results (
                    result_key, rank, job_id, job_name, engine, created_at,
                    protein_id, receptor_name, ligand_id, ligand_code,
                    ligand_name, smiles, chemotype_id, chemotype_smiles,
                    pose, affinity, scores_json, sdf_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_key,
                    rank,
                    job["job_id"],
                    job["job_name"],
                    job.get("engine"),
                    job["created_at"],
                    job.get("protein_id"),
                    job["receptor_name"],
                    ligand_id or None,
                    ligand_code,
                    ligand_name,
                    smiles,
                    chemotype["chemotype_id"],
                    chemotype["chemotype_smiles"],
                    pose,
                    affinity,
                    json.dumps(scores),
                    str(pose_file.resolve()),
                ),
            )
    connection.execute(
        """
        INSERT INTO indexed_jobs(
            job_id, source_mtime_ns, score_fields_json, index_version
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
            source_mtime_ns = excluded.source_mtime_ns,
            score_fields_json = excluded.score_fields_json,
            index_version = excluded.index_version
        """,
        (
            job["job_id"],
            source_mtime_ns,
            json.dumps(sorted(score_fields)),
            INDEX_VERSION,
        ),
    )
    connection.commit()


def prune_indexed_jobs(connection: sqlite3.Connection, keep: set[str]) -> int:
    """Drop cached rows for jobs that no longer have a scores.csv to index.

    index_job_csv() only refreshes a job whose CSV changed, so deleting a job
    used to leave its poses in the index forever. They kept showing in the
    result table even though no job row existed to hide them from the UI.
    """
    connection.execute("DROP TABLE IF EXISTS temp.live_jobs")
    connection.execute("CREATE TEMP TABLE live_jobs (job_id TEXT PRIMARY KEY)")
    try:
        connection.executemany(
            "INSERT OR IGNORE INTO live_jobs(job_id) VALUES (?)",
            ((job_id,) for job_id in keep),
        )
        removed = connection.execute(
            "DELETE FROM indexed_jobs WHERE job_id NOT IN (SELECT job_id FROM live_jobs)"
        ).rowcount
        connection.execute(
            "DELETE FROM results WHERE job_id NOT IN (SELECT job_id FROM live_jobs)"
        )
        connection.commit()
        return removed
    finally:
        connection.execute("DROP TABLE IF EXISTS temp.live_jobs")


def sync_hidden_ligands(connection: sqlite3.Connection, hidden: set[str]) -> None:
    connection.execute("DELETE FROM hidden_ligands")
    values = []
    for value in hidden:
        if ":" not in value:
            continue
        job_id, ligand_code = value.split(":", 1)
        values.append((job_id, ligand_code))
    connection.executemany(
        "INSERT OR IGNORE INTO hidden_ligands(job_id, ligand_code) VALUES (?, ?)",
        values,
    )
    connection.commit()


def sync_hidden_jobs(connection: sqlite3.Connection, hidden: set[str]) -> None:
    connection.execute("DELETE FROM hidden_jobs")
    connection.executemany(
        "INSERT OR IGNORE INTO hidden_jobs(job_id) VALUES (?)",
        ((job_id,) for job_id in hidden),
    )
    connection.commit()


def available_score_fields(connection: sqlite3.Connection) -> list[str]:
    fields: set[str] = set()
    for row in connection.execute(
        """
        SELECT jobs.score_fields_json
        FROM indexed_jobs jobs
        LEFT JOIN hidden_jobs hidden ON hidden.job_id = jobs.job_id
        WHERE hidden.job_id IS NULL
        """
    ):
        try:
            fields.update(json.loads(row["score_fields_json"]))
        except (TypeError, ValueError):
            continue
    fields.discard("affinity")
    return sorted(fields)


def _result_item(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["key"] = item.pop("result_key")
    item["scores"] = json.loads(item.pop("scores_json"))
    return item


def query_selected_results(
    connection: sqlite3.Connection,
    selections: list[tuple[str, str, int]],
) -> list[dict]:
    """Resolve explicitly selected poses without loading the complete result set."""
    connection.execute("DROP TABLE IF EXISTS temp.requested_results")
    connection.execute(
        """
        CREATE TEMP TABLE requested_results (
            ordinal INTEGER NOT NULL,
            job_id TEXT NOT NULL,
            ligand_code TEXT NOT NULL,
            pose INTEGER NOT NULL,
            PRIMARY KEY(job_id, ligand_code, pose)
        )
        """
    )
    try:
        connection.executemany(
            """
            INSERT OR IGNORE INTO requested_results(ordinal, job_id, ligand_code, pose)
            VALUES (?, ?, ?, ?)
            """,
            (
                (ordinal, job_id, ligand_code, pose)
                for ordinal, (job_id, ligand_code, pose) in enumerate(selections)
            ),
        )
        rows = connection.execute(
            """
            SELECT r.*
            FROM requested_results requested
            JOIN results r
              ON r.job_id = requested.job_id
             AND r.ligand_code = requested.ligand_code
             AND r.pose = requested.pose
            LEFT JOIN hidden_ligands hidden
              ON hidden.job_id = r.job_id
             AND hidden.ligand_code = r.ligand_code
            LEFT JOIN hidden_jobs hidden_job ON hidden_job.job_id = r.job_id
            WHERE hidden.job_id IS NULL AND hidden_job.job_id IS NULL
            ORDER BY requested.ordinal ASC
            """
        ).fetchall()
        selected = [_result_item(row) for row in rows]
        found = {
            (row["job_id"], row["ligand_code"], int(row["pose"]))
            for row in selected
        }
        missing = set(selections) - found
        if missing:
            raise ValueError(
                f"Selected docking results are unavailable: {sorted(missing)!r}"
            )
        return selected
    finally:
        connection.execute("DROP TABLE IF EXISTS temp.requested_results")


def query_result_page(
    connection: sqlite3.Connection,
    *,
    page: int,
    sort_key: str,
    direction: str,
) -> dict:
    score_fields = available_score_fields(connection)
    direction_sql = "DESC" if direction.lower() == "desc" else "ASC"
    parameters: list[object] = []
    if sort_key.startswith("score:"):
        score_key = sort_key.removeprefix("score:")
        if score_key not in score_fields or not re.fullmatch(r"[\w.-]+", score_key):
            sort_key = "rank"
            expression = SORT_COLUMNS["rank"]
        else:
            expression = "CAST(json_extract(r.scores_json, ?) AS REAL)"
            parameters.append(f'$."{score_key}"')
    else:
        expression = SORT_COLUMNS.get(sort_key, SORT_COLUMNS["rank"])
        if sort_key not in SORT_COLUMNS:
            sort_key = "rank"
    offset = (page - 1) * PAGE_SIZE
    total = connection.execute(
        """
        SELECT COUNT(*)
        FROM results r
        LEFT JOIN hidden_ligands h
          ON h.job_id = r.job_id AND h.ligand_code = r.ligand_code
        LEFT JOIN hidden_jobs hidden_job ON hidden_job.job_id = r.job_id
        WHERE h.job_id IS NULL AND hidden_job.job_id IS NULL
        """
    ).fetchone()[0]
    rows = connection.execute(
        f"""
        SELECT r.*
        FROM results r
        LEFT JOIN hidden_ligands h
          ON h.job_id = r.job_id AND h.ligand_code = r.ligand_code
        LEFT JOIN hidden_jobs hidden_job ON hidden_job.job_id = r.job_id
        WHERE h.job_id IS NULL AND hidden_job.job_id IS NULL
        ORDER BY
            ({expression}) IS NULL ASC,
            {expression} {direction_sql},
            r.rank ASC,
            r.created_at DESC,
            r.job_id ASC,
            r.ligand_code ASC,
            r.pose ASC
        LIMIT ? OFFSET ?
        """,
        [*parameters, *parameters, PAGE_SIZE, offset],
    ).fetchall()
    items = [_result_item(row) for row in rows]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": PAGE_SIZE,
        "pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
        "sort_key": sort_key,
        "direction": direction_sql.lower(),
        "score_columns": score_fields,
    }


def query_analysis_data(connection: sqlite3.Connection) -> dict:
    """Return the best pose per ligand and job for cross-job analysis."""

    score_columns = ["affinity", *available_score_fields(connection)]
    rows = connection.execute(
        """
        WITH ranked AS (
            SELECT
                r.*,
                ROW_NUMBER() OVER (
                    PARTITION BY r.job_id, r.ligand_code
                    ORDER BY
                        r.affinity IS NULL ASC,
                        r.affinity ASC,
                        r.rank ASC,
                        r.pose ASC
                ) AS pose_rank
            FROM results r
            LEFT JOIN hidden_ligands hidden
              ON hidden.job_id = r.job_id
             AND hidden.ligand_code = r.ligand_code
            LEFT JOIN hidden_jobs hidden_job ON hidden_job.job_id = r.job_id
            WHERE hidden.job_id IS NULL AND hidden_job.job_id IS NULL
        )
        SELECT * FROM ranked
        WHERE pose_rank = 1
        ORDER BY created_at DESC, job_id ASC, rank ASC, ligand_code ASC
        """
    ).fetchall()

    jobs: dict[str, dict] = {}
    points: list[dict] = []
    for row in rows:
        jobs.setdefault(
            row["job_id"],
            {
                "id": row["job_id"],
                "name": row["job_name"],
                "engine": row["engine"],
                "receptor_name": row["receptor_name"],
                "created_at": row["created_at"],
            },
        )
        scores = json.loads(row["scores_json"])
        if row["affinity"] is not None:
            scores["affinity"] = row["affinity"]
        smiles = row["smiles"] or ""
        match_key = smiles or row["ligand_id"] or row["ligand_code"]
        points.append(
            {
                "job_id": row["job_id"],
                "match_key": match_key,
                "ligand_id": row["ligand_id"],
                "ligand_code": row["ligand_code"],
                "ligand_name": row["ligand_name"],
                "smiles": smiles,
                "chemotype_id": row["chemotype_id"] or "CT-UNKNOWN",
                "chemotype_smiles": row["chemotype_smiles"] or "",
                "scores": scores,
            }
        )
    return {
        "jobs": list(jobs.values()),
        "score_columns": score_columns,
        "points": points,
    }
