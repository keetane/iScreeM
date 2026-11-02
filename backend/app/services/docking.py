from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ..config import settings


@dataclass(frozen=True)
class DockingInput:
    receptor: Path
    ligand: Path
    output: Path
    center: dict
    size: dict
    parameters: dict
    autobox_ligand: Path | None = None


class DockingAdapter(ABC):
    executable: str

    @abstractmethod
    def build_args(self, item: DockingInput) -> list[str]: ...

    def common_args(self, item: DockingInput) -> list[str]:
        args = [
            self.executable, "--receptor", str(item.receptor), "--ligand", str(item.ligand),
            "--out", str(item.output),
        ]
        if item.autobox_ligand is not None:
            args.extend(["--autobox_ligand", str(item.autobox_ligand)])
        else:
            args.extend([
                "--center_x", str(item.center["x"]),
                "--center_y", str(item.center["y"]), "--center_z", str(item.center["z"]),
                "--size_x", str(item.size["x"]), "--size_y", str(item.size["y"]),
                "--size_z", str(item.size["z"]),
            ])
        mapping = {"exhaustiveness": "--exhaustiveness", "num_modes": "--num_modes", "cpu": "--cpu", "seed": "--seed", "min_rmsd_filter": "--min_rmsd_filter"}
        for key, flag in mapping.items():
            value = item.parameters.get(key)
            if value is not None:
                args.extend([flag, str(value)])
        if item.parameters.get("local_only"):
            args.append("--local_only")
        return args


class SminaAdapter(DockingAdapter):
    executable = settings.smina_command
    def build_args(self, item: DockingInput) -> list[str]:
        return self.common_args(item)


class GninaAdapter(DockingAdapter):
    executable = settings.gnina_command
    def build_args(self, item: DockingInput) -> list[str]:
        args = self.common_args(item)
        if item.parameters.get("cnn_scoring") is not None:
            args.extend(["--cnn_scoring", item.parameters["cnn_scoring"]])
        if item.parameters.get("no_gpu"):
            args.append("--no_gpu")
        return args


ADAPTERS = {"smina": SminaAdapter(), "gnina": GninaAdapter()}

# Only an SDF can carry a docked pose. Every other input is SMILES, and the
# 3D coordinates generated for it sit at the origin rather than in the pocket.
POSE_BEARING_FORMATS = {"sdf"}


def local_only_input_error(inputs: list[tuple[str, str]]) -> str | None:
    """Reject --local_only runs whose ligands cannot supply a pose.

    --local_only minimises the supplied coordinates without ever moving the
    ligand into the search box, so SMILES input is scored hundreds of angstroms
    away from the receptor and every affinity comes back as 0.
    """
    for name, source_format in inputs:
        if (source_format or "").lower() in POSE_BEARING_FORMATS:
            continue
        return (
            f'Local only minimises the pose you supply, but "{name}" comes from SMILES '
            "and has no docked pose, so it would be scored outside the binding site. "
            "Upload the docked poses as SDF, or clear Local only and set Number of modes to 1."
        )
    return None
