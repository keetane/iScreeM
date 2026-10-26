import csv
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .models import DockingGrid, DockingJob, JobStatus, Ligand, Pocket, Protein
from .services.commands import run_command
from .services.docking import ADAPTERS, DockingInput
from .services.grids import grid_from_coordinates
from .services.ligand_normalization import (
    normalize_docked_sdf,
    normalize_prepared_sdf,
    prepare_3d_molecule,
    write_validated_sdf,
)
from .services.pdb import (
    infer_organic_hetero_bond_orders,
    parse_structure,
    receptor_atom_coordinates,
    restore_hetero_connectivity,
    select_docking_receptor_source,
    write_docking_receptor,
)
from .services.pocket_volume import find_p2rank_points, pocket_metrics, read_p2rank_points
from .services.protein_prep import protonate_with_pdb2pqr, repair_with_pdbfixer, write_report
from .services.score_parser import parse_docking_scores
from .services.storage_names import docking_job_root, unique_database_id


def _finish(job: DockingJob, status: JobStatus, error: str | None = None):
    job.status, job.error, job.finished_at = status, error, datetime.now(UTC)


def _absolute_path(value: str | Path) -> Path:
    """Resolve paths persisted by the API before changing worker cwd."""
    path = Path(value)
    return path if path.is_absolute() else path.resolve()


def prepare_docking_ligand(ligand: dict, prepared_input: Path) -> dict:
    """Normalize an existing library SDF or automatically prepare SMILES."""
    ligand_input = ligand.get("input")
    ligand_source = _absolute_path(ligand_input) if ligand_input else None
    if ligand_source is not None and ligand_source.is_file():
        report = normalize_prepared_sdf(
            ligand_source,
            prepared_input,
            name=ligand["name"],
            expected_smiles=ligand["smiles"],
            seed=int(ligand["preparation_seed"]),
            force_field=ligand["force_field"],
        )
        report["preparation_source"] = "ligand_library"
        return report

    from rdkit import Chem

    molecule = Chem.MolFromSmiles(ligand["smiles"])
    if molecule is None:
        raise ValueError(f"RDKit could not parse SMILES for {ligand['name']}")
    molecule, canonical_smiles = prepare_3d_molecule(
        molecule,
        name=ligand["name"],
        seed=int(ligand["preparation_seed"]),
        force_field=ligand["force_field"],
    )
    write_validated_sdf(molecule, prepared_input)
    return {
        "preparation_source": "automatic_before_docking",
        "canonical_smiles": canonical_smiles,
        "seed": int(ligand["preparation_seed"]),
        "force_field": ligand["force_field"],
        "input_validated": True,
    }


def run_p2rank(job_id: str, protein_id: str):
    db = SessionLocal()
    job = db.get(DockingJob, job_id)
    try:
        job.status, job.started_at = JobStatus.RUNNING, datetime.now(UTC)
        db.commit()
        protein = db.get(Protein, protein_id)
        work = (
            settings.data_dir / "projects" / protein.project_id / "pockets" / f"{protein.id}_pocket"
        ).resolve()
        work.mkdir(parents=True, exist_ok=True)
        input_path = _absolute_path(protein.original_path)
        result = run_command(
            [settings.p2rank_command, "predict", "-f", str(input_path), "-o", str(work)], work
        )
        (work / "p2rank.stdout.log").write_text(result.stdout)
        (work / "p2rank.stderr.log").write_text(result.stderr)
        if result.returncode:
            raise RuntimeError(f"P2Rank exited {result.returncode}: {result.stderr[-1000:]}")
        candidates = list(work.rglob("*_predictions.csv"))
        if not candidates:
            raise RuntimeError("P2Rank completed without a predictions CSV")
        with candidates[0].open(newline="") as handle:
            rows = list(csv.DictReader(handle, skipinitialspace=True))
        points_path = find_p2rank_points(work)
        points_by_rank = read_p2rank_points(points_path) if points_path else {}
        existing_keys = {
            pocket.pocket_key
            for pocket in db.scalars(select(Pocket).where(Pocket.protein_id == protein.id))
        }
        for index, row in enumerate(rows, 1):
            clean = {
                str(k).strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()
            }

            def val(*keys, default=None):
                for key in keys:
                    if clean.get(key) not in (None, ""):
                        return clean[key]
                return default

            center = {
                axis: float(val(f"center_{axis}", f"center {axis}", default=0)) for axis in "xyz"
            }
            residues = (
                re.split(r"\s+", val("residue_ids", "residues", default="").strip())
                if val("residue_ids", "residues", default="")
                else []
            )
            pocket_key = str(val("name", "pocket", default=index))
            if pocket_key in existing_keys:
                continue
            existing_keys.add(pocket_key)
            rank = int(val("rank", default=index))
            points = points_by_rank.get(rank, [])
            metrics = pocket_metrics(input_path, points) if points else {}
            db.add(
                Pocket(
                    id=unique_database_id(db, Pocket, f"{protein.name}_pocket_{rank}"),
                    project_id=protein.project_id,
                    protein_id=protein.id,
                    pocket_key=pocket_key,
                    rank=rank,
                    score=float(val("score", default=0)),
                    probability=float(val("probability", default=0)),
                    volume=metrics.get("volume"),
                    estimated_max_mw=metrics.get("estimated_max_mw"),
                    volume_method=metrics.get("volume_method"),
                    center=center,
                    residues=residues,
                    points=points,
                )
            )
        job.completed_count = job.success_count = 1
        _finish(job, JobStatus.COMPLETED)
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.get(DockingJob, job_id)
        _finish(job, JobStatus.FAILED, str(exc))
        db.commit()
        raise
    finally:
        db.close()


def prepare_receptor(job_id: str, protein_id: str):
    db = SessionLocal()
    job = db.get(DockingJob, job_id)
    try:
        job.status, job.started_at = JobStatus.RUNNING, datetime.now(UTC)
        db.commit()
        protein = db.get(Protein, protein_id)
        root = _absolute_path(
            settings.data_dir / "projects" / protein.project_id / "receptor" / protein.id
        )
        root.mkdir(parents=True, exist_ok=True)
        input_path = _absolute_path(protein.original_path)
        fixed_pdb = root / "pdbfixer_fixed.pdb"
        prepared_pdb = root / "prepared.pdb"
        prepared_pqr = root / "prepared.pqr"
        report_path = root / "preparation.json"
        missing_residues = repair_with_pdbfixer(input_path, fixed_pdb)
        p2pqr = protonate_with_pdb2pqr(
            fixed_pdb,
            prepared_pqr,
            prepared_pdb,
            settings.pdb2pqr_command,
        )
        restore_hetero_connectivity(fixed_pdb, prepared_pdb)
        infer_organic_hetero_bond_orders(prepared_pdb)
        (root / "preparation.stdout.log").write_text(p2pqr.stdout)
        (root / "preparation.stderr.log").write_text(p2pqr.stderr)
        write_report(report_path, missing_residues=missing_residues, p2pqr=p2pqr, ph=7.4)
        prepared_stats = parse_structure(prepared_pdb)
        source_suffix = input_path.suffix.lower()
        prepared_stats["source_format"] = "cif" if source_suffix in {".cif", ".mmcif"} else "pdb"
        prepared_stats["preparation"] = {
            "workflow": "PDBFixer -> PDB2PQR",
            "fixed_pdb": str(fixed_pdb),
            "pqr": str(prepared_pqr),
            "report": str(report_path),
            "missing_residues_added": missing_residues,
            "ph": 7.4,
        }
        protein.stats = prepared_stats
        # Keep both legacy fields populated, but point them to the PDB that
        # smina/gnina consume directly. No PDBQT is required for these engines.
        protein.prepared_path = str(prepared_pdb)
        protein.prepared_receptor_path = str(prepared_pdb)
        job.completed_count = job.success_count = 1
        _finish(job, JobStatus.COMPLETED)
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.get(DockingJob, job_id)
        _finish(job, JobStatus.FAILED, str(exc))
        db.commit()
        raise
    finally:
        db.close()


def prepare_ligands(job_id: str, ligand_ids: list[str], seed: int, force_field: str):
    from rdkit import Chem
    from rdkit.Chem import Crippen, Descriptors, Lipinski, rdMolDescriptors

    db = SessionLocal()
    job = db.get(DockingJob, job_id)
    job.status, job.started_at = JobStatus.RUNNING, datetime.now(UTC)
    db.commit()
    for ligand_id in ligand_ids:
        ligand = db.get(Ligand, ligand_id)
        try:
            mol = Chem.MolFromSmiles(ligand.smiles)
            if mol is None:
                raise ValueError("RDKit could not parse SMILES")
            mol, normalized_smiles = prepare_3d_molecule(
                mol, name=ligand.name, seed=seed, force_field=force_field
            )
            root = _absolute_path(
                settings.data_dir / "projects" / ligand.project_id / "ligands" / ligand.ligand_code
            )
            root.mkdir(parents=True, exist_ok=True)
            sdf = root / "prepared.sdf"
            write_validated_sdf(mol, sdf)
            ligand.prepared_sdf_path = str(sdf)
            ligand.descriptors = {
                "mw": Descriptors.MolWt(mol),
                "clogp": Crippen.MolLogP(mol),
                "tpsa": rdMolDescriptors.CalcTPSA(mol),
                "hbd": Lipinski.NumHDonors(mol),
                "hba": Lipinski.NumHAcceptors(mol),
                "rotb": Lipinski.NumRotatableBonds(mol),
                "formal_charge": Chem.GetFormalCharge(mol),
                "canonical_smiles": normalized_smiles,
                "normalization": "RDKit Cleanup",
                "seed": seed,
                "force_field": force_field,
            }
            ligand.status = "PREPARED_SDF"
            # smina/gnina can consume SDF directly. PDBQT is retained as an
            # optional compatibility artifact for AutoDock Vina workflows.
            try:
                from meeko import MoleculePreparation, PDBQTWriterLegacy

                setup = MoleculePreparation().prepare(mol)[0]
                pdbqt, ok, message = PDBQTWriterLegacy.write_string(setup)
                if not ok:
                    raise ValueError(message)
                path = root / "prepared.pdbqt"
                path.write_text(pdbqt)
                ligand.prepared_pdbqt_path = str(path)
                ligand.status = "PREPARED"
            except Exception:
                ligand.prepared_pdbqt_path = None
            ligand.error = None
            job.success_count += 1
        except Exception as exc:
            ligand.status, ligand.error = "FAILED", str(exc)
            job.failed_count += 1
        job.completed_count += 1
        db.commit()
    _finish(
        job,
        JobStatus.COMPLETED
        if not job.failed_count
        else (JobStatus.PARTIAL_SUCCESS if job.success_count else JobStatus.FAILED),
    )
    db.commit()
    db.close()


def run_docking(job_id: str):
    db = SessionLocal()
    job = db.get(DockingJob, job_id)
    job.status, job.started_at = JobStatus.RUNNING, datetime.now(UTC)
    db.commit()
    params = job.parameters
    protein = db.get(Protein, params["protein_id"])
    grid = db.get(DockingGrid, params.get("grid_id")) if params.get("grid_id") else None
    adapter = ADAPTERS[job.engine]
    job_root = docking_job_root(settings.data_dir / "projects" / job.project_id, job.id)
    job_root.mkdir(parents=True, exist_ok=True)
    try:
        receptor_selection = select_docking_receptor_source(
            [
                (
                    "prepared",
                    _absolute_path(protein.prepared_path)
                    if protein.prepared_path
                    else None,
                ),
                (
                    "prepared",
                    _absolute_path(protein.prepared_receptor_path)
                    if protein.prepared_receptor_path
                    else None,
                ),
                (
                    "original_unprepared",
                    _absolute_path(protein.original_path) if protein.original_path else None,
                ),
            ]
        )
        if receptor_selection is None:
            raise ValueError("Protein structure file is missing")
        receptor_source, receptor_preparation = receptor_selection
        uses_selected_h2o = "with_selected_h2o" in params
        keep_selected_h2o = bool(params.get("with_selected_h2o", False))
        selected_h2o = set(params.get("selected_h2o") or []) if keep_selected_h2o else set()
        legacy_interacting_h2o = bool(params.get("with_h2o", False)) if not uses_selected_h2o else False
        receptor_input = job_root / (
            "receptor_with_selected_h2o.pdb"
            if keep_selected_h2o
            else "receptor_with_interacting_h2o.pdb"
            if legacy_interacting_h2o
            else "receptor_no_hetatm.pdb"
        )
        receptor_stats = write_docking_receptor(
            receptor_source,
            receptor_input,
            keep_interacting_waters=legacy_interacting_h2o,
            selected_waters=selected_h2o if uses_selected_h2o else None,
        )
        if grid:
            search_center, search_size, search_mode = grid.center, grid.size, "grid"
        else:
            search_center, search_size, _ = grid_from_coordinates(
                receptor_atom_coordinates(receptor_input)
            )
            search_mode = "global_without_grid"
    except Exception as exc:
        _finish(job, JobStatus.FAILED, str(exc))
        db.commit()
        db.close()
        raise
    (job_root / "config.json").write_text(
        json.dumps(
            {
                **params,
                "search_mode": search_mode,
                "search_center": search_center,
                "search_size": search_size,
                "exclude_organic_hetatm": True,
                "with_selected_h2o": keep_selected_h2o,
                "selected_h2o": sorted(selected_h2o),
                "receptor_source": str(receptor_source),
                "receptor_preparation": receptor_preparation,
                "receptor_used": str(receptor_input),
                **receptor_stats,
            },
            indent=2,
        )
    )
    aggregate: list[dict] = []
    tasks = []
    for ligand_id in job.ligand_ids:
        ligand = db.get(Ligand, ligand_id)
        if ligand:
            descriptors = ligand.descriptors or {}
            tasks.append(
                {
                    "ligand_code": ligand.ligand_code,
                    "ligand_id": ligand.id,
                    "name": ligand.name,
                    "smiles": ligand.smiles,
                    "input": ligand.prepared_sdf_path,
                    "preparation_seed": descriptors.get("seed", 61453),
                    "force_field": descriptors.get("force_field", "MMFF94s"),
                }
            )
    for ligand in params.get("uploaded_ligands", []):
        tasks.append(
            {
                "ligand_code": ligand["ligand_code"],
                "ligand_id": "",
                "name": ligand.get("name", ligand["ligand_code"]),
                "smiles": ligand.get("smiles", ""),
                "input": ligand["sdf_path"],
                "preparation_seed": 61453,
                "force_field": "MMFF94s",
            }
        )
    for ligand in tasks:
        if job.cancel_requested:
            _finish(job, JobStatus.CANCELLED)
            db.commit()
            break
        task_root = job_root / ligand["ligand_code"]
        task_root.mkdir(exist_ok=True)
        try:
            prepared_input = task_root / "ligand.prepared.sdf"
            preparation_report = prepare_docking_ligand(ligand, prepared_input)
            output = task_root / "docked.sdf"
            raw_output = task_root / "docked.raw.sdf"
            item = DockingInput(
                receptor_input, prepared_input, raw_output, search_center, search_size, params
            )
            result = run_command(adapter.build_args(item), task_root)
            (task_root / "docking.stdout.log").write_text(result.stdout)
            (task_root / "docking.stderr.log").write_text(result.stderr)
            if result.returncode:
                raise RuntimeError(
                    f"{job.engine} exited {result.returncode}: {result.stderr[-1000:]}"
                )
            if not raw_output.is_file():
                raise RuntimeError(f"{job.engine} did not produce an SDF output")
            output_report = normalize_docked_sdf(raw_output, prepared_input, output)
            (task_root / "normalization.json").write_text(
                json.dumps(
                    {
                        **preparation_report,
                        **output_report,
                        "raw_output": str(raw_output),
                        "normalized_output": str(output),
                    },
                    indent=2,
                )
            )
            output_text = result.stdout + "\n" + result.stderr
            scores = parse_docking_scores(output_text)
            (task_root / "scores.json").write_text(json.dumps(scores, indent=2))
            for pose_index, score in enumerate(scores, start=1):
                aggregate.append(
                    {
                        "ligand_code": ligand["ligand_code"],
                        "ligand_id": ligand["ligand_id"],
                        "smiles": ligand["smiles"],
                        "pose": pose_index,
                        **score,
                    }
                )
            job.success_count += 1
        except Exception as exc:
            (task_root / "error.json").write_text(json.dumps({"error": str(exc)}, indent=2))
            job.failed_count += 1
        job.completed_count += 1
        db.commit()
    aggregate.sort(
        key=lambda row: (
            row.get("affinity") is None,
            row.get("affinity") if row.get("affinity") is not None else 0.0,
        )
    )
    with (job_root / "scores.csv").open("w", newline="") as handle:
        score_fields = sorted(
            {
                key
                for row in aggregate
                for key in row
                if key not in {"ligand_code", "ligand_id", "smiles", "pose"}
            }
        )
        if "affinity" in score_fields:
            score_fields.remove("affinity")
            score_fields.insert(0, "affinity")
        writer = csv.DictWriter(
            handle, fieldnames=["rank", "ligand_code", "ligand_id", "smiles", "pose", *score_fields]
        )
        writer.writeheader()
        for rank, row in enumerate(aggregate, start=1):
            writer.writerow({"rank": rank, **row})
    if job.status != JobStatus.CANCELLED:
        _finish(
            job,
            JobStatus.COMPLETED
            if not job.failed_count
            else (JobStatus.PARTIAL_SUCCESS if job.success_count else JobStatus.FAILED),
        )
        db.commit()
    db.close()
