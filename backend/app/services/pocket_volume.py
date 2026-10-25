from __future__ import annotations

import gzip
from collections import deque
from pathlib import Path

VOXEL_SPACING_ANGSTROM = 0.5
# P2Rank has already expanded each predicted pocket to neighboring SAS points.
# A ligand-atom-sized 1.5 Å shell avoids expanding that surface a second time.
POCKET_POINT_RADIUS_ANGSTROM = 1.5
ESTIMATED_MW_PER_CUBIC_ANGSTROM = 0.45
MAX_GRID_VOXELS = 4_000_000
VOLUME_METHOD = f"p2rank-voxel-{VOXEL_SPACING_ANGSTROM:g}A-r{POCKET_POINT_RADIUS_ANGSTROM:g}A"

# Bondi-style van der Waals radii. Unknown heavy atoms use carbon's radius.
VDW_RADII = {
    "C": 1.70,
    "N": 1.55,
    "O": 1.52,
    "F": 1.47,
    "P": 1.80,
    "S": 1.80,
    "CL": 1.75,
    "BR": 1.85,
    "I": 1.98,
    "B": 1.92,
    "SI": 2.10,
    "SE": 1.90,
}


def read_p2rank_points(path: Path) -> dict[int, list[list[float]]]:
    """Read P2Rank visualization points grouped by predicted pocket rank."""
    opener = gzip.open if path.suffix.lower() == ".gz" else open
    grouped: dict[int, list[list[float]]] = {}
    with opener(path, "rt", errors="replace") as handle:
        for line in handle:
            if line[:6].strip().upper() != "HETATM" or line[17:20].strip() != "STP":
                continue
            try:
                rank = int(line[22:26].strip())
                xyz = [float(line[30:38]), float(line[38:46]), float(line[46:54])]
            except ValueError:
                continue
            if rank > 0:
                grouped.setdefault(rank, []).append(xyz)
    return grouped


def _receptor_heavy_atoms(path: Path) -> tuple[list[list[float]], list[float]]:
    coordinates: list[list[float]] = []
    radii: list[float] = []
    if path.suffix.lower() in {".cif", ".mmcif"}:
        try:
            import gemmi
        except ImportError as exc:
            raise ValueError("Pocket volume for CIF/mmCIF requires gemmi") from exc
        structure = gemmi.read_structure(str(path))
        first_model = next(iter(structure), None)
        if first_model is None:
            return coordinates, radii
        for chain in first_model:
            for residue in chain:
                entity_name = residue.entity_type.name if residue.entity_type else "Unknown"
                if entity_name not in {"Polymer", "Unknown"}:
                    continue
                for atom in residue:
                    element = atom.element.name.upper()
                    if element == "H":
                        continue
                    coordinates.append([atom.pos.x, atom.pos.y, atom.pos.z])
                    radii.append(VDW_RADII.get(element, VDW_RADII["C"]))
        return coordinates, radii

    with path.open(errors="replace") as handle:
        for line in handle:
            if line[:6].strip().upper() != "ATOM":
                continue
            element = (line[76:78].strip() or line[12:16].strip()[:1]).upper()
            if element == "H":
                continue
            try:
                coordinates.append(
                    [float(line[30:38]), float(line[38:46]), float(line[46:54])]
                )
            except ValueError:
                continue
            radii.append(VDW_RADII.get(element, VDW_RADII["C"]))
    return coordinates, radii


def _largest_component(mask):
    """Keep the largest 6-connected voxel component."""
    import numpy as np

    shape = mask.shape
    visited = np.zeros(shape, dtype=bool)
    best: list[tuple[int, int, int]] = []
    for start in zip(*np.nonzero(mask & ~visited)):
        if visited[start]:
            continue
        component: list[tuple[int, int, int]] = []
        queue = deque([start])
        visited[start] = True
        while queue:
            point = queue.popleft()
            component.append(point)
            for axis in range(3):
                for step in (-1, 1):
                    neighbor = list(point)
                    neighbor[axis] += step
                    neighbor_tuple = tuple(neighbor)
                    if (
                        0 <= neighbor[axis] < shape[axis]
                        and mask[neighbor_tuple]
                        and not visited[neighbor_tuple]
                    ):
                        visited[neighbor_tuple] = True
                        queue.append(neighbor_tuple)
        if len(component) > len(best):
            best = component
    result = np.zeros(shape, dtype=bool)
    if best:
        indices = tuple(np.asarray(best).T)
        result[indices] = True
    return result


def calculate_pocket_volume(
    receptor_path: Path,
    pocket_points: list[list[float]],
    *,
    spacing: float = VOXEL_SPACING_ANGSTROM,
    point_radius: float = POCKET_POINT_RADIUS_ANGSTROM,
) -> float:
    """Estimate localized pocket void volume in cubic angstroms.

    Candidate voxels are the union of spheres around P2Rank pocket points.
    Voxels overlapping receptor heavy-atom van der Waals spheres are removed,
    then only the largest connected component is retained.
    """
    if not pocket_points:
        return 0.0
    try:
        import numpy as np
        from scipy.spatial import cKDTree
    except ImportError as exc:
        raise ValueError("Pocket volume calculation requires NumPy and SciPy") from exc

    points = np.asarray(pocket_points, dtype=float)
    atoms_list, radii_list = _receptor_heavy_atoms(receptor_path)
    atoms = np.asarray(atoms_list, dtype=float)
    atom_radii = np.asarray(radii_list, dtype=float)
    if atoms.size == 0:
        raise ValueError("Receptor contains no heavy polymer atoms")

    minimum = np.floor((points.min(axis=0) - point_radius) / spacing) * spacing
    maximum = np.ceil((points.max(axis=0) + point_radius) / spacing) * spacing
    axes = [np.arange(minimum[i], maximum[i] + spacing * 0.5, spacing) for i in range(3)]
    voxel_count = int(len(axes[0]) * len(axes[1]) * len(axes[2]))
    if voxel_count > MAX_GRID_VOXELS:
        raise ValueError(f"Pocket voxel grid is too large ({voxel_count:,} voxels)")

    mesh = np.meshgrid(*axes, indexing="ij")
    grid = np.column_stack([axis.ravel() for axis in mesh])
    near_pocket = cKDTree(points).query(grid, distance_upper_bound=point_radius)[0]
    candidate_indices = np.flatnonzero(np.isfinite(near_pocket))
    candidate_points = grid[candidate_indices]

    atom_tree = cKDTree(atoms)
    neighbors = atom_tree.query_ball_point(candidate_points, r=float(atom_radii.max()))
    occupied = np.zeros(len(candidate_points), dtype=bool)
    for index, atom_indices in enumerate(neighbors):
        if not atom_indices:
            continue
        offsets = atoms[atom_indices] - candidate_points[index]
        squared_distances = np.einsum("ij,ij->i", offsets, offsets)
        occupied[index] = bool(
            np.any(squared_distances <= atom_radii[atom_indices] ** 2)
        )

    mask = np.zeros(voxel_count, dtype=bool)
    mask[candidate_indices[~occupied]] = True
    mask = _largest_component(mask.reshape(tuple(len(axis) for axis in axes)))
    return round(float(mask.sum()) * spacing**3, 1)


def pocket_metrics(receptor_path: Path, points: list[list[float]]) -> dict[str, float | str]:
    volume = calculate_pocket_volume(receptor_path, points)
    return {
        "volume": volume,
        "estimated_max_mw": round(volume * ESTIMATED_MW_PER_CUBIC_ANGSTROM, 1),
        "volume_method": VOLUME_METHOD,
    }


def find_p2rank_points(root: Path) -> Path | None:
    direct = sorted((root / "visualizations" / "data").glob("*_points.pdb.gz"))
    if direct:
        return direct[0]
    candidates = sorted(root.rglob("*_points.pdb.gz")) if root.is_dir() else []
    return candidates[0] if candidates else None
