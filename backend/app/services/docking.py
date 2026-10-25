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


class DockingAdapter(ABC):
    executable: str

    @abstractmethod
    def build_args(self, item: DockingInput) -> list[str]: ...

    def common_args(self, item: DockingInput) -> list[str]:
        args = [
            self.executable, "--receptor", str(item.receptor), "--ligand", str(item.ligand),
            "--out", str(item.output), "--center_x", str(item.center["x"]),
            "--center_y", str(item.center["y"]), "--center_z", str(item.center["z"]),
            "--size_x", str(item.size["x"]), "--size_y", str(item.size["y"]),
            "--size_z", str(item.size["z"]),
        ]
        mapping = {"exhaustiveness": "--exhaustiveness", "num_modes": "--num_modes", "cpu": "--cpu", "seed": "--seed", "min_rmsd_filter": "--min_rmsd_filter"}
        for key, flag in mapping.items():
            value = item.parameters.get(key)
            if value is not None:
                args.extend([flag, str(value)])
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
