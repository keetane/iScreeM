"""Pose-level protein-ligand interaction fingerprint analysis.

The expensive ProLIF calculation is deliberately isolated from the Analysis
API.  A docking job writes these artifacts once and the normal result index
only consumes the compact summary columns added to ``scores.csv``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable


PROLIF_VERSION = "2.0.3"
IFP_FORMAT_VERSION = 1
DEFAULT_SIMILARITY_THRESHOLD = 0.7
EXACT_BUTINA_LIMIT = 5000
INTERACTIONS = (
    "HBDonor",
    "HBAcceptor",
    "Hydrophobic",
    "PiStacking",
    "Cationic",
    "Anionic",
    "CationPi",
    "PiCation",
)
SUMMARY_FIELDS = (
    "ifp_cluster_id",
    "ifp_cluster_size",
    "ifp_similarity",
    "ifp_count",
    "ifp_occurrence_count",
    "ifp_hbond_count",
    "ifp_hydrophobic_count",
    "ifp_pi_count",
    "ifp_status",
    "ifp_reference_id",
    "ifp_reference_version",
)


def pose_id(job_id: str, ligand_code: str, pose: int) -> str:
    return f"{job_id}:{ligand_code}:{pose}"


def tanimoto(left: set[str], right: set[str]) -> float | None:
    """Binary Tanimoto; two empty fingerprints are unknown, not identical."""
    if not left and not right:
        return None
    union = left | right
    return len(left & right) / len(union) if union else None


def _ordered_clusters(groups: Iterable[Iterable[str]]) -> dict[str, tuple[str, int]]:
    normalized = [sorted(group) for group in groups if group]
    normalized.sort(key=lambda group: (-len(group), group[0]))
    result: dict[str, tuple[str, int]] = {}
    for index, group in enumerate(normalized, 1):
        cluster_id = f"IFP-{index:04d}"
        for item in group:
            result[item] = (cluster_id, len(group))
    return result


def cluster_fingerprints(
    fingerprints: dict[str, set[str]],
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    exact_limit: int = EXACT_BUTINA_LIMIT,
) -> tuple[dict[str, tuple[str, int]], str]:
    """Cluster every non-empty pose without silently sampling large inputs."""
    if not 0 < threshold <= 1:
        raise ValueError("IFP similarity threshold must be in (0, 1]")
    nonempty = {key: value for key, value in fingerprints.items() if value}
    keys = sorted(nonempty)
    if not keys:
        return {}, "none"
    if len(keys) <= exact_limit:
        from rdkit import DataStructs
        from rdkit.ML.Cluster import Butina

        features = sorted({feature for values in nonempty.values() for feature in values})
        positions = {feature: index for index, feature in enumerate(features)}
        vectors = []
        for key in keys:
            vector = DataStructs.ExplicitBitVect(len(features))
            for feature in nonempty[key]:
                vector.SetBit(positions[feature])
            vectors.append(vector)
        distances: list[float] = []
        for index in range(1, len(vectors)):
            similarities = DataStructs.BulkTanimotoSimilarity(
                vectors[index], vectors[:index]
            )
            distances.extend(1.0 - similarity for similarity in similarities)
        clusters = Butina.ClusterData(
            distances,
            len(vectors),
            1.0 - threshold,
            isDistData=True,
            reordering=True,
        )
        return _ordered_clusters([[keys[index] for index in group] for group in clusters]), "butina"

    # Exact all-pairs Butina is quadratic.  Deterministic leader assignment
    # compares every pose with the current representatives and never drops a
    # pose.  The algorithm and threshold are recorded in metadata.
    representatives: list[str] = []
    groups: list[list[str]] = []
    for key in keys:
        best_index = -1
        best_similarity = -1.0
        for index, representative in enumerate(representatives):
            similarity = tanimoto(nonempty[key], nonempty[representative]) or 0.0
            if similarity >= threshold and similarity > best_similarity:
                best_index = index
                best_similarity = similarity
        if best_index < 0:
            representatives.append(key)
            groups.append([key])
        else:
            groups[best_index].append(key)
    return _ordered_clusters(groups), "deterministic_leader"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _residue_label(residue) -> str:
    name = str(getattr(residue, "name", "") or "UNK")
    number = str(getattr(residue, "number", "") or "")
    chain = str(getattr(residue, "chain", "") or "_")
    insertion = str(getattr(residue, "icode", "") or "")
    if name == "UNK" and not number:
        # ProLIF ResidueId.__str__ is stable and still preferable to losing the
        # residue identity when an upstream format omitted a chain field.
        return f"_:{residue}"
    return f"{chain}:{name}{number}{insertion}"


def _pose_features(raw: dict) -> dict:
    features: set[str] = set()
    occurrence_count = 0
    hbond_count = 0
    hydrophobic_count = 0
    pi_count = 0
    counts: dict[str, int] = defaultdict(int)
    for residue_pair, interaction_map in raw.items():
        protein_residue = residue_pair[1]
        residue = _residue_label(protein_residue)
        for interaction, occurrences in interaction_map.items():
            count = len(occurrences) if isinstance(occurrences, (tuple, list)) else 1
            feature = f"{residue}:{interaction}"
            features.add(feature)
            counts[feature] += count
            occurrence_count += count
            if interaction in {"HBDonor", "HBAcceptor"}:
                hbond_count += count
            if interaction == "Hydrophobic":
                hydrophobic_count += count
            if interaction in {"PiStacking", "CationPi", "PiCation"}:
                pi_count += count
    return {
        "features": features,
        "counts": dict(counts),
        "ifp_count": len(features),
        "ifp_occurrence_count": occurrence_count,
        "ifp_hbond_count": hbond_count,
        "ifp_hydrophobic_count": hydrophobic_count,
        "ifp_pi_count": pi_count,
        "ifp_status": "Completed" if features else "No Interaction",
    }


class ProlifCalculator:
    """Reuse the expensive protein conversion for every ligand in one job."""

    def __init__(self, receptor: Path):
        try:
            import MDAnalysis as mda
            import prolif as plf
        except ImportError as exc:  # pragma: no cover - deployment probe
            raise RuntimeError(
                f"IFP analysis requires ProLIF {PROLIF_VERSION}; run setup_dependencies.py"
            ) from exc
        if getattr(plf, "__version__", None) != PROLIF_VERSION:
            raise RuntimeError(
                f"Unsupported ProLIF version {getattr(plf, '__version__', 'unknown')}; "
                f"iScreeM requires {PROLIF_VERSION}"
            )
        universe = mda.Universe(str(receptor))
        atoms = universe.select_atoms("protein")
        if not len(atoms):
            atoms = universe.atoms
        # Docking receptor PDB files generally do not contain CONECT records
        # for standard residues.  MDAnalysis therefore guesses bonds.  Its
        # default chemical inferrer may then create an impossible valence at a
        # close contact, so retain guessed topology but skip global inference.
        self.protein = plf.Molecule.from_mda(atoms, inferrer=None, force=True)
        self.fingerprint = plf.Fingerprint(
            interactions=list(INTERACTIONS), count=True
        )
        self.plf = plf

    def calculate(self, ligand_path: Path) -> list[dict]:
        if ligand_path.suffix.lower() in {".sdf", ".sd"}:
            ligands = self.plf.sdf_supplier(str(ligand_path))
        else:
            from rdkit import Chem

            molecule = Chem.MolFromPDBFile(
                str(ligand_path), removeHs=False, sanitize=True
            )
            if molecule is None:
                raise RuntimeError(
                    f"ProLIF could not read reference ligand {ligand_path.name}"
                )
            ligands = [self.plf.Molecule(molecule)]
        results = []
        for ligand in ligands:
            raw = self.fingerprint.generate(
                ligand, self.protein, metadata=True
            )
            results.append(_pose_features(raw))
        return results


def calculate_ifp(receptor: Path, ligand_sdf: Path) -> list[dict]:
    """Calculate all poses in one file using the pinned ProLIF API."""
    return ProlifCalculator(receptor).calculate(ligand_sdf)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def run_ifp(
    *,
    job_id: str,
    job_root: Path,
    receptor: Path,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    reference: Path | None = None,
) -> dict:
    score_path = job_root / "scores.csv"
    if not score_path.is_file() or not receptor.is_file():
        raise RuntimeError("Docking scores or receptor structure is missing")
    with score_path.open(newline="", errors="replace") as handle:
        reader = csv.DictReader(handle)
        score_rows = list(reader)
        original_fields = list(reader.fieldnames or [])

    cache_material = {
        "format_version": IFP_FORMAT_VERSION,
        "prolif_version": PROLIF_VERSION,
        "interactions": INTERACTIONS,
        "threshold": threshold,
        "receptor_sha256": _sha256(receptor),
        "reference_sha256": _sha256(reference) if reference and reference.is_file() else None,
        "poses": {
            code: _sha256(job_root / code / "docked.sdf")
            for code in sorted({row.get("ligand_code", "") for row in score_rows})
            if code and (job_root / code / "docked.sdf").is_file()
        },
    }
    cache_key = hashlib.sha256(
        json.dumps(cache_material, sort_keys=True).encode()
    ).hexdigest()
    metadata_path = job_root / "ifp_metadata.json"
    summary_path = job_root / "ifp_summary.csv"
    if metadata_path.is_file() and summary_path.is_file():
        metadata = json.loads(metadata_path.read_text())
        if metadata.get("cache_key") == cache_key:
            return {**metadata, "cached": True}

    calculator = ProlifCalculator(receptor)

    by_pose: dict[str, dict] = {}
    failed = 0
    rows_by_ligand: dict[str, list[dict]] = defaultdict(list)
    for row in score_rows:
        rows_by_ligand[row.get("ligand_code", "")].append(row)
    for ligand_code, rows in rows_by_ligand.items():
        pose_file = job_root / ligand_code / "docked.sdf"
        try:
            calculated = calculator.calculate(pose_file)
        except Exception as exc:
            calculated = []
            error = str(exc)
        else:
            error = ""
        for row in rows:
            pose = int(row.get("pose") or 1)
            key = pose_id(job_id, ligand_code, pose)
            if pose <= len(calculated):
                by_pose[key] = calculated[pose - 1]
            else:
                failed += 1
                by_pose[key] = {
                    "features": set(),
                    "counts": {},
                    "ifp_count": None,
                    "ifp_occurrence_count": None,
                    "ifp_hbond_count": None,
                    "ifp_hydrophobic_count": None,
                    "ifp_pi_count": None,
                    "ifp_status": f"IFP Error: {error or 'pose could not be read'}",
                }

    reference_features: set[str] | None = None
    reference_id = None
    reference_version = None
    if reference and reference.is_file():
        try:
            reference_result = calculator.calculate(reference)
            if reference_result:
                reference_features = reference_result[0]["features"]
                reference_id = reference.stem
                reference_version = _sha256(reference)[:16]
        except Exception:
            # Reference failure does not invalidate pose IFPs.
            reference_features = None

    valid_fingerprints = {
        key: value["features"]
        for key, value in by_pose.items()
        if not str(value["ifp_status"]).startswith("IFP Error")
    }
    cluster_map, algorithm = cluster_fingerprints(
        valid_fingerprints, threshold=threshold
    )
    summary_rows: list[dict] = []
    feature_rows: list[dict] = []
    for key in sorted(by_pose):
        value = by_pose[key]
        cluster_id, cluster_size = cluster_map.get(
            key,
            (
                "IFP Error"
                if str(value["ifp_status"]).startswith("IFP Error")
                else "No Interaction",
                0,
            ),
        )
        if cluster_id.startswith("IFP-"):
            # Clusters are calculated per docking job.  Scope the persisted ID
            # so unrelated IFP-0001 clusters from two jobs are never colored
            # or filtered as if they were the same binding mode.
            cluster_id = f"{job_id}:{cluster_id}"
        similarity = (
            tanimoto(value["features"], reference_features)
            if reference_features is not None
            else None
        )
        summary_rows.append(
            {
                "pose_id": key,
                "ifp_cluster_id": cluster_id,
                "ifp_cluster_size": cluster_size,
                "ifp_similarity": similarity,
                "ifp_count": value["ifp_count"],
                "ifp_occurrence_count": value["ifp_occurrence_count"],
                "ifp_hbond_count": value["ifp_hbond_count"],
                "ifp_hydrophobic_count": value["ifp_hydrophobic_count"],
                "ifp_pi_count": value["ifp_pi_count"],
                "ifp_status": value["ifp_status"],
                "ifp_reference_id": reference_id,
                "ifp_reference_version": reference_version,
            }
        )
        for feature in sorted(value["features"]):
            feature_rows.append(
                {
                    "pose_id": key,
                    "feature": feature,
                    "binary": 1,
                    "occurrences": value["counts"].get(feature, 1),
                }
            )

    _write_csv(
        summary_path,
        ["pose_id", *SUMMARY_FIELDS],
        summary_rows,
    )
    import pandas as pd

    pd.DataFrame(
        feature_rows,
        columns=["pose_id", "feature", "binary", "occurrences"],
    ).to_parquet(job_root / "ifp_features.parquet", index=False)
    summaries = {row["pose_id"]: row for row in summary_rows}
    merged_rows = []
    for row in score_rows:
        key = pose_id(job_id, row.get("ligand_code", ""), int(row.get("pose") or 1))
        merged_rows.append({**row, **summaries.get(key, {})})
    merged_fields = [*original_fields, *[field for field in SUMMARY_FIELDS if field not in original_fields]]
    _write_csv(job_root / "docking_results_ifp.csv", merged_fields, merged_rows)
    _write_csv(score_path, merged_fields, merged_rows)

    metadata = {
        **cache_material,
        "cache_key": cache_key,
        "created_at": datetime.now(UTC).isoformat(),
        "cluster_algorithm": algorithm,
        "similarity_metric": "Tanimoto",
        "pose_count": len(summary_rows),
        "success_count": len(summary_rows) - failed,
        "failed_count": failed,
        "reference_id": reference_id,
        "reference_version": reference_version,
        "cached": False,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2))
    return metadata
