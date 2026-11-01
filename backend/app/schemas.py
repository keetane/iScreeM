from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class Axis3(BaseModel):
    x: float
    y: float
    z: float


class GridCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    pocket_id: str | None = None
    center: Axis3 | None = None
    size: Axis3 | None = None

    @field_validator("size")
    @classmethod
    def positive_size(cls, value: Axis3 | None):
        if value and min(value.x, value.y, value.z) <= 0:
            raise ValueError("Grid size must be positive")
        return value


class GridPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    center: Axis3 | None = None
    size: Axis3 | None = None

    @field_validator("size")
    @classmethod
    def positive_size(cls, value: Axis3 | None):
        if value and min(value.x, value.y, value.z) <= 0:
            raise ValueError("Grid size must be positive")
        return value


class SmilesLigand(BaseModel):
    smiles: str = Field(min_length=1)
    name: str | None = None


class LigandImport(BaseModel):
    ligands: list[SmilesLigand] = Field(min_length=1, max_length=5000)


class LigandQueryImport(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=100)


class ResidueDeleteRequest(BaseModel):
    residue_keys: list[str] = Field(min_length=1, max_length=10000)


class LigandPatch(BaseModel):
    ligand_code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    smiles: str | None = Field(default=None, min_length=1)


class LigandPrepareRequest(BaseModel):
    ligand_ids: list[str] = Field(min_length=1)
    random_seed: int = 61453
    force_field: Literal["MMFF94", "MMFF94s"] = "MMFF94s"


class LigandDeleteRequest(BaseModel):
    ligand_ids: list[str] = Field(min_length=1)


class AutoboxLigandSelector(BaseModel):
    name: str = Field(min_length=1, max_length=16)
    chain: str = Field(default="_", min_length=1, max_length=16)
    residue_number: str = Field(min_length=1, max_length=32)


class DockingRequest(BaseModel):
    name: str = Field(default="Docking run", min_length=1, max_length=160)
    comment: str = Field(default="", max_length=120)
    protein_id: str
    grid_id: str | None = None
    autobox_ligand: AutoboxLigandSelector | None = None
    ligand_ids: list[str] = Field(default_factory=list)
    upload_ids: list[str] = Field(default_factory=list)
    with_selected_h2o: bool = False
    selected_h2o: list[str] = Field(default_factory=list, max_length=10000)
    engine: Literal["smina", "gnina"]
    exhaustiveness: int | None = Field(default=None, ge=1, le=1024)
    num_modes: int = Field(default=3, ge=1, le=100)
    cpu: int | None = Field(default=None, ge=1, le=256)
    seed: int | None = None
    min_rmsd_filter: float | None = Field(default=None, gt=0)
    local_only: bool = False
    cnn_scoring: Literal["none", "rescore", "refinement", "all"] | None = None
    no_gpu: bool = False


class DockingResultLigand(BaseModel):
    job_id: str
    ligand_code: str = Field(min_length=1, max_length=160)


class DockingResultDeleteRequest(BaseModel):
    ligands: list[DockingResultLigand] = Field(min_length=1, max_length=5000)


class DockingResultJobVisibilityRequest(BaseModel):
    visible: bool


class PoseSelection(BaseModel):
    job_id: str
    ligand_code: str
    pose: int = Field(ge=1)


class PseExportRequest(BaseModel):
    protein_id: str
    poses: list[PoseSelection] = Field(min_length=1, max_length=500)


class CsvExportRequest(BaseModel):
    poses: list[PoseSelection] = Field(min_length=1, max_length=5000)


class ProjectOut(BaseModel):
    id: str
    name: str
    created_at: datetime
    model_config = {"from_attributes": True}

