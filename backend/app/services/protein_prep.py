"""Structure repair and protonation for docking receptors.

PDBFixer is used to restore residues/atoms that are absent from an input
structure.  PDB2PQR then assigns a pH-dependent protonation state and emits a
PDB copy for docking.  We intentionally do not silently delete residues when
either stage fails: an incomplete receptor must be reviewed before docking.
"""

from __future__ import annotations

import json
from pathlib import Path

from .commands import CommandResult, run_command


def _missing_residue_report(fixer) -> list[dict]:
    chains = list(fixer.topology.chains())
    report: list[dict] = []
    for (chain_index, insertion_index), names in fixer.missingResidues.items():
        chain = chains[chain_index]
        report.append(
            {
                "chain": chain.id or "_",
                "index": insertion_index,
                "residues": list(names),
            }
        )
    return report


def repair_with_pdbfixer(input_path: Path, fixed_path: Path, ph: float = 7.4) -> list[dict]:
    """Add SEQRES-backed missing residues/atoms and hydrogens to a PDB/mmCIF."""
    try:
        from pdbfixer import PDBFixer
        from openmm.app import PDBFile
    except ImportError as exc:  # pragma: no cover - exercised by the tool probe
        raise RuntimeError(
            "PDBFixer and OpenMM are required for receptor preparation; "
            "run scripts/setup_dependencies.py"
        ) from exc

    fixer = PDBFixer(filename=str(input_path))
    fixer.findMissingResidues()
    missing = _missing_residue_report(fixer)
    fixer.findMissingAtoms()
    fixer.addMissingAtoms()
    fixer.addMissingHydrogens(ph)
    fixed_path.parent.mkdir(parents=True, exist_ok=True)
    with fixed_path.open("w") as handle:
        PDBFile.writeFile(fixer.topology, fixer.positions, handle, keepIds=True)
    if not fixed_path.is_file() or fixed_path.stat().st_size == 0:
        raise RuntimeError("PDBFixer completed without producing a repaired PDB")
    return missing


def protonate_with_pdb2pqr(
    fixed_path: Path,
    pqr_path: Path,
    prepared_path: Path,
    command: str,
    ph: float = 7.4,
) -> CommandResult:
    """Run PDB2PQR and retain both its PQR output and a PDB copy for docking."""
    pqr_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_command(
        [
            command,
            "--ff=AMBER",
            "--with-ph",
            str(ph),
            "--keep-chain",
            "--pdb-output",
            str(prepared_path),
            str(fixed_path),
            str(pqr_path),
        ],
        fixed_path.parent,
    )
    if result.returncode or not prepared_path.is_file():
        raise RuntimeError(
            f"PDB2PQR failed ({result.returncode}): {result.stderr[-1600:]}"
        )
    if not pqr_path.is_file():
        raise RuntimeError("PDB2PQR completed without producing a PQR file")
    return result


def write_report(path: Path, *, missing_residues: list[dict], p2pqr: CommandResult, ph: float) -> None:
    path.write_text(
        json.dumps(
            {
                "workflow": "PDBFixer -> PDB2PQR",
                "ph": ph,
                "missing_residues_added": missing_residues,
                "pdb2pqr": {
                    "args": p2pqr.args,
                    "returncode": p2pqr.returncode,
                },
            },
            indent=2,
        )
    )
