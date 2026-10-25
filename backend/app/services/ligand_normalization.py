from __future__ import annotations

from pathlib import Path

from .sdf import split_sdf_records


def _rdkit():
    from rdkit import Chem
    from rdkit.Chem import AllChem
    from rdkit.Chem.MolStandardize import rdMolStandardize

    return Chem, AllChem, rdMolStandardize


def normalize_molecule(molecule):
    """Normalize valence/bond representation while preserving charge and tautomer."""
    Chem, _, rdMolStandardize = _rdkit()
    if molecule is None:
        raise ValueError("RDKit could not parse the ligand")
    working = Chem.Mol(molecule)
    Chem.SanitizeMol(working)
    working = Chem.RemoveHs(working)
    working = rdMolStandardize.Cleanup(working)
    Chem.SanitizeMol(working)
    Chem.AssignStereochemistry(working, cleanIt=True, force=True)
    problems = Chem.DetectChemistryProblems(working)
    if problems:
        raise ValueError("Ligand normalization failed: " + "; ".join(problem.Message() for problem in problems))
    return working


def canonical_smiles(molecule) -> str:
    Chem, _, _ = _rdkit()
    return Chem.MolToSmiles(normalize_molecule(molecule), canonical=True, isomericSmiles=True)


def _has_3d_coordinates(molecule) -> bool:
    if molecule.GetNumConformers() == 0:
        return False
    conformer = molecule.GetConformer()
    return conformer.Is3D() and any(
        abs(conformer.GetAtomPosition(index).z) > 1e-4
        for index in range(molecule.GetNumAtoms())
    )


def prepare_3d_molecule(
    molecule,
    *,
    name: str,
    seed: int,
    force_field: str = "MMFF94s",
    preserve_existing_3d: bool = False,
):
    """Create a sanitized, normalized and validated explicit-H 3D ligand."""
    Chem, AllChem, _ = _rdkit()
    normalized = normalize_molecule(molecule)
    smiles = Chem.MolToSmiles(normalized, canonical=True, isomericSmiles=True)
    prepared = Chem.AddHs(normalized, addCoords=True)
    if not preserve_existing_3d or not _has_3d_coordinates(prepared):
        prepared.RemoveAllConformers()
        parameters = AllChem.ETKDGv3()
        parameters.randomSeed = seed
        if AllChem.EmbedMolecule(prepared, parameters) != 0:
            raise ValueError(f"ETKDGv3 conformer generation failed for {name}")
    if AllChem.MMFFHasAllMoleculeParams(prepared):
        status = AllChem.MMFFOptimizeMolecule(prepared, mmffVariant=force_field)
    elif AllChem.UFFHasAllMoleculeParams(prepared):
        status = AllChem.UFFOptimizeMolecule(prepared)
    else:
        status = 0
    if status < 0:
        raise ValueError(f"3D force-field preparation failed for {name}")
    Chem.SanitizeMol(prepared)
    if not _has_3d_coordinates(prepared):
        raise ValueError(f"Prepared ligand has no 3D coordinates: {name}")
    prepared.SetProp("_Name", name)
    prepared.SetProp("IDOCK_CANONICAL_SMILES", smiles)
    prepared.SetProp("IDOCK_PREPARATION", f"RDKit Cleanup + ETKDGv3 + {force_field}")
    return prepared, smiles


def write_validated_sdf(molecule, destination: Path) -> None:
    Chem, _, _ = _rdkit()
    destination.parent.mkdir(parents=True, exist_ok=True)
    writer = Chem.SDWriter(str(destination))
    writer.write(molecule)
    writer.close()
    loaded = list(Chem.SDMolSupplier(str(destination), removeHs=False, sanitize=True))
    if len(loaded) != 1 or loaded[0] is None:
        destination.unlink(missing_ok=True)
        raise ValueError(f"Prepared SDF failed RDKit validation: {destination.name}")


def normalize_prepared_sdf(
    source: Path,
    destination: Path,
    *,
    name: str,
    expected_smiles: str,
    seed: int,
    force_field: str = "MMFF94s",
) -> dict:
    """Revalidate and rewrite a job-local ligand immediately before docking."""
    Chem, _, _ = _rdkit()
    molecule = next((mol for mol in Chem.SDMolSupplier(str(source), removeHs=False) if mol is not None), None)
    prepared, smiles = prepare_3d_molecule(
        molecule,
        name=name,
        seed=seed,
        force_field=force_field,
        preserve_existing_3d=True,
    )
    if expected_smiles:
        expected = canonical_smiles(Chem.MolFromSmiles(expected_smiles))
        if smiles != expected:
            raise ValueError(f"Prepared ligand does not match its registered SMILES: {name}")
    write_validated_sdf(prepared, destination)
    return {
        "canonical_smiles": smiles,
        "formal_charge": Chem.GetFormalCharge(prepared),
        "atom_count": prepared.GetNumAtoms(),
        "heavy_atom_count": prepared.GetNumHeavyAtoms(),
        "input_validated": True,
    }


def normalize_docked_sdf(raw_output: Path, template_sdf: Path, destination: Path) -> dict:
    """Keep docked coordinates but restore chemistry from the validated input graph."""
    Chem, _, _ = _rdkit()
    template_with_h = next(
        (mol for mol in Chem.SDMolSupplier(str(template_sdf), removeHs=False) if mol is not None),
        None,
    )
    template = normalize_molecule(template_with_h)
    parameters = Chem.AdjustQueryParameters()
    parameters.makeBondsGeneric = True
    query = Chem.AdjustQueryProperties(template, parameters)
    records = split_sdf_records(raw_output.read_text())
    if not records:
        raise ValueError("Docking engine produced no SDF poses")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    writer = Chem.SDWriter(str(temporary))
    try:
        for pose, record in enumerate(records, start=1):
            raw = Chem.MolFromMolBlock(record, removeHs=False, sanitize=False, strictParsing=False)
            if raw is None:
                raise ValueError(f"Docking pose {pose} could not be parsed")
            match = raw.GetSubstructMatch(query)
            if len(match) != template.GetNumAtoms() or raw.GetNumAtoms() != template.GetNumAtoms():
                raise ValueError(f"Docking pose {pose} does not match the prepared ligand graph")
            restored = Chem.Mol(template)
            restored.RemoveAllConformers()
            conformer = Chem.Conformer(template.GetNumAtoms())
            conformer.Set3D(True)
            raw_conformer = raw.GetConformer()
            for template_index, raw_index in enumerate(match):
                conformer.SetAtomPosition(template_index, raw_conformer.GetAtomPosition(raw_index))
            restored.AddConformer(conformer)
            for property_name in raw.GetPropNames(includePrivate=False, includeComputed=False):
                restored.SetProp(property_name, raw.GetProp(property_name))
            restored.SetIntProp("IDOCK_POSE", pose)
            restored.SetProp("IDOCK_CHEMISTRY", "bond orders and formal charges restored from pre-docking template")
            Chem.SanitizeMol(restored)
            writer.write(restored)
    finally:
        writer.close()

    validated = list(Chem.SDMolSupplier(str(temporary), removeHs=False, sanitize=True))
    if len(validated) != len(records) or any(molecule is None for molecule in validated):
        temporary.unlink(missing_ok=True)
        raise ValueError("Normalized docking SDF failed RDKit validation")
    temporary.replace(destination)
    return {
        "pose_count": len(validated),
        "canonical_smiles": Chem.MolToSmiles(template, canonical=True, isomericSmiles=True),
        "formal_charge": Chem.GetFormalCharge(template),
        "output_validated": True,
    }
