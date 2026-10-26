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


def _exclude_terminal_missing_residues(fixer) -> None:
    """Keep only internal gaps; unresolved N/C termini are intentional truncations."""
    chains = list(fixer.topology.chains())
    for key in list(fixer.missingResidues):
        chain_index, insertion_index = key
        residue_count = len(list(chains[chain_index].residues()))
        if insertion_index in {0, residue_count}:
            del fixer.missingResidues[key]


def _missing_residue_report(fixer) -> list[dict]:
    chains = list(fixer.topology.chains())
    report: list[dict] = []
    for (chain_index, insertion_index), names in fixer.missingResidues.items():
        chain = chains[chain_index]
        report.append(
            {
                "_chain_index": chain_index,
                "chain": chain.id or "_",
                "index": insertion_index,
                "residues": list(names),
            }
        )
    return report


def _record_added_residue_numbers(fixer, report: list[dict]) -> list[dict]:
    """Resolve each repaired gap to the residue ids written by PDBFixer."""
    chains = list(fixer.topology.chains())
    offsets: dict[int, int] = {}
    for item in sorted(report, key=lambda value: (value["_chain_index"], value["index"])):
        chain_index = item["_chain_index"]
        count = len(item["residues"])
        start = item["index"] + offsets.get(chain_index, 0)
        residues = list(chains[chain_index].residues())
        item["residue_numbers"] = [str(residue.id) for residue in residues[start : start + count]]
        offsets[chain_index] = offsets.get(chain_index, 0) + count
        del item["_chain_index"]
    return report


def repair_with_pdbfixer(input_path: Path, fixed_path: Path, ph: float = 7.4) -> list[dict]:
    """Add SEQRES-backed missing residues/atoms and hydrogens to a PDB/mmCIF."""
    try:
        from openmm.app import PDBFile
        from pdbfixer import PDBFixer
    except ImportError as exc:  # pragma: no cover - exercised by the tool probe
        raise RuntimeError(
            "PDBFixer and OpenMM are required for receptor preparation; "
            "run scripts/setup_dependencies.py"
        ) from exc

    fixer = PDBFixer(filename=str(input_path))
    fixer.findMissingResidues()
    _exclude_terminal_missing_residues(fixer)
    missing = _missing_residue_report(fixer)
    fixer.findMissingAtoms()
    fixer.addMissingAtoms()
    _record_added_residue_numbers(fixer, missing)
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
