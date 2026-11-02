"""Build ligand poses on a reference ligand already bound to the receptor.

Generated molecules arrive as SMILES, which carry no coordinates. Docking them
from a freshly embedded conformer lets every analogue find its own binding
mode, so an analogue series cannot be compared and the binding mode chosen on
the parent compound is lost. Template docking instead pins the atoms a molecule
shares with the reference ligand onto the reference coordinates and samples
only the rest, so the whole series is scored in one binding mode.
"""

from __future__ import annotations

from pathlib import Path

from .ligand_normalization import normalize_molecule

MIN_TEMPLATE_CORE_ATOMS = 8
MCS_TIMEOUT_SECONDS = 30


def _rdkit():
    from rdkit import Chem
    from rdkit.Chem import AllChem, rdFMCS

    return Chem, AllChem, rdFMCS


def load_template(path: Path):
    """Read the autobox ligand extracted from the receptor structure.

    A HETATM record carries no bond orders, so the molecule is left unsanitised
    and matched on elements and connectivity alone.
    """
    Chem, _, _ = _rdkit()
    template = Chem.MolFromPDBFile(str(path), removeHs=False, sanitize=False)
    if template is None or template.GetNumConformers() == 0:
        raise ValueError(f"Could not read a 3D template ligand from {path.name}")
    template.UpdatePropertyCache(strict=False)
    # Ring perception is skipped along with sanitisation, but substructure
    # matching needs it.
    Chem.FastFindRings(template)
    return template


def _shared_core(template, probe, timeout: int):
    """Match on elements and connectivity, since the template has no bond orders."""
    _, _, rdFMCS = _rdkit()
    parameters = rdFMCS.MCSParameters()
    parameters.AtomTyper = rdFMCS.AtomCompare.CompareElements
    parameters.BondTyper = rdFMCS.BondCompare.CompareAny
    parameters.BondCompareParameters.RingMatchesRingOnly = True
    parameters.Timeout = timeout
    return rdFMCS.FindMCS([template, probe], parameters)


def template_pose(
    molecule,
    template,
    *,
    name: str,
    seed: int,
    force_field: str = "MMFF94s",
    min_core_atoms: int = MIN_TEMPLATE_CORE_ATOMS,
    timeout: int = MCS_TIMEOUT_SECONDS,
):
    """Embed `molecule` with its shared core held on the template coordinates."""
    Chem, AllChem, _ = _rdkit()
    from rdkit.Geometry import Point3D

    normalized = normalize_molecule(molecule)
    smiles = Chem.MolToSmiles(normalized, canonical=True, isomericSmiles=True)

    result = _shared_core(template, normalized, timeout)
    if result.canceled or not result.smartsString:
        raise ValueError(f"No common core with the template ligand was found for {name}")
    pattern = Chem.MolFromSmarts(result.smartsString)
    template_match = template.GetSubstructMatch(pattern) if pattern is not None else ()
    probe_match = normalized.GetSubstructMatch(pattern) if pattern is not None else ()
    if not template_match or len(template_match) != len(probe_match):
        raise ValueError(f"The common core could not be mapped onto the template for {name}")
    if result.numAtoms < min_core_atoms:
        raise ValueError(
            f"{name} shares only {result.numAtoms} atoms with the template ligand "
            f"(at least {min_core_atoms} are needed to place it reliably)"
        )

    prepared = Chem.AddHs(normalized)
    positions = template.GetConformer().GetPositions()
    coordinates = {
        int(probe_index): Point3D(*positions[template_index])
        for template_index, probe_index in zip(template_match, probe_match)
    }
    parameters = AllChem.ETKDGv3()
    parameters.randomSeed = seed
    parameters.useRandomCoords = True
    parameters.SetCoordMap(coordinates)
    if AllChem.EmbedMolecule(prepared, parameters) != 0:
        raise ValueError(f"Could not build a pose on the template for {name}")

    # Relax the substituents while the shared core stays on the template.
    if AllChem.MMFFHasAllMoleculeParams(prepared):
        properties = AllChem.MMFFGetMoleculeProperties(prepared, mmffVariant=force_field)
        field = AllChem.MMFFGetMoleculeForceField(prepared, properties)
    elif AllChem.UFFHasAllMoleculeParams(prepared):
        field = AllChem.UFFGetMoleculeForceField(prepared)
    else:
        field = None
    if field is not None:
        for index in coordinates:
            field.AddFixedPoint(index)
        field.Minimize(maxIts=500)

    Chem.SanitizeMol(prepared)
    prepared.SetProp("_Name", name)
    prepared.SetProp("IDOCK_CANONICAL_SMILES", smiles)
    prepared.SetProp(
        "IDOCK_PREPARATION",
        f"RDKit Cleanup + template pose on {result.numAtoms} shared atoms + {force_field}",
    )
    prepared.SetProp("IDOCK_TEMPLATE_CORE_ATOMS", str(result.numAtoms))
    return prepared, smiles, result.numAtoms
