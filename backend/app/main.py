import asyncio
import csv
import io
import json
import shutil
import subprocess
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import Base, engine, ensure_runtime_schema, get_db
from .models import DockingGrid, DockingJob, JobStatus, Ligand, Pocket, Project, Protein
from .schemas import (
    CsvExportRequest,
    DockingRequest,
    DockingResultDeleteRequest,
    GridCreate,
    GridPatch,
    LigandDeleteRequest,
    LigandImport,
    LigandPatch,
    LigandPrepareRequest,
    LigandQueryImport,
    ProjectCreate,
    ProjectOut,
    PseExportRequest,
    ResidueDeleteRequest,
    SmilesLigand,
)
from .services.commands import all_tools
from .services.docking_csv import select_docking_results
from .services.docking_uploads import load_docking_upload, prepare_docking_upload
from .services.export_names import export_filename
from .services.grids import grid_from_coordinates
from .services.pdb import (
    has_hetero_connectivity,
    infer_organic_hetero_bond_orders,
    parse_structure,
    residue_coordinates,
    restore_hetero_connectivity,
    select_docking_receptor_source,
    write_structure_without_residues,
)
from .services.pocket_volume import (
    VOLUME_METHOD,
    find_p2rank_points,
    pocket_metrics,
    read_p2rank_points,
)
from .services.pubchem import resolve_ligand_queries
from .services.queue import queue
from .services.score_parser import parse_docking_scores
from .services.sdf import split_sdf_records
from .services.sdf_export import append_sdf_properties
from .services.storage_names import (
    docking_job_root,
    readable_name,
    unique_database_id,
    unique_directory_name,
)

settings.prepare()
Base.metadata.create_all(engine)
ensure_runtime_schema()
app = FastAPI(title="iScreeM API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def serialize(obj):
    return {column.name: getattr(obj, column.name) for column in obj.__table__.columns}


def cancel_queued_job(item: DockingJob) -> None:
    """Cancel a job that has not started, including legacy rows without an RQ id."""
    item.cancel_requested = True
    if item.status != JobStatus.QUEUED:
        return
    if item.rq_job_id:
        try:
            queue().remove(item.rq_job_id)
        except Exception:
            # Redis may have forgotten a stale queue item already. The database
            # state must still become terminal so it cannot block receptor edits.
            pass
    item.status = JobStatus.CANCELLED


def enqueue_or_503(func, *args, queue_name="default"):
    try:
        return queue(queue_name).enqueue(func, *args)
    except Exception as exc:
        raise HTTPException(503, f"Redis/RQ unavailable: {exc}") from exc


def resolve_data_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else (Path.cwd() / path).resolve()


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/system/tools")
def tools():
    result = all_tools()
    try:
        queue().connection.ping()
        result["redis"] = {
            "name": "redis",
            "available": True,
            "version": "connected",
            "path": None,
            "error": None,
        }
    except Exception as exc:
        result["redis"] = {
            "name": "redis",
            "available": False,
            "version": None,
            "path": None,
            "error": str(exc),
        }
    return result


@app.post("/api/projects", response_model=ProjectOut)
def create_project(body: ProjectCreate, db: Session = Depends(get_db)):
    item = Project(id=unique_database_id(db, Project, body.name), name=body.name)
    db.add(item)
    db.commit()
    db.refresh(item)
    (settings.data_dir / "projects" / item.id).mkdir(parents=True, exist_ok=True)
    return item


@app.get("/api/projects")
def projects(db: Session = Depends(get_db)):
    return [serialize(x) for x in db.scalars(select(Project).order_by(Project.created_at.desc()))]


@app.get("/api/projects/{project_id}")
def project(project_id: str, db: Session = Depends(get_db)):
    item = db.get(Project, project_id)
    if not item:
        raise HTTPException(404, "Project not found")
    proteins = []
    for protein in db.scalars(
        select(Protein).where(Protein.project_id == project_id).order_by(Protein.created_at.desc())
    ):
        data = serialize(protein)
        # The viewer should reflect receptor preparation once it has completed.
        # Before preparation there is no prepared file, so fall back to the
        # uploaded source.  `source_format` therefore describes the file that
        # /api/proteins/{id}/file will actually return.
        viewer_path = resolve_data_path(data.get("prepared_path"))
        if viewer_path is None or not viewer_path.is_file():
            viewer_path = resolve_data_path(data.get("original_path"))
        source_suffix = (
            viewer_path.suffix.lower()
            if viewer_path
            else Path(data["original_path"]).suffix.lower()
        )
        source_format = "cif" if source_suffix in {".cif", ".mmcif"} else "pdb"
        data["stats"] = {**(data["stats"] or {}), "source_format": source_format}
        # Refresh structure-derived counts/sequences on every response.  This
        # also upgrades existing database rows created before modified
        # residues (such as 6ETJ NEP) were classified as polymer residues.
        source = viewer_path or resolve_data_path(data.get("original_path"))
        try:
            if source is not None:
                prepared_metadata = data["stats"].get("preparation")
                parsed = parse_structure(source)
                data["stats"] = {**(data["stats"] or {}), **parsed, "source_format": source_format}
                if prepared_metadata:
                    data["stats"]["preparation"] = prepared_metadata
        except (OSError, ValueError, TypeError):
            pass
        proteins.append(data)
    pocket_rows = list(db.scalars(select(Pocket).where(Pocket.project_id == project_id)))
    proteins_by_id = {
        protein.id: protein
        for protein in db.scalars(select(Protein).where(Protein.project_id == project_id))
    }
    points_cache: dict[str, dict[int, list[list[float]]]] = {}
    metrics_changed = False
    for pocket in pocket_rows:
        if (
            pocket.volume is not None
            and pocket.estimated_max_mw is not None
            and pocket.volume_method == VOLUME_METHOD
        ):
            continue
        protein = proteins_by_id.get(pocket.protein_id)
        if protein is None:
            continue
        try:
            if protein.id not in points_cache:
                root = settings.data_dir / "projects" / project_id / "pockets" / f"{protein.id}_pocket"
                points_path = find_p2rank_points(root)
                points_cache[protein.id] = read_p2rank_points(points_path) if points_path else {}
            points = points_cache[protein.id].get(pocket.rank, [])
            if not points:
                continue
            receptor_path = resolve_data_path(protein.original_path)
            if receptor_path is None:
                continue
            values = pocket_metrics(receptor_path, points)
            pocket.points = points
            pocket.volume = float(values["volume"])
            pocket.estimated_max_mw = float(values["estimated_max_mw"])
            pocket.volume_method = str(values["volume_method"])
            metrics_changed = True
        except (OSError, ValueError, TypeError):
            continue
    if metrics_changed:
        db.commit()

    pockets = []
    seen_pockets = set()
    for pocket in pocket_rows:
        key = (pocket.protein_id, pocket.pocket_key)
        if key in seen_pockets:
            continue
        seen_pockets.add(key)
        pockets.append(serialize(pocket))
    return {
        **serialize(item),
        "proteins": proteins,
        "pockets": pockets,
        "grids": [
            serialize(x)
            for x in db.scalars(
                select(DockingGrid)
                .where(DockingGrid.project_id == project_id)
                .order_by(DockingGrid.created_at.desc())
            )
        ],
        "ligands": [
            serialize(x) for x in db.scalars(select(Ligand).where(Ligand.project_id == project_id))
        ],
    }


@app.post("/api/projects/{project_id}/protein")
async def upload_protein(
    project_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    if not db.get(Project, project_id):
        raise HTTPException(404, "Project not found")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdb", ".ent", ".cif", ".mmcif"}:
        raise HTTPException(415, "Supported structure formats: .pdb, .ent, .cif, .mmcif")
    receptor_name = Path(file.filename or "receptor").stem
    protein_id = unique_database_id(db, Protein, receptor_name)
    root = settings.data_dir / "projects" / project_id / "receptor" / protein_id
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"original{suffix}"
    size = 0
    with destination.open("wb") as output:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > settings.max_upload_mb * 1024 * 1024:
                output.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(413, "Upload too large")
            output.write(chunk)
    try:
        stats = parse_structure(destination)
    except ValueError as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)) from exc
    item = Protein(
        id=protein_id,
        project_id=project_id,
        name=receptor_name,
        original_path=str(destination),
        stats=stats,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return serialize(item)


@app.get("/api/proteins/{protein_id}/file")
def protein_file(protein_id: str, db: Session = Depends(get_db)):
    item = db.get(Protein, protein_id)
    if not item:
        raise HTTPException(404, "Protein not found")
    # Once receptor prep has completed, serve that repaired/protonated
    # structure to the viewer.  This is what makes PDBFixer-added residues
    # visible instead of silently reverting to the incomplete upload.
    source = resolve_data_path(item.prepared_path)
    if source is None or not source.is_file():
        source = resolve_data_path(item.original_path)
    if source is None or not source.is_file():
        raise HTTPException(404, "Protein structure file is missing")
    if source.suffix.lower() == ".pdb" and not has_hetero_connectivity(source):
        fixed = source.parent / "pdbfixer_fixed.pdb"
        if fixed.is_file():
            restore_hetero_connectivity(fixed, source)
    if source.suffix.lower() == ".pdb" and item.prepared_path:
        infer_organic_hetero_bond_orders(source)
    media_type = (
        "chemical/x-cif" if source.suffix.lower() in {".cif", ".mmcif"} else "chemical/x-pdb"
    )
    return FileResponse(source, media_type=media_type, filename=source.name)


@app.post("/api/projects/{project_id}/proteins/{protein_id}/residues/delete")
def delete_protein_residues(
    project_id: str,
    protein_id: str,
    body: ResidueDeleteRequest,
    db: Session = Depends(get_db),
):
    protein = db.get(Protein, protein_id)
    if not protein or protein.project_id != project_id:
        raise HTTPException(404, "Protein not found")
    active = list(db.scalars(select(DockingJob).where(DockingJob.project_id == project_id)))
    if any(
        job.status
        in {JobStatus.QUEUED, JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.POSTPROCESSING}
        and (job.parameters or {}).get("protein_id") == protein_id
        for job in active
    ):
        raise HTTPException(409, "Protein has an active job; wait for it to finish before editing")
    source = resolve_data_path(protein.prepared_path)
    if source is None or not source.is_file():
        source = resolve_data_path(protein.original_path)
    if source is None or not source.is_file():
        raise HTTPException(404, "Protein structure file is missing")

    parent = settings.data_dir / "projects" / project_id / "receptor" / protein_id
    base = readable_name(f"{protein.name}_edited")
    destination = parent / f"{base}.pdb"
    suffix = 2
    while destination.exists():
        destination = parent / f"{base}_{suffix:02d}.pdb"
        suffix += 1
    try:
        stats = write_structure_without_residues(
            source,
            destination,
            set(body.residue_keys),
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc

    removed = int(stats.pop("removed_residue_count"))
    protein.original_path = str(destination)
    protein.prepared_path = None
    protein.prepared_receptor_path = None
    protein.stats = {
        **stats,
        "editing": {
            "source": str(source),
            "removed_residue_count": removed,
            "selected_residues": body.residue_keys,
        },
    }
    db.commit()
    db.refresh(protein)
    return {"removed_residue_count": removed, "protein": serialize(protein)}


@app.delete("/api/projects/{project_id}/proteins/{protein_id}")
def delete_protein(project_id: str, protein_id: str, db: Session = Depends(get_db)):
    protein = db.get(Protein, protein_id)
    if not protein or protein.project_id != project_id:
        raise HTTPException(404, "Protein not found")
    active = list(db.scalars(select(DockingJob).where(DockingJob.project_id == project_id)))
    queued_jobs = [
        job
        for job in active
        if job.status == JobStatus.QUEUED
        and (job.parameters or {}).get("protein_id") == protein_id
    ]
    for job in queued_jobs:
        cancel_queued_job(job)
    active_jobs = [
        job
        for job in active
        if job.status
        in {JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.POSTPROCESSING}
        and (job.parameters or {}).get("protein_id") == protein_id
    ]
    if active_jobs:
        raise HTTPException(409, "Protein has an active job; cancel it before deletion")

    artifact_dirs = {
        (settings.data_dir / "projects" / project_id / "receptor" / protein_id).resolve(),
        (settings.data_dir / "projects" / project_id / "pockets" / protein_id).resolve(),
        (
            settings.data_dir / "projects" / project_id / "pockets" / f"{protein_id}_pocket"
        ).resolve(),
    }
    artifact_files = set()
    for value in (protein.original_path, protein.prepared_path, protein.prepared_receptor_path):
        path = resolve_data_path(value)
        if path is not None:
            artifact_files.add(path.resolve())
    # Remove docking outputs belonging to this protein, while leaving unrelated
    # jobs in the same project intact.
    for job in active:
        if (job.parameters or {}).get("protein_id") != protein_id:
            continue
        if job.kind == "POCKET_PREDICTION":
            artifact_dirs.add(
                (
                    settings.data_dir / "projects" / project_id / "pockets" / protein_id / job.id
                ).resolve()
            )
            artifact_dirs.add(
                (settings.data_dir / "projects" / project_id / "pockets" / job.id).resolve()
            )
        else:
            artifact_dirs.add(docking_job_root(settings.data_dir / "projects" / project_id, job.id))
        db.delete(job)
    for pocket in list(db.scalars(select(Pocket).where(Pocket.protein_id == protein_id))):
        for grid in list(db.scalars(select(DockingGrid).where(DockingGrid.pocket_id == pocket.id))):
            db.delete(grid)
        db.delete(pocket)
    db.delete(protein)
    db.commit()
    for path in artifact_dirs:
        if path.is_dir():
            shutil.rmtree(path)
    for path in artifact_files:
        if path.is_file():
            path.unlink()
    return {"deleted": protein_id}


@app.post("/api/projects/{project_id}/receptor/prepare")
def receptor_prepare(project_id: str, protein_id: str, db: Session = Depends(get_db)):
    tools = all_tools()
    missing = [key for key in ("pdbfixer", "openmm", "pdb2pqr") if not tools[key]["available"]]
    if missing:
        detail = ", ".join(f"{key}: {tools[key]['error']}" for key in missing)
        raise HTTPException(503, f"PDBFixer/PDB2PQR preparation unavailable ({detail})")
    protein = db.get(Protein, protein_id)
    if not protein or protein.project_id != project_id:
        raise HTTPException(404, "Protein not found")
    item = DockingJob(
        id=unique_database_id(db, DockingJob, f"{protein.name}_receptor_prep"),
        project_id=project_id,
        name="Receptor preparation",
        kind="RECEPTOR_PREPARATION",
        status=JobStatus.QUEUED,
        total_count=1,
        parameters={"protein_id": protein_id, "input": protein.original_path},
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    from .workers import prepare_receptor

    rq = enqueue_or_503(prepare_receptor, item.id, protein_id)
    item.rq_job_id = rq.id
    db.commit()
    return serialize(item)


@app.post("/api/projects/{project_id}/pockets/predict")
def predict(project_id: str, protein_id: str, db: Session = Depends(get_db)):
    available = all_tools()["p2rank"]
    if not available["available"]:
        raise HTTPException(503, f"P2Rank unavailable: {available['error']}")
    protein = db.get(Protein, protein_id)
    if not protein or protein.project_id != project_id:
        raise HTTPException(404, "Protein not found")
    item = DockingJob(
        id=unique_database_id(db, DockingJob, f"{protein.name}_pocket_prediction"),
        project_id=project_id,
        name="Pocket prediction",
        kind="POCKET_PREDICTION",
        status=JobStatus.QUEUED,
        total_count=1,
        parameters={"protein_id": protein_id, "command": settings.p2rank_command},
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    from .workers import run_p2rank

    rq = enqueue_or_503(run_p2rank, item.id, protein_id)
    item.rq_job_id = rq.id
    db.commit()
    return serialize(item)


@app.post("/api/projects/{project_id}/grids")
def create_grid(project_id: str, body: GridCreate, db: Session = Depends(get_db)):
    approximate = False
    if body.pocket_id:
        pocket = db.get(Pocket, body.pocket_id)
        if not pocket or pocket.project_id != project_id:
            raise HTTPException(404, "Pocket not found")
        protein = db.get(Protein, pocket.protein_id)
        if not protein:
            raise HTTPException(404, "Protein not found")
        source = resolve_data_path(protein.original_path)
        if source is None:
            raise HTTPException(422, "Protein source file is missing")
        points = pocket.points or residue_coordinates(source, pocket.residues)
        center, size, approximate = grid_from_coordinates(points, pocket.center)
    elif body.center and body.size:
        center, size = body.center.model_dump(), body.size.model_dump()
    else:
        raise HTTPException(422, "Provide pocket_id or explicit center and size")
    item = DockingGrid(
        id=unique_database_id(db, DockingGrid, body.name),
        project_id=project_id,
        pocket_id=body.pocket_id,
        name=body.name,
        center=center,
        size=size,
        approximate=approximate,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return serialize(item)


@app.patch("/api/grids/{grid_id}")
def patch_grid(grid_id: str, body: GridPatch, db: Session = Depends(get_db)):
    item = db.get(DockingGrid, grid_id)
    if not item:
        raise HTTPException(404, "Grid not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(item, key, value.model_dump() if hasattr(value, "model_dump") else value)
    db.commit()
    db.refresh(item)
    return serialize(item)


@app.delete("/api/grids/{grid_id}")
def delete_grid(grid_id: str, db: Session = Depends(get_db)):
    item = db.get(DockingGrid, grid_id)
    if not item:
        raise HTTPException(404, "Grid not found")
    active = list(db.scalars(select(DockingJob).where(DockingJob.project_id == item.project_id)))
    if any(
        job.status
        in {JobStatus.QUEUED, JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.POSTPROCESSING}
        and (job.parameters or {}).get("grid_id") == grid_id
        for job in active
    ):
        raise HTTPException(409, "Grid has an active docking job; cancel it before deletion")
    db.delete(item)
    db.commit()
    return {"deleted": grid_id}


@app.delete("/api/projects/{project_id}/pockets/{pocket_id}")
def delete_pocket(project_id: str, pocket_id: str, db: Session = Depends(get_db)):
    item = db.get(Pocket, pocket_id)
    if not item or item.project_id != project_id:
        raise HTTPException(404, "Pocket not found")
    active = list(db.scalars(select(DockingJob).where(DockingJob.project_id == project_id)))
    if any(
        job.status
        in {JobStatus.QUEUED, JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.POSTPROCESSING}
        and (job.parameters or {}).get("protein_id") == item.protein_id
        and job.kind == "POCKET_PREDICTION"
        for job in active
    ):
        raise HTTPException(409, "Pocket prediction is still running; cancel it before deletion")
    for grid in list(db.scalars(select(DockingGrid).where(DockingGrid.pocket_id == pocket_id))):
        db.delete(grid)
    db.delete(item)
    db.commit()
    return {"deleted": pocket_id}


@app.post("/api/projects/{project_id}/ligands")
def import_ligands(project_id: str, body: LigandImport, db: Session = Depends(get_db)):
    if not db.get(Project, project_id):
        raise HTTPException(404, "Project not found")
    current = db.scalar(
        select(Ligand).where(Ligand.project_id == project_id).order_by(Ligand.ligand_code.desc())
    )
    start = int(current.ligand_code.split("_")[-1]) + 1 if current else 1
    items = []
    for i, ligand in enumerate(body.ligands):
        code = f"ligand_{start + i:04d}"
        name = ligand.name or f"Ligand {start + i}"
        items.append(
            Ligand(
                id=unique_database_id(db, Ligand, f"{name}_{code}"),
                project_id=project_id,
                ligand_code=code,
                name=name,
                smiles=ligand.smiles,
            )
        )
    db.add_all(items)
    db.commit()
    return [serialize(x) for x in items]


@app.post("/api/projects/{project_id}/ligands/import-text")
def import_ligand_text(
    project_id: str, body: LigandQueryImport, db: Session = Depends(get_db)
):
    try:
        ligands = resolve_ligand_queries(body.queries)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return import_ligands(
        project_id,
        LigandImport(ligands=[SmilesLigand(**ligand) for ligand in ligands]),
        db,
    )


@app.post("/api/projects/{project_id}/ligands/prepare")
def prepare(project_id: str, body: LigandPrepareRequest, db: Session = Depends(get_db)):
    tools = all_tools()
    if not tools["rdkit"]["available"]:
        raise HTTPException(503, "RDKit Python package is not installed")
    ligands = list(
        db.scalars(
            select(Ligand).where(Ligand.id.in_(body.ligand_ids), Ligand.project_id == project_id)
        )
    )
    if len(ligands) != len(set(body.ligand_ids)):
        raise HTTPException(404, "One or more ligands not found")
    item = DockingJob(
        id=unique_database_id(db, DockingJob, "ligand_preparation"),
        project_id=project_id,
        name="Ligand preparation",
        kind="LIGAND_PREPARATION",
        status=JobStatus.QUEUED,
        ligand_ids=body.ligand_ids,
        total_count=len(body.ligand_ids),
        parameters=body.model_dump(),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    from .workers import prepare_ligands

    rq = enqueue_or_503(
        prepare_ligands, item.id, body.ligand_ids, body.random_seed, body.force_field
    )
    item.rq_job_id = rq.id
    db.commit()
    return serialize(item)


@app.patch("/api/projects/{project_id}/ligands/{ligand_id}")
def patch_ligand(project_id: str, ligand_id: str, body: LigandPatch, db: Session = Depends(get_db)):
    item = db.get(Ligand, ligand_id)
    if not item or item.project_id != project_id:
        raise HTTPException(404, "Ligand not found")
    changes = {
        key: value.strip()
        for key, value in body.model_dump(exclude_unset=True).items()
        if value is not None
    }
    if any(not value for value in changes.values()):
        raise HTTPException(422, "Ligand ID, name, and SMILES cannot be blank")
    new_code = changes.get("ligand_code")
    if new_code and new_code != item.ligand_code:
        duplicate = db.scalar(
            select(Ligand).where(
                Ligand.project_id == project_id,
                Ligand.ligand_code == new_code,
                Ligand.id != ligand_id,
            )
        )
        if duplicate:
            raise HTTPException(409, f"Ligand ID '{new_code}' already exists in this project")
    old_paths: list[Path | None] = []
    new_smiles = changes.get("smiles")
    if new_smiles and new_smiles != item.smiles:
        active = list(db.scalars(select(DockingJob).where(DockingJob.project_id == project_id)))
        if any(
            job.status
            in {JobStatus.QUEUED, JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.POSTPROCESSING}
            and ligand_id in (job.ligand_ids or [])
            for job in active
        ):
            raise HTTPException(409, "Ligand has an active job; cancel it before changing SMILES")
        old_paths = [
            resolve_data_path(item.prepared_sdf_path),
            resolve_data_path(item.prepared_pdbqt_path),
        ]
        item.descriptors = {}
        item.status = "IMPORTED"
        item.error = None
        item.prepared_sdf_path = None
        item.prepared_pdbqt_path = None
    for key, value in changes.items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    for path in old_paths:
        if path and path.is_file():
            path.unlink()
    return serialize(item)


@app.post("/api/projects/{project_id}/ligands/delete")
def delete_ligands(project_id: str, body: LigandDeleteRequest, db: Session = Depends(get_db)):
    requested_ids = list(dict.fromkeys(body.ligand_ids))
    items = list(
        db.scalars(
            select(Ligand).where(Ligand.project_id == project_id, Ligand.id.in_(requested_ids))
        )
    )
    if len(items) != len(requested_ids):
        raise HTTPException(404, "One or more ligands were not found")
    active = list(db.scalars(select(DockingJob).where(DockingJob.project_id == project_id)))
    active_ids = {
        ligand_id
        for job in active
        if job.status
        in {JobStatus.QUEUED, JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.POSTPROCESSING}
        for ligand_id in (job.ligand_ids or [])
    }
    blocked = [item.ligand_code for item in items if item.id in active_ids]
    if blocked:
        raise HTTPException(
            409, f"Active jobs use: {', '.join(blocked)}; cancel them before deletion"
        )
    paths = [
        path
        for item in items
        for path in (
            resolve_data_path(item.prepared_sdf_path),
            resolve_data_path(item.prepared_pdbqt_path),
        )
    ]
    for item in items:
        db.delete(item)
    db.commit()
    for path in paths:
        if path and path.is_file():
            path.unlink()
    return {"deleted": requested_ids, "count": len(requested_ids)}


@app.delete("/api/projects/{project_id}/ligands/{ligand_id}")
def delete_ligand(project_id: str, ligand_id: str, db: Session = Depends(get_db)):
    item = db.get(Ligand, ligand_id)
    if not item or item.project_id != project_id:
        raise HTTPException(404, "Ligand not found")
    active = list(db.scalars(select(DockingJob).where(DockingJob.project_id == project_id)))
    if any(
        job.status
        in {JobStatus.QUEUED, JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.POSTPROCESSING}
        and ligand_id in (job.ligand_ids or [])
        for job in active
    ):
        raise HTTPException(409, "Ligand has an active job; cancel it before deletion")
    paths = [resolve_data_path(item.prepared_sdf_path), resolve_data_path(item.prepared_pdbqt_path)]
    db.delete(item)
    db.commit()
    for path in paths:
        if path and path.is_file():
            path.unlink()
    return {"deleted": ligand_id}


@app.post("/api/projects/{project_id}/docking-jobs")
def create_docking(project_id: str, body: DockingRequest, db: Session = Depends(get_db)):
    tool = all_tools()[body.engine]
    if not tool["available"]:
        raise HTTPException(503, f"{body.engine} unavailable: {tool['error']}")
    protein = db.get(Protein, body.protein_id)
    if not protein or protein.project_id != project_id:
        raise HTTPException(404, "Protein not found")
    grid = db.get(DockingGrid, body.grid_id) if body.grid_id else None
    if body.grid_id and (not grid or grid.project_id != project_id):
        raise HTTPException(404, "Grid not found")
    if grid and grid.pocket_id:
        pocket = db.get(Pocket, grid.pocket_id)
        if not pocket or pocket.protein_id != protein.id:
            raise HTTPException(422, "Selected grid belongs to a different protein")
    receptor_source = select_docking_receptor_source(
        [
            ("prepared", resolve_data_path(protein.prepared_path)),
            ("prepared", resolve_data_path(protein.prepared_receptor_path)),
            ("original_unprepared", resolve_data_path(protein.original_path)),
        ]
    )
    if receptor_source is None:
        raise HTTPException(422, "Receptor structure file is missing")
    _, receptor_preparation = receptor_source
    if not body.ligand_ids and not body.upload_ids:
        raise HTTPException(422, "Select library ligands or upload a docking ligand file")
    ligands = (
        list(
            db.scalars(
                select(Ligand).where(
                    Ligand.id.in_(body.ligand_ids), Ligand.project_id == project_id
                )
            )
        )
        if body.ligand_ids
        else []
    )
    if len(ligands) != len(set(body.ligand_ids)):
        raise HTTPException(404, "One or more selected ligands were not found")
    selected_h2o = list(dict.fromkeys(body.selected_h2o)) if body.with_selected_h2o else []
    if body.with_selected_h2o:
        available_h2o = {
            f"{water.get('chain') or '_'}:{water.get('residue_number') or ''}"
            for water in (protein.stats or {}).get("water_molecules", [])
        }
        if not selected_h2o:
            raise HTTPException(422, "Select at least one water molecule")
        unknown_h2o = sorted(set(selected_h2o) - available_h2o)
        if unknown_h2o:
            raise HTTPException(422, f"Selected water molecule was not found: {unknown_h2o[0]}")
    uploaded_ligands: list[dict] = []
    project_root = (settings.data_dir / "projects" / project_id).resolve()
    try:
        for upload_id in dict.fromkeys(body.upload_ids):
            uploaded_ligands.extend(load_docking_upload(project_root, upload_id).get("ligands", []))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    snapshot = body.model_dump(exclude_none=True)
    snapshot["selected_h2o"] = selected_h2o
    snapshot["search_mode"] = "grid" if grid else "global_without_grid"
    snapshot["receptor_preparation"] = receptor_preparation
    snapshot["uploaded_ligands"] = uploaded_ligands
    snapshot["auto_prepare_unprepared_ligands"] = True
    item = DockingJob(
        id=unique_database_id(db, DockingJob, body.name),
        project_id=project_id,
        name=body.name,
        kind="DOCKING",
        engine=body.engine,
        engine_version=tool["version"],
        status=JobStatus.QUEUED,
        parameters=snapshot,
        ligand_ids=body.ligand_ids,
        total_count=len(body.ligand_ids) + len(uploaded_ligands),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    from .workers import run_docking

    rq = enqueue_or_503(run_docking, item.id, queue_name="docking")
    item.rq_job_id = rq.id
    db.commit()
    return serialize(item)


@app.post("/api/projects/{project_id}/docking-ligands/upload")
async def upload_docking_ligands(
    project_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    if not db.get(Project, project_id):
        raise HTTPException(404, "Project not found")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".sdf", ".csv", ".smi", ".smiles"}:
        raise HTTPException(415, "Supported docking ligand formats: .sdf, .csv, .smi, .smiles")
    uploads_root = (settings.data_dir / "projects" / project_id / "docking_uploads").resolve()
    upload_id = unique_directory_name(
        uploads_root, f"upload_{Path(file.filename or 'ligands').stem}"
    )
    root = (uploads_root / upload_id).resolve()
    root.mkdir(parents=True, exist_ok=True)
    source = root / f"source{suffix}"
    size = 0
    try:
        with source.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_mb * 1024 * 1024:
                    raise HTTPException(413, "Upload too large")
                output.write(chunk)
        return prepare_docking_upload(source, root, file.filename or source.name)
    except HTTPException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(root, ignore_errors=True)
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/jobs")
def jobs(db: Session = Depends(get_db)):
    return [
        serialize(x) for x in db.scalars(select(DockingJob).order_by(DockingJob.created_at.desc()))
    ]


def _docking_root(project_id: str, job_id: str) -> Path:
    return docking_job_root(settings.data_dir / "projects" / project_id, job_id)


def _safe_result_file(project_id: str, job_id: str, ligand_code: str, filename: str) -> Path:
    root = _docking_root(project_id, job_id)
    candidate = (root / ligand_code / filename).resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise HTTPException(404, "Docking result file not found")
    return candidate


def _scores_from_task(task_root: Path) -> list[dict]:
    scores_path = task_root / "scores.json"
    try:
        scores = json.loads(scores_path.read_text()) if scores_path.is_file() else []
    except (OSError, ValueError):
        scores = []
    try:
        output = (
            (task_root / "docking.stdout.log").read_text()
            + "\n"
            + (task_root / "docking.stderr.log").read_text()
        )
    except OSError:
        return scores
    parsed = parse_docking_scores(output)
    # Older jobs stored affinity only in scores.json. Prefer the richer table
    # parsed from the preserved engine log when it contains additional scores.
    if parsed and any(len(item) > 1 for item in parsed):
        return parsed
    return scores or parsed


def _hidden_result_ligands(project_id: str) -> set[str]:
    path = (
        settings.data_dir / "projects" / project_id / "docking" / "hidden_ligands.json"
    ).resolve()
    try:
        values = json.loads(path.read_text()) if path.is_file() else []
    except (OSError, ValueError):
        values = []
    return {str(value) for value in values}


@app.get("/api/projects/{project_id}/docking-results")
def docking_results(project_id: str, db: Session = Depends(get_db)):
    if not db.get(Project, project_id):
        raise HTTPException(404, "Project not found")
    ligands = {
        item.id: item for item in db.scalars(select(Ligand).where(Ligand.project_id == project_id))
    }
    proteins = {
        item.id: item
        for item in db.scalars(select(Protein).where(Protein.project_id == project_id))
    }
    rows: list[dict] = []
    hidden = _hidden_result_ligands(project_id)
    task_score_cache: dict[tuple[str, str], list[dict]] = {}
    for job in db.scalars(
        select(DockingJob)
        .where(DockingJob.project_id == project_id, DockingJob.kind == "DOCKING")
        .order_by(DockingJob.created_at.desc())
    ):
        root = _docking_root(project_id, job.id)
        csv_path = root / "scores.csv"
        if not csv_path.is_file():
            legacy_rows: list[dict] = []
            if root.is_dir():
                for task_root in sorted(path for path in root.iterdir() if path.is_dir()):
                    scores = _scores_from_task(task_root)
                    ligand = next(
                        (item for item in ligands.values() if item.ligand_code == task_root.name),
                        None,
                    )
                    for pose, score in enumerate(scores, start=1):
                        legacy_rows.append(
                            {
                                "ligand_code": task_root.name,
                                "ligand_id": ligand.id if ligand else "",
                                "pose": pose,
                                **score,
                            }
                        )
            legacy_rows.sort(
                key=lambda row: (
                    row.get("affinity") is None,
                    row.get("affinity") if row.get("affinity") is not None else 0.0,
                )
            )
            if legacy_rows:
                with csv_path.open("w", newline="") as handle:
                    score_fields = sorted(
                        {
                            key
                            for row in legacy_rows
                            for key in row
                            if key not in {"ligand_code", "ligand_id", "pose"}
                        }
                    )
                    if "affinity" in score_fields:
                        score_fields.remove("affinity")
                        score_fields.insert(0, "affinity")
                    writer = csv.DictWriter(
                        handle,
                        fieldnames=["rank", "ligand_code", "ligand_id", "pose", *score_fields],
                    )
                    writer.writeheader()
                    for rank, row in enumerate(legacy_rows, start=1):
                        writer.writerow({"rank": rank, **row})
            else:
                continue
        with csv_path.open(newline="") as handle:
            for item in csv.DictReader(handle):
                ligand = ligands.get(item.get("ligand_id", ""))
                ligand_code = item.get("ligand_code", "")
                if f"{job.id}:{ligand_code}" in hidden:
                    continue
                pose_file = root / ligand_code / "docked.sdf"
                if not ligand_code or not pose_file.is_file():
                    continue
                affinity = item.get("affinity")
                score_values = {
                    key: float(value)
                    for key, value in item.items()
                    if key
                    not in {"rank", "ligand_code", "ligand_id", "ligand_name", "smiles", "pose", ""}
                    and value not in (None, "")
                }
                pose_number = int(item.get("pose", "1"))
                cache_key = (str(job.id), ligand_code)
                if cache_key not in task_score_cache:
                    task_score_cache[cache_key] = _scores_from_task(root / ligand_code)
                logged_scores = task_score_cache[cache_key]
                if 0 < pose_number <= len(logged_scores):
                    score_values.update(logged_scores[pose_number - 1])
                uploaded_ligands = {
                    entry.get("ligand_code"): entry
                    for entry in (job.parameters or {}).get("uploaded_ligands", [])
                }
                uploaded = uploaded_ligands.get(ligand_code, {})
                protein_id = (job.parameters or {}).get("protein_id")
                receptor = proteins.get(protein_id)
                rows.append(
                    {
                        "key": f"{job.id}:{ligand_code}:{item.get('pose', '1')}",
                        "job_id": job.id,
                        "job_name": job.name,
                        "engine": job.engine,
                        "created_at": job.created_at,
                        "protein_id": protein_id,
                        "receptor_name": receptor.name
                        if receptor
                        else (protein_id or "Unknown receptor"),
                        "ligand_id": item.get("ligand_id") or (ligand.id if ligand else None),
                        "ligand_code": ligand_code,
                        "ligand_name": ligand.name if ligand else uploaded.get("name", ligand_code),
                        "smiles": item.get("smiles")
                        or (ligand.smiles if ligand else uploaded.get("smiles", "")),
                        "pose": pose_number,
                        "affinity": float(affinity) if affinity not in (None, "") else None,
                        "scores": score_values,
                        "sdf_path": str(pose_file.resolve()),
                        "pose_url": f"/api/projects/{project_id}/docking-results/{job.id}/{ligand_code}/poses?pose={pose_number}",
                    }
                )
    rows.sort(
        key=lambda item: (
            item["affinity"] is None,
            item["affinity"] if item["affinity"] is not None else 0.0,
            item["created_at"],
        )
    )
    for rank, item in enumerate(rows, start=1):
        item["rank"] = rank
    return rows


@app.post("/api/projects/{project_id}/docking-results/delete-ligands")
def delete_docking_result_ligands(
    project_id: str, body: DockingResultDeleteRequest, db: Session = Depends(get_db)
):
    if not db.get(Project, project_id):
        raise HTTPException(404, "Project not found")
    requested: set[str] = set()
    for selection in body.ligands:
        job = db.get(DockingJob, selection.job_id)
        if not job or job.project_id != project_id or job.kind != "DOCKING":
            raise HTTPException(404, "Docking job not found")
        # Validate that the result exists, but intentionally leave the SDF,
        # scores CSV, logs and all other job artifacts untouched.
        _safe_result_file(project_id, selection.job_id, selection.ligand_code, "docked.sdf")
        requested.add(f"{selection.job_id}:{selection.ligand_code}")
    hidden = _hidden_result_ligands(project_id) | requested
    path = (
        settings.data_dir / "projects" / project_id / "docking" / "hidden_ligands.json"
    ).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(hidden), indent=2))
    return {"deleted_from_view": len(requested), "artifacts_deleted": False}


@app.get("/api/projects/{project_id}/docking-results/{job_id}/scores.csv")
def docking_scores_csv(project_id: str, job_id: str, db: Session = Depends(get_db)):
    job = db.get(DockingJob, job_id)
    if not job or job.project_id != project_id or job.kind != "DOCKING":
        raise HTTPException(404, "Docking job not found")
    path = _docking_root(project_id, job_id) / "scores.csv"
    if not path.is_file():
        raise HTTPException(404, "Score CSV is not available yet")
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    if "ligand" not in fieldnames:
        insert_at = (
            fieldnames.index("ligand_code") + 1
            if "ligand_code" in fieldnames
            else min(2, len(fieldnames))
        )
        fieldnames.insert(insert_at, "ligand")
    if "smiles" not in fieldnames:
        insert_at = (
            fieldnames.index("ligand_id") + 1
            if "ligand_id" in fieldnames
            else min(4, len(fieldnames))
        )
        fieldnames.insert(insert_at, "smiles")
    if "sdf_path" in fieldnames:
        fieldnames.remove("sdf_path")
    fieldnames.append("sdf_path")
    project_ligands = list(db.scalars(select(Ligand).where(Ligand.project_id == project_id)))
    ligands_by_id = {item.id: item for item in project_ligands}
    ligands_by_code = {item.ligand_code: item for item in project_ligands}
    uploaded_by_code = {
        item.get("ligand_code"): item for item in (job.parameters or {}).get("uploaded_ligands", [])
    }
    uploaded_smiles: dict[str, str] = {}
    project_root = (settings.data_dir / "projects" / project_id).resolve()
    for row in rows:
        ligand = ligands_by_id.get(row.get("ligand_id", "")) or ligands_by_code.get(
            row.get("ligand_code", "")
        )
        smiles = row.get("smiles", "") or (ligand.smiles if ligand else "")
        code = row.get("ligand_code", "")
        uploaded = uploaded_by_code.get(code)
        row["ligand"] = row.get("ligand", "") or (
            ligand.name if ligand else (uploaded or {}).get("name", code)
        )
        if not smiles and uploaded:
            smiles = uploaded.get("smiles", "") or uploaded_smiles.get(code, "")
            if not smiles:
                try:
                    from rdkit import Chem

                    source = resolve_data_path(uploaded.get("sdf_path"))
                    if source and source.is_file() and project_root in source.resolve().parents:
                        molecule = next(
                            (
                                mol
                                for mol in Chem.SDMolSupplier(str(source), removeHs=False)
                                if mol is not None
                            ),
                            None,
                        )
                        smiles = (
                            Chem.MolToSmiles(Chem.RemoveHs(molecule))
                            if molecule is not None
                            else ""
                        )
                except Exception:
                    smiles = ""
                uploaded_smiles[code] = smiles
        row["smiles"] = smiles
        row["sdf_path"] = str((_docking_root(project_id, job_id) / code / "docked.sdf").resolve())
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    protein = db.get(Protein, (job.parameters or {}).get("protein_id"))
    filename = export_filename([protein.name if protein else "receptor"], "csv")
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/projects/{project_id}/docking-results/export-csv")
def export_selected_docking_csv(
    project_id: str, body: CsvExportRequest, db: Session = Depends(get_db)
):
    rows = docking_results(project_id, db)
    selections = [(item.job_id, item.ligand_code, item.pose) for item in body.poses]
    try:
        selected = select_docking_results(rows, selections)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    score_columns = sorted(
        {key for row in selected for key in (row.get("scores") or {}) if key != "affinity"}
    )
    fieldnames = [
        "rank",
        "receptor",
        "ligand",
        "ligand_code",
        "ligand_id",
        "smiles",
        "pose",
        "affinity",
        *score_columns,
        "engine",
        "job",
        "job_id",
        "sdf_path",
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in selected:
        values = {
            "rank": row["rank"],
            "receptor": row["receptor_name"],
            "ligand": row["ligand_name"],
            "ligand_code": row["ligand_code"],
            "ligand_id": row["ligand_id"] or "",
            "smiles": row.get("smiles", ""),
            "pose": row["pose"],
            "affinity": row["affinity"],
            "engine": row["engine"] or "",
            "job": row["job_name"],
            "job_id": row["job_id"],
            "sdf_path": row["sdf_path"],
        }
        values.update({key: (row.get("scores") or {}).get(key, "") for key in score_columns})
        writer.writerow(values)
    filename = export_filename([row["receptor_name"] for row in selected], "csv")
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/projects/{project_id}/docking-results/export-sdf")
def export_selected_docking_sdf(
    project_id: str, body: CsvExportRequest, db: Session = Depends(get_db)
):
    rows = docking_results(project_id, db)
    selections = [(item.job_id, item.ligand_code, item.pose) for item in body.poses]
    try:
        selected = select_docking_results(rows, selections)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    output: list[str] = []
    for row in selected:
        records = split_sdf_records(Path(row["sdf_path"]).read_text())
        pose = int(row["pose"])
        if pose > len(records):
            raise HTTPException(404, f"Pose {pose} is not present in {row['ligand_code']}")
        properties: dict[str, object] = {
            "iScreeM_Rank": row["rank"],
            "iScreeM_Receptor": row["receptor_name"],
            "iScreeM_Ligand": row["ligand_name"],
            "iScreeM_Ligand_Code": row["ligand_code"],
            "iScreeM_Ligand_ID": row["ligand_id"],
            "iScreeM_SMILES": row.get("smiles", ""),
            "iScreeM_Pose": pose,
            "iScreeM_Affinity": row["affinity"],
            "iScreeM_Engine": row["engine"],
            "iScreeM_Job": row["job_name"],
            "iScreeM_Job_ID": row["job_id"],
            "iScreeM_Source_SDF": row["sdf_path"],
        }
        properties.update(
            {f"iScreeM_Score_{key}": value for key, value in (row.get("scores") or {}).items()}
        )
        output.append(append_sdf_properties(records[pose - 1], properties))
    filename = export_filename([row["receptor_name"] for row in selected], "sdf")
    return Response(
        content="".join(output),
        media_type="chemical/x-mdl-sdfile",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/projects/{project_id}/docking-results/{job_id}/{ligand_code}/poses")
def docking_pose_file(
    project_id: str,
    job_id: str,
    ligand_code: str,
    pose: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
):
    job = db.get(DockingJob, job_id)
    if not job or job.project_id != project_id or job.kind != "DOCKING":
        raise HTTPException(404, "Docking job not found")
    path = _safe_result_file(project_id, job_id, ligand_code, "docked.sdf")
    if pose is not None:
        records = split_sdf_records(path.read_text())
        if pose > len(records):
            raise HTTPException(404, f"Pose {pose} is not present in {ligand_code}")
        return Response(
            content=f"{records[pose - 1]}\n$$$$\n",
            media_type="chemical/x-mdl-sdfile",
            headers={"Content-Disposition": f'inline; filename="{ligand_code}-pose-{pose}.sdf"'},
        )
    return FileResponse(
        path, media_type="chemical/x-mdl-sdfile", filename=f"{ligand_code}-docked.sdf"
    )


@app.post("/api/projects/{project_id}/docking-results/export-pse")
def export_pse(project_id: str, body: PseExportRequest, db: Session = Depends(get_db)):
    protein = db.get(Protein, body.protein_id)
    if not protein or protein.project_id != project_id:
        raise HTTPException(404, "Protein not found")
    pymol = shutil.which("pymol") or shutil.which("pymol2")
    if not pymol:
        raise HTTPException(
            503, "PyMOL is required for .pse export; install PyMOL and restart iScreeM"
        )
    receptor = resolve_data_path(
        protein.prepared_path or protein.prepared_receptor_path or protein.original_path
    )
    if receptor is None or not receptor.is_file():
        raise HTTPException(404, "Receptor structure file not found")
    export_dir = (settings.data_dir / "projects" / project_id / "docking" / "exports").resolve()
    export_dir.mkdir(parents=True, exist_ok=True)
    export_name = export_filename([protein.name], "pse")
    output = export_dir / export_name
    commands = [f"load {json.dumps(str(receptor))}, receptor"]
    for index, selection in enumerate(body.poses, start=1):
        sdf = _safe_result_file(project_id, selection.job_id, selection.ligand_code, "docked.sdf")
        raw = f"raw_{index}"
        commands.append(f"load {json.dumps(str(sdf))}, {raw}")
        commands.append(f"create pose_{index}, {raw}, state={selection.pose}")
        commands.append(f"delete {raw}")
    commands.extend([f"save {json.dumps(str(output))}, all", "quit"])
    script = "\n".join(commands) + "\n"
    result = subprocess.run(
        [pymol, "-cq"],
        input=script,
        text=True,
        capture_output=True,
        cwd=export_dir,
        timeout=120,
        check=False,
    )
    if result.returncode or not output.is_file():
        output.unlink(missing_ok=True)
        raise HTTPException(500, f"PyMOL .pse export failed: {result.stderr[-1000:]}")
    return {
        "download_url": f"/api/projects/{project_id}/docking-results/exports/{export_name}",
        "filename": export_name,
    }


@app.get("/api/projects/{project_id}/docking-results/exports/{filename}")
def download_pse(project_id: str, filename: str, db: Session = Depends(get_db)):
    if (
        not db.get(Project, project_id)
        or Path(filename).name != filename
        or not filename.endswith(".pse")
    ):
        raise HTTPException(404, "Export not found")
    root = (settings.data_dir / "projects" / project_id / "docking" / "exports").resolve()
    path = (root / filename).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "Export not found")
    return FileResponse(path, media_type="application/octet-stream", filename=filename)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, db: Session = Depends(get_db)):
    item = db.get(DockingJob, job_id)
    if not item:
        raise HTTPException(404, "Job not found")
    if item.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
        raise HTTPException(409, "Job is already terminal")
    cancel_queued_job(item)
    db.commit()
    return serialize(item)


@app.get("/api/jobs/events")
async def job_events(request: Request):
    async def stream():
        while not await request.is_disconnected():
            from .db import SessionLocal

            with SessionLocal() as db:
                payload = [
                    serialize(x)
                    for x in db.scalars(select(DockingJob).order_by(DockingJob.created_at.desc()))
                ]
            yield f"data: {json.dumps(payload, default=str)}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(stream(), media_type="text/event-stream")
