from dataclasses import dataclass

import csv
from pathlib import Path

import pandas as pd

from app.services import ifp
from app.services.docking_result_pages import (
    index_job_csv,
    open_results_index,
    query_analysis_data,
)
from app.services.ifp import _pose_features, cluster_fingerprints, tanimoto


@dataclass(frozen=True)
class Residue:
    name: str
    number: int
    chain: str
    icode: str = ""


def test_tanimoto_does_not_treat_two_empty_ifps_as_identical():
    assert tanimoto(set(), set()) is None
    assert tanimoto({"A"}, set()) == 0
    assert tanimoto({"A", "B"}, {"B", "C"}) == 1 / 3


def test_pose_features_distinguish_binary_features_and_occurrences():
    ligand = Residue("LIG", 1, "L")
    asp = Residue("ASP", 129, "A")
    phe = Residue("PHE", 131, "A")
    result = _pose_features(
        {
            (ligand, asp): {
                "HBDonor": ({"distance": 2.8}, {"distance": 3.0}),
                "Hydrophobic": ({"distance": 3.6},),
            },
            (ligand, phe): {"PiStacking": ({"distance": 4.2},)},
        }
    )
    assert result["ifp_count"] == 3
    assert result["ifp_occurrence_count"] == 4
    assert result["ifp_hbond_count"] == 2
    assert result["ifp_hydrophobic_count"] == 1
    assert result["ifp_pi_count"] == 1
    assert "A:ASP129:HBDonor" in result["features"]


def test_cluster_ids_are_deterministic_and_empty_ifps_remain_separate():
    fingerprints = {
        "job:ligand-b:1": {"A:ASP1:HBDonor", "A:PHE2:PiStacking"},
        "job:ligand-a:1": {"A:ASP1:HBDonor", "A:PHE2:PiStacking"},
        "job:ligand-c:1": {"A:LEU3:Hydrophobic"},
        "job:empty:1": set(),
    }
    clusters, algorithm = cluster_fingerprints(fingerprints, threshold=0.7)
    assert algorithm == "butina"
    assert clusters["job:ligand-a:1"] == ("IFP-0001", 2)
    assert clusters["job:ligand-b:1"] == ("IFP-0001", 2)
    assert clusters["job:ligand-c:1"] == ("IFP-0002", 1)
    assert "job:empty:1" not in clusters


def test_large_mode_assigns_every_nonempty_pose_without_sampling():
    fingerprints = {
        "pose-1": {"A"},
        "pose-2": {"A"},
        "pose-3": {"B"},
    }
    clusters, algorithm = cluster_fingerprints(
        fingerprints, threshold=0.7, exact_limit=1
    )
    assert algorithm == "deterministic_leader"
    assert set(clusters) == set(fingerprints)


def test_ifp_artifacts_merge_back_into_pose_rows_and_are_cached(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "job"
    ligand_root = root / "ligand-a"
    ligand_root.mkdir(parents=True)
    receptor = root / "receptor.pdb"
    receptor.write_text("ATOM\n")
    (ligand_root / "docked.sdf").write_text("pose\n$$$$\n")
    (root / "scores.csv").write_text(
        "rank,ligand_code,ligand_id,ligand_name,smiles,pose,affinity\n"
        "1,ligand-a,id-a,Ligand A,CCO,1,-7.5\n"
    )

    class FakeCalculator:
        calls = 0

        def __init__(self, _receptor):
            pass

        def calculate(self, _ligand):
            self.__class__.calls += 1
            return [
                {
                    "features": {"A:ASP1:HBDonor"},
                    "counts": {"A:ASP1:HBDonor": 1},
                    "ifp_count": 1,
                    "ifp_occurrence_count": 1,
                    "ifp_hbond_count": 1,
                    "ifp_hydrophobic_count": 0,
                    "ifp_pi_count": 0,
                    "ifp_status": "Completed",
                }
            ]

    monkeypatch.setattr(ifp, "ProlifCalculator", FakeCalculator)
    monkeypatch.setattr(
        pd.DataFrame,
        "to_parquet",
        lambda self, path, index=False: Path(path).write_text("parquet"),
    )
    result = ifp.run_ifp(
        job_id="job-1", job_root=root, receptor=receptor, threshold=0.7
    )
    assert result["success_count"] == 1
    assert (root / "ifp_features.parquet").is_file()
    assert (root / "ifp_summary.csv").is_file()
    with (root / "scores.csv").open(newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["ifp_cluster_id"] == "job-1:IFP-0001"
    assert row["ifp_count"] == "1"

    cached = ifp.run_ifp(
        job_id="job-1", job_root=root, receptor=receptor, threshold=0.7
    )
    assert cached["cached"] is True
    assert FakeCalculator.calls == 1


def test_ifp_summary_is_indexed_as_category_and_numeric_analysis_metrics(
    tmp_path: Path,
):
    root = tmp_path / "docking-job"
    pose_root = root / "ligand-a"
    pose_root.mkdir(parents=True)
    (pose_root / "docked.sdf").write_text("pose\n$$$$\n")
    score_path = root / "scores.csv"
    score_path.write_text(
        "rank,ligand_code,ligand_id,ligand_name,smiles,pose,affinity,"
        "ifp_cluster_id,ifp_status,ifp_similarity,ifp_count\n"
        "1,ligand-a,id-a,Ligand A,CCO,1,-7.5,IFP-0001,Completed,0.75,4\n"
    )
    connection = open_results_index(tmp_path / "results.sqlite")
    try:
        index_job_csv(
            connection,
            job={
                "job_id": "job-1",
                "job_name": "Docking 1",
                "engine": "smina",
                "created_at": "2026-11-08T00:00:00+00:00",
                "protein_id": "protein-1",
                "receptor_name": "Receptor 1",
            },
            csv_path=score_path,
            job_root=root,
            ligand_names={},
            ligand_smiles={},
        )
        analysis = query_analysis_data(connection)
    finally:
        connection.close()
    assert "ifp_similarity" in analysis["score_columns"]
    assert "ifp_count" in analysis["score_columns"]
    assert analysis["points"][0]["ifp_cluster_id"] == "IFP-0001"
    assert analysis["points"][0]["ifp_status"] == "Completed"
    assert analysis["points"][0]["scores"]["ifp_similarity"] == 0.75
