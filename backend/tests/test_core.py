import shutil
import subprocess
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.error import URLError
from zoneinfo import ZoneInfo

import pytest

from app.services import pubchem
from app.services.docking import DockingInput, GninaAdapter, SminaAdapter
from app.services.docking_csv import select_docking_results
from app.services.export_names import export_filename
from app.services.grids import grid_from_coordinates
from app.services.ligand_normalization import (
    normalize_docked_sdf,
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
from app.services.pubchem import resolve_ligand_queries
from app.services.sdf import split_sdf_records
from app.services.sdf_export import append_sdf_properties
from app.services.storage_names import docking_job_root, readable_name, unique_directory_name


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


def test_docking_upload_smi_is_three_dimensional_and_job_only(tmp_path: Path):
    pytest.importorskip("rdkit")
    from rdkit import Chem

    from app.services.docking_uploads import prepare_docking_upload

    source = tmp_path / "ligands.smi"
    source.write_text("CCO ethanol\nCC(=O)O acetic acid\n")
    root = tmp_path / "upload-id"
    manifest = prepare_docking_upload(source, root, source.name)
    assert len(manifest["ligands"]) == 2
    assert manifest["ligands"][0]["smiles"] == "CCO"
    molecule = next(
        mol for mol in Chem.SDMolSupplier(manifest["ligands"][0]["sdf_path"], removeHs=False) if mol
    )
    coordinates = molecule.GetConformer().GetPositions()
    assert any(abs(float(value)) > 1e-4 for value in coordinates[:, 2])
    assert (root / "manifest.json").is_file()


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
        {"num_modes": 9, "seed": 0},
    )
    smina = SminaAdapter().build_args(item)
    assert "--num_modes" in smina and "--cpu" not in smina and Path(smina[0]).name == "smina"
    assert smina[smina.index("--seed") + 1] == "0"
    gnina = GninaAdapter().build_args(
        DockingInput(**{**item.__dict__, "parameters": {"cnn_scoring": "rescore", "no_gpu": True}})
    )
    assert Path(gnina[0]).name == "gnina" and "--cnn_scoring" in gnina and "--no_gpu" in gnina


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
