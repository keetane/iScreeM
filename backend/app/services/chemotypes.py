from __future__ import annotations

import hashlib


DESCRIPTOR_FIELDS = ("MW", "XLogP", "TPSA", "RoB", "HBD", "HBA", "HAC")


def chemotype_from_smiles(smiles: str | None) -> dict[str, str]:
    """Return a stable Bemis-Murcko chemotype for a ligand.

    Ring-free molecules intentionally share the ACYCLIC class. Invalid or
    unavailable structures remain distinguishable from that valid class.
    """

    value = (smiles or "").strip()
    if not value:
        return {"chemotype_id": "CT-UNKNOWN", "chemotype_smiles": ""}

    try:
        from rdkit import Chem
        from rdkit.Chem.Scaffolds import MurckoScaffold

        molecule = Chem.MolFromSmiles(value)
        if molecule is None:
            raise ValueError("invalid SMILES")
        scaffold = MurckoScaffold.MurckoScaffoldSmiles(
            mol=molecule, includeChirality=False
        )
        if not scaffold:
            # Bemis-Murcko is empty for acyclic compounds. A generic molecular
            # graph keeps chain length and branching as a drawable chemotype.
            scaffold = Chem.MolToSmiles(
                MurckoScaffold.MakeScaffoldGeneric(molecule),
                isomericSmiles=False,
            )
    except Exception:
        return {"chemotype_id": "CT-UNKNOWN", "chemotype_smiles": ""}

    digest = hashlib.sha256(scaffold.encode()).hexdigest()[:8].upper()
    return {
        "chemotype_id": f"CT-{digest}",
        "chemotype_smiles": scaffold,
    }


def molecular_properties_from_smiles(smiles: str | None) -> dict[str, float | int]:
    """Calculate docking-analysis descriptors from a canonical SMILES."""

    value = (smiles or "").strip()
    if not value:
        return {}
    try:
        from rdkit import Chem
        from rdkit.Chem import Crippen, Descriptors, Lipinski, rdMolDescriptors

        molecule = Chem.MolFromSmiles(value)
        if molecule is None:
            return {}
        return {
            "MW": Descriptors.MolWt(molecule),
            "XLogP": Crippen.MolLogP(molecule),
            "TPSA": rdMolDescriptors.CalcTPSA(molecule),
            "RoB": Lipinski.NumRotatableBonds(molecule),
            "HBD": Lipinski.NumHDonors(molecule),
            "HBA": Lipinski.NumHAcceptors(molecule),
            "HAC": molecule.GetNumHeavyAtoms(),
        }
    except Exception:
        return {}


def normalized_score_fields(
    score: float | None, heavy_atom_count: float | int | None
) -> dict[str, float]:
    if score is None or not heavy_atom_count:
        return {}
    hac = float(heavy_atom_count)
    return {
        "Score_per_HAC": score / hac,
        "Score_per_HAC2": score / (hac * hac),
    }


def structure_svg(smiles: str, width: int = 240, height: int = 140) -> str:
    """Render a SMILES as an RDKit-generated SVG structure diagram."""

    from rdkit import Chem
    from rdkit.Chem import rdDepictor
    from rdkit.Chem.Draw import rdMolDraw2D

    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError("RDKit could not parse the supplied SMILES")
    rdDepictor.Compute2DCoords(molecule)
    drawer = rdMolDraw2D.MolDraw2DSVG(width, height)
    options = drawer.drawOptions()
    options.padding = 0.08
    drawer.DrawMolecule(molecule)
    drawer.FinishDrawing()
    return drawer.GetDrawingText()
