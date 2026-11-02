import shutil
import subprocess
import sys
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.error import URLError
from zoneinfo import ZoneInfo

import pytest

from app.services import commands, pubchem
from app.services.docking import (
    DockingInput,
    GninaAdapter,
    SminaAdapter,
    local_only_input_error,
)
from app.services.docking_csv import select_docking_results
from app.services.export_names import export_filename
from app.services.grids import grid_from_coordinates, point_in_grid
from app.services.ligand_normalization import (
    normalize_docked_sdf,
    normalize_prepared_sdf,
    prepare_3d_molecule,
    write_validated_sdf,
)
from app.services.pdb import (
    has_hetero_connectivity,
    infer_organic_hetero_bond_orders,
    parse_pdb,
    parse_structure,
    receptor_atom_coordinates,
    restore_hetero_connectivity,
    select_docking_receptor_source,
    write_autobox_ligand,
    write_docking_receptor,
    write_receptor_without_hetatm,
    write_structure_without_residues,
)
from app.services.pocket_volume import (
    ESTIMATED_MW_PER_CUBIC_ANGSTROM,
    calculate_pocket_volume,
    pocket_metrics,
    read_p2rank_points,
)
from app.services.protein_prep import (
    _exclude_terminal_missing_residues,
    _missing_residue_report,
    _record_added_residue_numbers,
)
from app.services.score_parser import (
    combine_scores,
    parse_docking_scores,
    scores_from_sdf,
)
from app.services.pubchem import resolve_ligand_queries
from app.services.sdf import split_sdf_records
from app.services.sdf_export import append_sdf_properties
from app.services.storage_names import (
    docking_job_root,
    readable_docking_name,
    readable_name,
    unique_directory_name,
    unique_docking_job_id,
)


def test_command_resolution_falls_back_to_active_python_environment(tmp_path: Path, monkeypatch):
    environment_bin = tmp_path / "env" / "bin"
    environment_bin.mkdir(parents=True)
    python = environment_bin / "python"
    executable = environment_bin / "pdb2pqr"
    python.touch()
    executable.touch(mode=0o755)
    monkeypatch.setattr(commands.shutil, "which", lambda _command: None)
    monkeypatch.setattr(sys, "executable", str(python))

    assert commands.resolve_command("pdb2pqr") == str(executable)


def test_receptor_repair_skips_terminal_gaps_and_records_internal_residue_ids():
    class Residue:
        def __init__(self, residue_id: str):
            self.id = residue_id

    class Chain:
        id = "A"

        def __init__(self, residue_ids: list[str]):
            self.residue_ids = residue_ids

        def residues(self):
            return iter(Residue(value) for value in self.residue_ids)

    class Topology:
        def __init__(self, chain: Chain):
            self.chain = chain

        def chains(self):
            return iter([self.chain])

    class Fixer:
        def __init__(self):
            self.chain = Chain(["10", "13", "14"])
            self.topology = Topology(self.chain)
            self.missingResidues = {
                (0, 0): ["MET"],
                (0, 1): ["ALA", "GLY"],
                (0, 3): ["LYS"],
            }

    fixer = Fixer()
    _exclude_terminal_missing_residues(fixer)
    report = _missing_residue_report(fixer)
    fixer.chain.residue_ids = ["10", "11", "12", "13", "14"]

    assert fixer.missingResidues == {(0, 1): ["ALA", "GLY"]}
    assert _record_added_residue_numbers(fixer, report) == [
        {
            "chain": "A",
            "index": 1,
            "residues": ["ALA", "GLY"],
            "residue_numbers": ["11", "12"],
        }
    ]


def test_cancel_queued_job_without_rq_id_becomes_terminal():
    from app.main import cancel_queued_job
    from app.models import DockingJob, JobStatus

    job = DockingJob(
        id="stale-job",
        project_id="project",
        name="Pocket prediction",
        kind="POCKET_PREDICTION",
        status=JobStatus.QUEUED,
        parameters={"protein_id": "protein"},
        rq_job_id=None,
    )

    cancel_queued_job(job)

    assert job.cancel_requested is True
    assert job.status == JobStatus.CANCELLED


def test_docking_summaries_lists_project_score_files(tmp_path: Path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app import main
    from app.db import Base
    from app.models import DockingJob, Project

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)

    with Session(engine) as db:
        project = Project(id="20261027_test", name="Test")
        completed = DockingJob(
            id="20261027_docking_5ajv",
            project_id=project.id,
            name="smina docking · 5AJV",
            kind="DOCKING",
        )
        unfinished = DockingJob(
            id="20261027_docking_pending",
            project_id=project.id,
            name="Pending docking",
            kind="DOCKING",
        )
        db.add_all([project, completed, unfinished])
        db.commit()
        score_path = (
            tmp_path
            / "projects"
            / project.id
            / "docking"
            / completed.id
            / "scores.csv"
        )
        score_path.parent.mkdir(parents=True)
        score_path.write_text("rank,ligand_code,pose,affinity\n1,ligand_0001,1,-7.0\n")

        catalog = main.docking_summaries(project.id, db)

    assert catalog["directory"] == str(
        (tmp_path / "projects" / project.id / "docking").resolve()
    )
    assert len(catalog["files"]) == 1
    assert catalog["files"][0]["job_id"] == completed.id
    assert catalog["files"][0]["relative_path"] == (
        f"docking/{completed.id}/scores.csv"
    )
    assert catalog["files"][0]["size"] == score_path.stat().st_size


def test_delete_protein_cancels_stale_queued_job(tmp_path: Path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app import main
    from app.db import Base
    from app.models import DockingJob, JobStatus, Project, Protein

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    source = tmp_path / "protein.pdb"
    source.write_text("END\n")
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)

    with Session(engine) as db:
        project = Project(id="project", name="Test")
        protein = Protein(
            id="protein",
            project_id=project.id,
            name="5ajv",
            original_path=str(source),
            stats={},
        )
        job = DockingJob(
            id="stale-job",
            project_id=project.id,
            name="Pocket prediction",
            kind="POCKET_PREDICTION",
            status=JobStatus.QUEUED,
            parameters={"protein_id": protein.id},
            rq_job_id=None,
        )
        db.add_all([project, protein, job])
        db.commit()

        assert main.delete_protein(project.id, protein.id, db) == {"deleted": protein.id}
        assert db.get(Protein, protein.id) is None
        assert db.get(DockingJob, job.id) is None
        assert not source.exists()


def test_grid_uses_padding_and_minimum():
    center, size, approximate = grid_from_coordinates([[0, 1, 2], [4, 5, 6]])
    assert center == {"x": 2.0, "y": 3.0, "z": 4.0}
    assert size == {"x": 18.0, "y": 18.0, "z": 18.0}
    assert approximate is False


def test_grid_fallback_is_marked_approximate():
    center, size, approximate = grid_from_coordinates([], {"x": 1, "y": 2, "z": 3})
    assert center["z"] == 3 and size["x"] == 18 and approximate


def test_p2rank_points_are_grouped_by_pocket_rank(tmp_path: Path):
    points = tmp_path / "protein_points.pdb.gz"
    import gzip

    with gzip.open(points, "wt") as handle:
        handle.write(
            "HETATM    1 H    STP A   1       0.000   1.000   2.000  0.50 0.900\n"
            "HETATM    2 H    STP A   2       3.000   4.000   5.000  0.50 0.800\n"
            "HETATM    3 H    STP A   0       6.000   7.000   8.000  0.50 0.100\n"
        )

    grouped = read_p2rank_points(points)

    assert grouped == {1: [[0.0, 1.0, 2.0]], 2: [[3.0, 4.0, 5.0]]}


def test_pocket_volume_and_estimated_mw_are_calculated(tmp_path: Path):
    pytest.importorskip("scipy")
    receptor = tmp_path / "receptor.pdb"
    receptor.write_text(
        "ATOM      1  C   ALA A   1      20.000  20.000  20.000  1.00 20.00           C  \nEND\n"
    )

    volume = calculate_pocket_volume(
        receptor, [[0.0, 0.0, 0.0]], spacing=0.5, point_radius=2.0
    )
    metrics = pocket_metrics(receptor, [[0.0, 0.0, 0.0]])

    assert 30.0 <= volume <= 37.0
    assert 10.0 <= metrics["volume"] <= 16.0
    assert metrics["estimated_max_mw"] == round(
        metrics["volume"] * ESTIMATED_MW_PER_CUBIC_ANGSTROM, 1
    )


def test_sdf_split_preserves_blank_header_for_later_poses():
    record = "\n\n\n  2  1  0  0  0  0            999 V2000\n"
    sdf = record + "first\n$$$$\n" + record + "second\n$$$$\n"

    records = split_sdf_records(sdf)

    assert len(records) == 2
    assert records[0].splitlines()[3].startswith("  2  1")
    assert records[1].splitlines()[3].startswith("  2  1")


def test_csv_selection_excludes_unchecked_results():
    rows = [
        {"job_id": "job-a", "ligand_code": "ligand_1", "pose": 1, "rank": 1},
        {"job_id": "job-a", "ligand_code": "ligand_1", "pose": 2, "rank": 2},
        {"job_id": "job-b", "ligand_code": "ligand_2", "pose": 1, "rank": 3},
    ]

    selected = select_docking_results(rows, [("job-a", "ligand_1", 2)])

    assert [(row["ligand_code"], row["pose"]) for row in selected] == [("ligand_1", 2)]


def test_ligand_text_resolves_smiles_and_pubchem_names():
    resolved = resolve_ligand_queries(
        ["CCO ethanol", "aspirin"],
        lookup=lambda name: "CC(=O)OC1=CC=CC=C1C(=O)O" if name == "aspirin" else "",
    )

    assert resolved == [
        {"smiles": "CCO", "name": "ethanol"},
        {"smiles": "CC(=O)OC1=CC=CC=C1C(=O)O", "name": "aspirin"},
    ]


def test_pubchem_retries_transient_dns_errors(monkeypatch):
    attempts = 0

    def fake_urlopen(_request, timeout):
        nonlocal attempts
        assert timeout == 15.0
        attempts += 1
        if attempts < 3:
            raise URLError("temporary DNS failure")
        return BytesIO(
            b'{"PropertyTable":{"Properties":[{"SMILES":"CN1C=NC2=C1C(=O)N(C(=O)N2C)C"}]}}'
        )

    monkeypatch.setattr(pubchem, "urlopen", fake_urlopen)
    monkeypatch.setattr(pubchem, "sleep", lambda _seconds: None)
    monkeypatch.setattr(pubchem, "PUBCHEM_REQUEST_INTERVAL_SECONDS", 0)

    assert pubchem.fetch_pubchem_smiles("caffeine") == (
        "CN1C=NC2=C1C(=O)N(C(=O)N2C)C"
    )
    assert attempts == 3


def test_export_filename_uses_timestamp_and_receptor_names():
    moment = datetime(2026, 10, 25, 14, 7, tzinfo=ZoneInfo("Asia/Tokyo"))

    assert export_filename(["6ETJ"], "csv", current_time=moment) == "20261025_1407_6ETJ.csv"
    assert (
        export_filename(["6ETJ", "5ajv", "6ETJ"], ".pse", current_time=moment)
        == "20261025_1407_6ETJ-5ajv.pse"
    )


def test_storage_names_are_dated_readable_and_unique(tmp_path: Path):
    moment = datetime(2026, 10, 25, 14, 7, tzinfo=ZoneInfo("Asia/Tokyo"))

    assert readable_name("My receptor / A", current_time=moment) == "20261025_My_receptor_A"
    (tmp_path / "20261025_upload_ligands").mkdir()
    assert (
        unique_directory_name(tmp_path, "upload ligands", current_time=moment)
        == "20261025_upload_ligands_02"
    )


def test_docking_names_include_minute_and_optional_safe_comment():
    moment = datetime(2026, 11, 1, 9, 5, tzinfo=ZoneInfo("Asia/Tokyo"))

    assert (
        readable_docking_name("smina docking · 6ETJ", current_time=moment)
        == "202611010905_smina_docking_6ETJ"
    )
    assert (
        readable_docking_name(
            "smina docking · 6ETJ",
            comment="control / ATP site",
            current_time=moment,
        )
        == "202611010905_smina_docking_6ETJ_control_ATP_site"
    )


def test_unique_docking_name_keeps_comment_at_end():
    class Existing:
        pass

    class FakeDb:
        existing = {"202611010905_smina_docking_6ETJ_control"}

        def get(self, _model, candidate):
            return object() if candidate in self.existing else None

    moment = datetime(2026, 11, 1, 9, 5, tzinfo=ZoneInfo("Asia/Tokyo"))

    assert (
        unique_docking_job_id(
            FakeDb(),
            Existing,
            "smina docking · 6ETJ",
            comment="control",
            current_time=moment,
        )
        == "202611010905_smina_docking_6ETJ_02_control"
    )


def test_docking_job_root_keeps_legacy_directory_compatibility(tmp_path: Path):
    legacy = tmp_path / "docking" / "job_legacy-uuid"
    legacy.mkdir(parents=True)

    assert docking_job_root(tmp_path, "legacy-uuid") == legacy.resolve()
    assert (
        docking_job_root(tmp_path, "20261025_docking")
        == (tmp_path / "docking" / "20261025_docking").resolve()
    )


def test_selected_sdf_properties_include_scores():
    record = "ligand\n  iScreeM\n\n  0  0  0  0  0  0            999 V2000\nM  END\n"

    exported = append_sdf_properties(
        record, {"iScreeM_Affinity": -7.25, "iScreeM_Score_RMSD l.b.": 1.5}
    )

    assert ">  <iScreeM_Affinity>\n-7.25" in exported
    assert ">  <iScreeM_Score_RMSD_l.b.>\n1.5" in exported
    assert exported.endswith("$$$$\n")


@pytest.mark.parametrize(
    "smiles",
    [
        "Cn1cnc2n(C)c(=O)n(C)c(=O)c12",
        "N1(C2C=C(OC)C=NN=2)C=NN=N1",
        "C1(ON=C(C)C=1)C1=NN=CC(OC)=C1",
    ],
)
def test_pre_docking_preparation_normalizes_problem_ligands(tmp_path: Path, smiles: str):
    Chem = pytest.importorskip("rdkit.Chem")
    prepared, canonical = prepare_3d_molecule(Chem.MolFromSmiles(smiles), name="test", seed=61453)
    output = tmp_path / "prepared.sdf"
    write_validated_sdf(prepared, output)

    loaded = next(iter(Chem.SDMolSupplier(str(output), removeHs=False)), None)

    assert loaded is not None
    assert Chem.MolToSmiles(Chem.RemoveHs(loaded), canonical=True) == canonical
    assert loaded.GetNumConformers() == 1 and loaded.GetConformer().Is3D()


def test_unprepared_library_ligand_is_prepared_for_docking(tmp_path: Path):
    Chem = pytest.importorskip("rdkit.Chem")
    from app.workers import prepare_docking_ligand

    output = tmp_path / "ligand.prepared.sdf"
    report = prepare_docking_ligand(
        {
            "input": None,
            "name": "ethanol",
            "smiles": "CCO",
            "preparation_seed": 61453,
            "force_field": "MMFF94s",
        },
        output,
    )
    molecule = next(iter(Chem.SDMolSupplier(str(output), removeHs=False)), None)

    assert report["preparation_source"] == "automatic_before_docking"
    assert molecule is not None and molecule.GetNumConformers() == 1


def test_docked_sdf_restores_template_bonds_after_engine_reordering(tmp_path: Path):
    Chem = pytest.importorskip("rdkit.Chem")
    prepared, canonical = prepare_3d_molecule(
        Chem.MolFromSmiles("N1(C2C=C(OC)C=NN=2)C=NN=N1"),
        name="tetrazole",
        seed=61453,
    )
    template = tmp_path / "template.sdf"
    write_validated_sdf(prepared, template)
    heavy = Chem.RemoveHs(prepared)
    reordered = Chem.RenumberAtoms(heavy, list(reversed(range(heavy.GetNumAtoms()))))
    for atom in reordered.GetAtoms():
        atom.SetIsAromatic(False)
    for bond in reordered.GetBonds():
        if bond.GetIsAromatic():
            bond.SetIsAromatic(False)
            bond.SetBondType(Chem.BondType.SINGLE)
    raw = tmp_path / "docked.raw.sdf"
    raw.write_text(Chem.MolToMolBlock(reordered, kekulize=False) + "\n$$$$\n")
    normalized = tmp_path / "docked.sdf"

    report = normalize_docked_sdf(raw, template, normalized)
    poses = list(Chem.SDMolSupplier(str(normalized), removeHs=False, sanitize=True))

    assert report["output_validated"] is True
    assert len(poses) == 1 and poses[0] is not None
    assert Chem.MolToSmiles(poses[0], canonical=True) == canonical


def test_docked_sdf_accepts_polar_hydrogen_retained_by_smina(tmp_path: Path):
    Chem = pytest.importorskip("rdkit.Chem")
    prepared, canonical = prepare_3d_molecule(
        Chem.MolFromSmiles("COc1cccc2c(NCCCCCO[N+](=O)[O-])ccnc12"),
        name="5MPN",
        seed=61453,
    )
    template = tmp_path / "template.sdf"
    write_validated_sdf(prepared, template)

    heavy = Chem.RemoveHs(prepared)
    donor_index = next(
        atom.GetIdx()
        for atom in heavy.GetAtoms()
        if atom.GetAtomicNum() in {7, 8, 16} and atom.GetTotalNumHs() > 0
    )
    raw = Chem.AddHs(heavy, addCoords=True, onlyOnAtoms=[donor_index])
    assert raw.GetNumAtoms() == heavy.GetNumAtoms() + 1
    raw_output = tmp_path / "docked.raw.sdf"
    raw_output.write_text(Chem.MolToMolBlock(raw) + "\n$$$$\n")
    normalized = tmp_path / "docked.sdf"

    report = normalize_docked_sdf(raw_output, template, normalized)
    pose = next(iter(Chem.SDMolSupplier(str(normalized), removeHs=False)), None)

    assert report["output_validated"] is True
    assert pose is not None
    assert pose.GetNumAtoms() == pose.GetNumHeavyAtoms()
    assert Chem.MolToSmiles(pose, canonical=True) == canonical


def test_pdb_parser(tmp_path: Path):
    pdb = tmp_path / "x.pdb"
    pdb.write_text(
        "ATOM      1  CA  ALA A   1      10.000  11.000  12.000  1.00 20.00           C  \nHETATM    2  O   HOH A 101      12.000  11.000  12.000  1.00 20.00           O  \nEND\n"
    )
    stats = parse_pdb(pdb)
    assert stats["atom_count"] == 2
    assert stats["residue_count"] == 1
    assert stats["water_atom_count"] == 1
    assert stats["water_molecules"] == [
        {"name": "HOH", "chain": "A", "residue_number": "101", "atom_count": 1}
    ]


def test_pdb_parser_keeps_organic_hetero_residues_separate(tmp_path: Path):
    pdb = tmp_path / "ligands.pdb"
    pdb.write_text(
        "HETATM    1  C1  ATP A 101      10.000  11.000  12.000  1.00 20.00           C  \n"
        "HETATM    2  N1  ATP A 101      11.000  11.000  12.000  1.00 20.00           N  \n"
        "HETATM    3  C1  ATP B 202      20.000  21.000  22.000  1.00 20.00           C  \n"
        "END\n"
    )
    stats = parse_pdb(pdb)
    assert stats["hetero_molecules"] == [
        {"name": "ATP", "chain": "A", "residue_number": "101", "atom_count": 2},
        {"name": "ATP", "chain": "B", "residue_number": "202", "atom_count": 1},
    ]


def test_docking_receptor_excludes_hetatm_and_dangling_conect(tmp_path: Path):
    source = tmp_path / "prepared.pdb"
    destination = tmp_path / "receptor_no_hetatm.pdb"
    source.write_text(
        "ATOM      1  CA  ALA A   1      10.000  11.000  12.000  1.00 20.00           C  \n"
        "HETATM    2  C1  ATP A 101      12.000  11.000  12.000  1.00 20.00           C  \n"
        "CONECT    1    2\n"
        "TER\nEND\n"
    )
    assert write_receptor_without_hetatm(source, destination) == 1
    written = destination.read_text()
    assert "ATOM      1" in written
    assert "HETATM" not in written
    assert "CONECT" not in written
    assert written.endswith("TER\nEND\n")


def test_restore_hetero_connectivity_translates_serials_and_bond_order(tmp_path: Path):
    template = tmp_path / "fixed.pdb"
    template.write_text(
        "HETATM   10  C1  LIG A 101       0.000   0.000   0.000  1.00 20.00           C  \n"
        "HETATM   11  O1  LIG A 101       1.200   0.000   0.000  1.00 20.00           O  \n"
        "CONECT   10   11   11\nCONECT   11   10   10\nEND\n"
    )
    prepared = tmp_path / "prepared.pdb"
    prepared.write_text(
        "HETATM  201  C1  LIG A 101       0.000   0.000   0.000  1.00 20.00           C  \n"
        "HETATM  202  O1  LIG A 101       1.200   0.000   0.000  1.00 20.00           O  \n"
        "END\n"
    )

    count = restore_hetero_connectivity(template, prepared)
    written = prepared.read_text()

    assert count == 2
    assert "CONECT  201  202  202" in written
    assert "CONECT  202  201  201" in written
    assert has_hetero_connectivity(prepared)


def test_infer_organic_hetero_bond_orders_writes_double_bonds(tmp_path: Path):
    pytest.importorskip("rdkit")
    pdb = tmp_path / "ligand.pdb"
    pdb.write_text(
        "HETATM    1  C1  LIG A   1       0.000   0.000   0.000  1.00  0.00           C  \n"
        "HETATM    2  O1  LIG A   1       1.230   0.000   0.000  1.00  0.00           O  \n"
        "HETATM    3  C2  LIG A   1      -1.500   0.000   0.000  1.00  0.00           C  \n"
        "CONECT    1    2    3\n"
        "CONECT    2    1\n"
        "CONECT    3    1\nEND\n"
    )

    inferred = infer_organic_hetero_bond_orders(pdb)
    contents = pdb.read_text()

    assert inferred == 2
    assert "CONECT    1    2    2    3" in contents
    assert "CONECT    2    1    1" in contents
    assert infer_organic_hetero_bond_orders(pdb) == 0


def test_docking_receptor_keeps_only_interacting_waters(tmp_path: Path):
    source = tmp_path / "prepared.pdb"
    destination = tmp_path / "receptor_with_water.pdb"
    source.write_text(
        "ATOM      1  O   SER A   1      10.000  10.000  10.000  1.00 20.00           O  \n"
        "HETATM    2  O   HOH A 101      12.800  10.000  10.000  1.00 20.00           O  \n"
        "HETATM    3  O   HOH A 102      30.000  30.000  30.000  1.00 20.00           O  \n"
        "HETATM    4  C1  ATP A 201      11.000  10.000  10.000  1.00 20.00           C  \n"
        "END\n"
    )
    stats = write_docking_receptor(source, destination, keep_interacting_waters=True)
    written = destination.read_text()
    assert stats == {"receptor_atom_count": 1, "water_atom_count": 1, "water_residue_count": 1}
    assert "A 101" in written
    assert "A 102" not in written
    assert "ATP" not in written


def test_docking_receptor_keeps_only_selected_waters(tmp_path: Path):
    source = tmp_path / "prepared.pdb"
    destination = tmp_path / "receptor_with_selected_water.pdb"
    source.write_text(
        "ATOM      1  O   SER A   1      10.000  10.000  10.000  1.00 20.00           O  \n"
        "HETATM    2  O   HOH A 101      12.800  10.000  10.000  1.00 20.00           O  \n"
        "HETATM    3  O   HOH A 102      30.000  30.000  30.000  1.00 20.00           O  \n"
        "HETATM    4  C1  ATP A 201      11.000  10.000  10.000  1.00 20.00           C  \n"
        "END\n"
    )

    stats = write_docking_receptor(source, destination, selected_waters={"A:102"})
    written = destination.read_text()

    assert stats == {"receptor_atom_count": 1, "water_atom_count": 1, "water_residue_count": 1}
    assert "A 101" not in written
    assert "A 102" in written
    assert "ATP" not in written


def test_write_autobox_ligand_extracts_only_selected_original_hetatm(tmp_path: Path):
    source = tmp_path / "original.pdb"
    destination = tmp_path / "autobox_ligand.pdb"
    source.write_text(
        "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C  \n"
        "HETATM    2  C1  LIG A 201      10.000  11.000  12.000  1.00 20.00           C  \n"
        "HETATM    3  O1  LIG A 201      11.000  11.000  12.000  1.00 20.00           O  \n"
        "HETATM    4  C1  LIG B 202      30.000  31.000  32.000  1.00 20.00           C  \n"
        "HETATM    5  O   HOH A 301      10.000  10.000  10.000  1.00 20.00           O  \n"
        "END\n"
    )

    coordinates = write_autobox_ligand(
        source,
        destination,
        name="LIG",
        chain="A",
        residue_number="201",
    )
    written = destination.read_text()

    assert coordinates == [[10.0, 11.0, 12.0], [11.0, 11.0, 12.0]]
    assert written.count("HETATM") == 2
    assert "LIG A 201" in written
    assert "LIG B 202" not in written
    assert "HOH" not in written


def test_write_autobox_ligand_accepts_cif_label_chain_id(tmp_path: Path):
    source = tmp_path / "original.cif"
    destination = tmp_path / "autobox_ligand.pdb"
    source.write_text(
        """data_autobox_label_chain
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_seq_id
_atom_site.label_entity_id
_atom_site.label_alt_id
_atom_site.auth_asym_id
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.auth_atom_id
_atom_site.pdbx_PDB_ins_code
_atom_site.pdbx_formal_charge
_atom_site.pdbx_PDB_model_num
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.B_iso_or_equiv
ATOM 1 C CA ALA A 1 1 . A 1 ALA CA ? ? 1 0.0 0.0 0.0 1.0 20.0
HETATM 2 C C1 BWW D . 4 . A 603 BWW C1 ? ? 1 10.0 11.0 12.0 1.0 20.0
HETATM 3 O O1 BWW D . 4 . A 603 BWW O1 ? ? 1 11.0 11.0 12.0 1.0 20.0
"""
    )

    coordinates = write_autobox_ligand(
        source,
        destination,
        name="BWW",
        chain="D",
        residue_number="603",
    )

    assert coordinates == [[10.0, 11.0, 12.0], [11.0, 11.0, 12.0]]
    assert destination.read_text().count("HETATM") == 2
    assert "BWW A 603" in destination.read_text()


def test_docking_receptor_source_falls_back_to_unprepared_original(tmp_path: Path):
    original = tmp_path / "original.pdb"
    original.write_text("ATOM\n")

    selected = select_docking_receptor_source(
        [
            ("prepared", tmp_path / "missing-prepared.pdb"),
            ("prepared", None),
            ("original_unprepared", original),
        ]
    )

    assert selected == (original, "original_unprepared")


def test_structure_edit_removes_selected_residue_and_water(tmp_path: Path):
    source = tmp_path / "source.pdb"
    destination = tmp_path / "edited.pdb"
    source.write_text(
        "ATOM      1  CA  ALA A   1      10.000  11.000  12.000  1.00 20.00           C  \n"
        "ATOM      2  CA  GLY A   2      11.000  11.000  12.000  1.00 20.00           C  \n"
        "HETATM    3  O   HOH A 101      12.000  11.000  12.000  1.00 20.00           O  \n"
        "HETATM    4  C1  LIG A 201      13.000  11.000  12.000  1.00 20.00           C  \n"
        "CONECT    2    4\n"
        "CONECT    4    2\n"
        "END\n"
    )

    stats = write_structure_without_residues(source, destination, {"A:2", "A:101"})
    written = destination.read_text()

    assert stats["removed_residue_count"] == 2
    assert stats["residue_count"] == 1
    assert "ALA A   1" in written
    assert "GLY A   2" not in written
    assert "HOH A 101" not in written
    assert "LIG A 201" in written
    assert "CONECT" not in written


def test_docking_receptor_converts_unprepared_cif_to_pdb(tmp_path: Path):
    source = Path(__file__).parent / "fixtures" / "minimal_alanine.cif"
    destination = tmp_path / "receptor_no_hetatm.pdb"

    stats = write_docking_receptor(source, destination)

    assert stats == {
        "receptor_atom_count": 5,
        "water_atom_count": 0,
        "water_residue_count": 0,
    }
    assert len(receptor_atom_coordinates(destination)) == 5
    assert "ATOM" in destination.read_text()


def test_global_search_box_covers_all_receptor_atoms(tmp_path: Path):
    receptor = tmp_path / "receptor.pdb"
    receptor.write_text(
        "ATOM      1  CA  ALA A   1      -5.000   1.000   9.000  1.00 20.00           C  \n"
        "ATOM      2  O   SER A   2      25.000  31.000  39.000  1.00 20.00           O  \n"
        "HETATM    3  O   HOH A 101      99.000  99.000  99.000  1.00 20.00           O  \n"
        "END\n"
    )
    center, size, approximate = grid_from_coordinates(receptor_atom_coordinates(receptor))
    assert center == {"x": 10.0, "y": 16.0, "z": 24.0}
    assert size == {"x": 38.0, "y": 38.0, "z": 38.0}
    assert approximate is False


def test_docking_upload_smi_defers_three_dimensional_preparation(tmp_path: Path):
    from app.services.docking_uploads import (
        iter_docking_upload_ligands,
        prepare_docking_upload,
    )

    source = tmp_path / "ligands.smi"
    source.write_text("CCO ethanol\nnot-yet-validated deferred ligand\n")
    root = tmp_path / "upload-id"
    manifest = prepare_docking_upload(source, root, source.name)
    entries = list(iter_docking_upload_ligands(manifest))

    assert manifest["streaming"] is True
    assert "ligands" not in manifest
    assert manifest["ligand_count"] == 2
    assert entries[0]["smiles"] == "CCO"
    assert entries[1]["smiles"] == "not-yet-validated"
    assert not list(root.glob("*.sdf"))
    assert (root / "manifest.json").is_file()


def test_docking_upload_has_no_molecule_count_limit(tmp_path: Path):
    from app.services.docking_uploads import (
        iter_docking_upload_ligands,
        prepare_docking_upload,
    )

    source = tmp_path / "large-library.smi"
    source.write_text("".join(f"CC ligand-{index}\n" for index in range(5001)))
    root = tmp_path / "large-upload"

    manifest = prepare_docking_upload(source, root, source.name)
    count = 0
    last = None
    for count, last in enumerate(iter_docking_upload_ligands(manifest), 1):
        pass

    assert manifest["ligand_count"] == 5001
    assert count == 5001
    assert last and last["name"] == "ligand-5000"
    assert (root / "manifest.json").stat().st_size < 1000
    assert not list(root.glob("*.sdf"))


def test_docking_upload_sdf_materializes_one_record_on_demand(tmp_path: Path):
    from app.services.docking_uploads import (
        iter_docking_upload_ligands,
        materialize_sdf_record,
        prepare_docking_upload,
    )

    source = tmp_path / "source.sdf"
    source.write_text(
        "first ligand\nrecord one\n$$$$\n"
        "second ligand\nrecord two\n$$$$\n"
    )
    root = tmp_path / "upload-id"
    manifest = prepare_docking_upload(source, root, source.name)
    entries = iter_docking_upload_ligands(manifest)
    first = next(entries)
    entry = next(entries)

    output = materialize_sdf_record(entry, tmp_path / "task" / "ligand.uploaded.sdf")

    assert manifest["ligand_count"] == 2
    assert first["name"] == "first ligand"
    assert output.read_text() == "second ligand\nrecord two\n$$$$\n"


def test_streamed_docking_scores_are_sorted_on_disk(tmp_path: Path):
    import csv
    import json
    import sqlite3

    from app.workers import _write_streamed_score_csv

    store = sqlite3.connect(":memory:")
    store.execute(
        "CREATE TABLE poses (sequence INTEGER PRIMARY KEY, affinity REAL, row_json TEXT NOT NULL)"
    )
    rows = [
        {
            "ligand_code": "second",
            "ligand_id": "",
            "ligand_name": "Second",
            "smiles": "N",
            "pose": 1,
            "affinity": -5.0,
        },
        {
            "ligand_code": "first",
            "ligand_id": "",
            "ligand_name": "First",
            "smiles": "C",
            "pose": 1,
            "affinity": -8.0,
        },
        {
            "ligand_code": "unscored",
            "ligand_id": "",
            "ligand_name": "Unscored",
            "smiles": "O",
            "pose": 1,
        },
    ]
    for sequence, row in enumerate(rows, 1):
        store.execute(
            "INSERT INTO poses(sequence, affinity, row_json) VALUES (?, ?, ?)",
            (sequence, row.get("affinity"), json.dumps(row)),
        )
    destination = tmp_path / "scores.csv"

    _write_streamed_score_csv(store, destination, {"affinity"})

    with destination.open(newline="") as handle:
        written = list(csv.DictReader(handle))
    assert [row["ligand_code"] for row in written] == ["first", "second", "unscored"]
    assert [row["rank"] for row in written] == ["1", "2", "3"]
    assert written[0]["ligand_name"] == "First"


def test_docking_results_are_paginated_and_sorted_server_side(tmp_path: Path):
    import csv

    from app.services.docking_result_pages import (
        index_job_csv,
        open_results_index,
        query_result_page,
        query_selected_results,
        sync_hidden_jobs,
        sync_hidden_ligands,
    )

    job_root = tmp_path / "job"
    job_root.mkdir()
    csv_path = job_root / "scores.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "rank",
                "ligand_code",
                "ligand_id",
                "ligand_name",
                "smiles",
                "pose",
                "affinity",
                "rmsd_lb",
            ],
        )
        writer.writeheader()
        for index in range(105):
            code = f"ligand-{index:03d}"
            task_root = job_root / code
            task_root.mkdir()
            (task_root / "docked.sdf").write_text("pose\n$$$$\n")
            writer.writerow(
                {
                    "rank": index + 1,
                    "ligand_code": code,
                    "ligand_id": "",
                    "ligand_name": code,
                    "smiles": "C",
                    "pose": 1,
                    "affinity": -10 + index / 10,
                    "rmsd_lb": index / 10,
                }
            )
    connection = open_results_index(tmp_path / "results.sqlite")
    index_job_csv(
        connection,
        job={
            "job_id": "job-1",
            "job_name": "Large docking",
            "engine": "smina",
            "created_at": "2026-10-31T00:00:00+00:00",
            "protein_id": "protein-1",
            "receptor_name": "Receptor 1",
        },
        csv_path=csv_path,
        job_root=job_root,
        ligand_names={},
        ligand_smiles={},
    )

    first_page = query_result_page(
        connection, page=1, sort_key="affinity", direction="asc"
    )
    second_page = query_result_page(
        connection, page=2, sort_key="affinity", direction="asc"
    )
    score_sorted = query_result_page(
        connection, page=1, sort_key="score:rmsd_lb", direction="desc"
    )
    selected = query_selected_results(
        connection,
        [("job-1", "ligand-104", 1), ("job-1", "ligand-000", 1)],
    )
    sync_hidden_ligands(connection, {"job-1:ligand-000"})
    hidden = query_result_page(
        connection, page=1, sort_key="rank", direction="asc"
    )

    assert first_page["total"] == 105
    assert len(first_page["items"]) == 100
    assert first_page["items"][0]["ligand_code"] == "ligand-000"
    assert len(second_page["items"]) == 5
    assert score_sorted["items"][0]["ligand_code"] == "ligand-104"
    assert score_sorted["score_columns"] == ["rmsd_lb"]
    assert [row["ligand_code"] for row in selected] == ["ligand-104", "ligand-000"]
    assert hidden["total"] == 104
    assert hidden["items"][0]["ligand_code"] == "ligand-001"
    with pytest.raises(ValueError, match="ligand-000"):
        query_selected_results(connection, [("job-1", "ligand-000", 1)])
    sync_hidden_jobs(connection, {"job-1"})
    job_hidden = query_result_page(
        connection, page=1, sort_key="rank", direction="asc"
    )
    assert job_hidden["total"] == 0
    with pytest.raises(ValueError, match="ligand-104"):
        query_selected_results(connection, [("job-1", "ligand-104", 1)])
    connection.close()


def test_cif_parser():
    stats = parse_structure(Path(__file__).parent / "fixtures" / "minimal_alanine.cif")
    assert stats["format"] == "cif"
    assert stats["atom_count"] == 5
    assert stats["chains"] == [{"id": "A", "residue_count": 1}]


def test_adapters_only_emit_explicit_options(tmp_path: Path):
    item = DockingInput(
        tmp_path / "r.pdbqt",
        tmp_path / "l.pdbqt",
        tmp_path / "out.sdf",
        {"x": 1, "y": 2, "z": 3},
        {"x": 18, "y": 19, "z": 20},
        {"num_modes": 9, "seed": 0, "local_only": True},
    )
    smina = SminaAdapter().build_args(item)
    assert "--num_modes" in smina and "--cpu" not in smina and Path(smina[0]).name == "smina"
    assert smina[smina.index("--seed") + 1] == "0"
    assert "--local_only" in smina
    gnina = GninaAdapter().build_args(
        DockingInput(**{**item.__dict__, "parameters": {"cnn_scoring": "rescore", "no_gpu": True}})
    )
    assert Path(gnina[0]).name == "gnina" and "--cnn_scoring" in gnina and "--no_gpu" in gnina

    autobox = tmp_path / "original-ligand.pdb"
    autobox_args = SminaAdapter().build_args(
        DockingInput(**{**item.__dict__, "autobox_ligand": autobox})
    )
    assert autobox_args[autobox_args.index("--autobox_ligand") + 1] == str(autobox)
    assert "--center_x" not in autobox_args and "--size_x" not in autobox_args


@pytest.mark.integration
def test_real_meeko_receptor_preparation(tmp_path: Path):
    executable = shutil.which("mk_prepare_receptor.py")
    if not executable:
        pytest.skip("Meeko receptor CLI is not installed")
    source = Path(__file__).parent / "fixtures" / "minimal_alanine.pdb"
    output = tmp_path / "receptor"
    done = subprocess.run(
        [
            executable,
            "-i",
            str(source),
            "-o",
            str(output),
            "--write_pdb",
            str(output.with_suffix(".pdb")),
            "-p",
            str(output.with_suffix(".pdbqt")),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert output.with_suffix(".pdbqt").is_file()


GNINA_POSE_TABLE = """
mode |  affinity  |  intramol  |    CNN     |   CNN
     | (kcal/mol) | (kcal/mol) | pose score | affinity
-----+------------+------------+------------+----------
    1      -10.25       -0.28       0.7601      5.428
    2      -10.71       -0.91       0.6441      5.234
"""

GNINA_LOCAL_ONLY_OUTPUT = """
Commandline: /usr/local/bin/gnina --local_only
Affinity: 0.00000  -1.41088 (kcal/mol)
RMSD: 1.79754
CNNscore: 0.37937
CNNaffinity: 3.90183
CNNvariance: 0.04224
"""


def _sdf_record(**tags: float) -> str:
    body = "pose\n  iScreeM\n\n  0  0  0  0  0  0            999 V2000\nM  END\n"
    for name, value in tags.items():
        body += f">  <{name}>\n{value}\n\n"
    return body + "$$$$\n"


def test_gnina_pose_table_keeps_both_cnn_columns():
    parsed = parse_docking_scores(GNINA_POSE_TABLE)

    assert parsed == [
        {"affinity": -10.25, "intramol": -0.28, "cnn_score": 0.7601, "cnn_affinity": 5.428},
        {"affinity": -10.71, "intramol": -0.91, "cnn_score": 0.6441, "cnn_affinity": 5.234},
    ]


def test_local_only_output_keeps_cnn_scores():
    parsed = parse_docking_scores(GNINA_LOCAL_ONLY_OUTPUT)

    assert parsed == [
        {
            "affinity": 0.0,
            "intramol": -1.41088,
            "rmsd": 1.79754,
            "cnn_score": 0.37937,
            "cnn_affinity": 3.90183,
            "cnn_affinity_variance": 0.04224,
        }
    ]


def test_scores_from_sdf_reads_every_pose(tmp_path: Path):
    path = tmp_path / "docked.raw.sdf"
    path.write_text(
        _sdf_record(
            IDOCK_PREPARATION="RDKit",
            minimizedAffinity=-10.24745,
            CNNscore=0.7601293325,
            CNNaffinity=5.4279847145,
            CNN_VS=4.1259703636,
        )
        + _sdf_record(minimizedAffinity=-10.70655, CNNscore=0.6440595388)
    )

    assert scores_from_sdf(path) == [
        {
            "affinity": -10.24745,
            "cnn_score": 0.7601293325,
            "cnn_affinity": 5.4279847145,
            "cnn_vs": 4.1259703636,
        },
        {"affinity": -10.70655, "cnn_score": 0.6440595388},
    ]


def test_scores_from_sdf_ignores_a_missing_file(tmp_path: Path):
    assert scores_from_sdf(tmp_path / "absent.sdf") == []


def test_combine_scores_prefers_the_sdf_and_fills_log_only_columns():
    combined = combine_scores(
        [{"affinity": -10.24745, "cnn_vs": 4.126}],
        [{"affinity": -10.25, "intramol": -0.28}],
    )

    assert combined == [{"affinity": -10.24745, "cnn_vs": 4.126, "intramol": -0.28}]


def test_combine_scores_keeps_primary_when_pose_counts_disagree():
    assert combine_scores([{"affinity": -1.0}], [{"affinity": -2.0}, {"affinity": -3.0}]) == [
        {"affinity": -1.0}
    ]


def test_local_only_rejects_smiles_input():
    message = local_only_input_error([("library.sdf", "sdf"), ("generated.csv", "csv")])

    assert message is not None
    assert "generated.csv" in message
    assert "Number of modes" in message


def test_local_only_accepts_sdf_input():
    assert local_only_input_error([("poses.sdf", "sdf")]) is None


def test_point_in_grid_includes_the_box_edge():
    center = {"x": 0.0, "y": 0.0, "z": 0.0}
    size = {"x": 10.0, "y": 10.0, "z": 10.0}

    assert point_in_grid((5.0, 0.0, 0.0), center, size)
    assert not point_in_grid((5.1, 0.0, 0.0), center, size)


def _strained_pose_sdf(path: Path):
    """Write a deliberately strained conformer a force field would change."""
    Chem = pytest.importorskip("rdkit.Chem")
    AllChem = pytest.importorskip("rdkit.Chem.AllChem")
    mol = Chem.AddHs(Chem.MolFromSmiles("CCO"))
    AllChem.EmbedMolecule(mol, randomSeed=0xF00D)
    conformer = mol.GetConformer()
    for index in range(mol.GetNumAtoms()):
        position = conformer.GetAtomPosition(index)
        conformer.SetAtomPosition(index, (position.x * 1.25, position.y, position.z + 20.0))
    Chem.SDWriter(str(path)).write(mol)
    return Chem


def _heavy_positions(path: Path):
    """Hydrogens are always rebuilt, so only heavy atoms carry the pose."""
    Chem = pytest.importorskip("rdkit.Chem")
    molecule = next(Chem.SDMolSupplier(str(path), removeHs=False))
    return Chem.RemoveHs(molecule).GetConformer().GetPositions()


def _max_shift(before, after) -> float:
    return max(
        abs(b - a)
        for row_b, row_a in zip(before, after)
        for b, a in zip(row_b, row_a)
    )


def test_keeping_an_uploaded_pose_leaves_the_coordinates_untouched(tmp_path: Path):
    source = tmp_path / "pose.sdf"
    Chem = _strained_pose_sdf(source)
    before = _heavy_positions(source)

    normalize_prepared_sdf(
        source,
        tmp_path / "kept.sdf",
        name="pose",
        expected_smiles="",
        seed=61453,
        relax=False,
    )

    kept = next(Chem.SDMolSupplier(str(tmp_path / "kept.sdf"), removeHs=False))
    assert _max_shift(before, _heavy_positions(tmp_path / "kept.sdf")) < 1e-6
    assert kept.GetProp("IDOCK_PREPARATION") == "RDKit Cleanup, uploaded pose kept"


def test_relaxing_an_uploaded_pose_still_changes_the_geometry(tmp_path: Path):
    source = tmp_path / "pose.sdf"
    Chem = _strained_pose_sdf(source)
    before = _heavy_positions(source)

    normalize_prepared_sdf(
        source,
        tmp_path / "relaxed.sdf",
        name="pose",
        expected_smiles="",
        seed=61453,
    )

    relaxed = next(Chem.SDMolSupplier(str(tmp_path / "relaxed.sdf"), removeHs=False))
    assert _max_shift(before, _heavy_positions(tmp_path / "relaxed.sdf")) > 1e-3
    assert "MMFF94s" in relaxed.GetProp("IDOCK_PREPARATION")
