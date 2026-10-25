GRID_PADDING_ANGSTROM = 4.0
MIN_GRID_SIZE_ANGSTROM = 18.0


def grid_from_coordinates(points: list[list[float]], fallback_center: dict | None = None):
    if not points:
        if not fallback_center:
            raise ValueError("No coordinates available to calculate grid")
        return dict(fallback_center), {axis: MIN_GRID_SIZE_ANGSTROM for axis in "xyz"}, True
    mins = [min(row[i] for row in points) for i in range(3)]
    maxs = [max(row[i] for row in points) for i in range(3)]
    center = {axis: round((mins[i] + maxs[i]) / 2, 3) for i, axis in enumerate("xyz")}
    size = {
        axis: round(max(MIN_GRID_SIZE_ANGSTROM, maxs[i] - mins[i] + 2 * GRID_PADDING_ANGSTROM), 3)
        for i, axis in enumerate("xyz")
    }
    return center, size, False

