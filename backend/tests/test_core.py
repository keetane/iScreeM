import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

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
    parse_pdb,
    parse_structure,
    receptor_atom_coordinates,
    write_docking_receptor,
    write_receptor_without_hetatm,
)
from app.services.pocket_volume import (
    ESTIMATED_MW_PER_CUBIC_ANGSTROM,
    calculate_pocket_volume,
    pocket_metrics,
    read_p2rank_points,
)
from app.services.sdf import split_sdf_records
from app.services.sdf_export import append_sdf_properties
from app.services.storage_names import docking_job_root, readable_name, unique_directory_name


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
        {"num_modes": 9},
    )
    smina = SminaAdapter().build_args(item)
    assert "--num_modes" in smina and "--cpu" not in smina and Path(smina[0]).name == "smina"
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
