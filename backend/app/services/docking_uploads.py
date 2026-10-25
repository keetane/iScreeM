import csv
import json
import re
from pathlib import Path
from .ligand_normalization import prepare_3d_molecule, write_validated_sdf


def _column(value: str) -> str:
    return re.sub(r"[\s_\-().]", "", value.casefold())


def _smiles_rows(path: Path) -> list[tuple[str, str]]:
    if path.suffix.lower() == ".csv":
        with path.open(newline="", errors="replace") as handle:
            rows = list(csv.reader(handle))
        if not rows:
            return []
        header = [_column(value) for value in rows[0]]
        smiles_index = next(
            (i for i, value in enumerate(header) if value == "smiles" or "smiles" in value), -1
        )
        if smiles_index < 0:
            raise ValueError("CSV must contain a SMILES column")
        names = {
            "ligand",
            "ligandname",
            "name",
            "mole",
            "molecule",
            "moleculename",
            "compound",
            "compoundname",
            "title",
            "id",
            "code",
        }
        name_index = next(
            (
                i
                for i, value in enumerate(header)
                if value in names or "ligand" in value or "molecule" in value
            ),
            -1,
        )
        return [
            (
                row[smiles_index].strip(),
                row[name_index].strip() if 0 <= name_index < len(row) else f"Ligand {index}",
            )
            for index, row in enumerate(rows[1:], 1)
            if smiles_index < len(row) and row[smiles_index].strip()
        ]
    result = []
    for index, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        result.append((fields[0], " ".join(fields[1:]) or f"Ligand {index}"))
    return result


def prepare_docking_upload(
    source: Path, destination_root: Path, original_name: str, seed: int = 61453
) -> dict:
    """Convert an uploaded SDF/CSV/SMI file into job-only 3D SDF inputs."""
    from rdkit import Chem

    suffix = source.suffix.lower()
    if suffix == ".sdf":
        supplied = Chem.SDMolSupplier(str(source), removeHs=False)
        molecules = [
            (
                mol,
                mol.GetProp("_Name")
                if mol is not None and mol.HasProp("_Name")
                else f"Ligand {index}",
            )
            for index, mol in enumerate(supplied, 1)
            if mol is not None
        ]
    elif suffix in {".csv", ".smi", ".smiles"}:
        molecules = []
        for smiles, name in _smiles_rows(source):
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                raise ValueError(f"RDKit could not parse SMILES for {name}: {smiles}")
            molecules.append((mol, name))
    else:
        raise ValueError("Supported docking ligand formats: .sdf, .csv, .smi, .smiles")
    if not molecules:
        raise ValueError("No valid ligand molecules were found")
    if len(molecules) > 5000:
        raise ValueError("A docking upload is limited to 5000 molecules")

    destination_root.mkdir(parents=True, exist_ok=True)
    entries = []
    for index, (mol, name) in enumerate(molecules, 1):
        code = f"upload_{destination_root.name[:8]}_{index:04d}"
        display_name = str(name).strip() or f"Ligand {index}"
        mol, smiles = prepare_3d_molecule(
            mol,
            name=display_name,
            seed=seed + index,
            force_field="MMFF94s",
            preserve_existing_3d=True,
        )
        output = destination_root / f"{code}.sdf"
        write_validated_sdf(mol, output)
        entries.append(
            {
                "ligand_code": code,
                "name": display_name,
                "smiles": smiles,
                "sdf_path": str(output.resolve()),
            }
        )
    manifest = {
        "upload_id": destination_root.name,
        "original_name": original_name,
        "ligands": entries,
    }
    (destination_root / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def load_docking_upload(project_root: Path, upload_id: str) -> dict:
    if not re.fullmatch(r"[\w.-]+", upload_id, flags=re.UNICODE) or upload_id in {".", ".."}:
        raise ValueError("Invalid docking upload id")
    root = (project_root / "docking_uploads" / upload_id).resolve()
    allowed = (project_root / "docking_uploads").resolve()
    if allowed not in root.parents:
        raise ValueError("Invalid docking upload path")
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Docking ligand upload was not found")
    manifest = json.loads(manifest_path.read_text())
    for ligand in manifest.get("ligands", []):
        path = Path(ligand.get("sdf_path", "")).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError("Docking ligand upload is incomplete")
    return manifest
