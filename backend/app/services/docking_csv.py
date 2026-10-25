from collections.abc import Iterable


def select_docking_results(
    rows: list[dict], selections: Iterable[tuple[str, str, int]]
) -> list[dict]:
    """Return only explicitly selected result rows, preserving score order."""
    wanted = set(selections)
    selected = [
        row
        for row in rows
        if (row["job_id"], row["ligand_code"], int(row["pose"])) in wanted
    ]
    found = {
        (row["job_id"], row["ligand_code"], int(row["pose"])) for row in selected
    }
    if missing := wanted - found:
        raise ValueError(f"Selected docking results are unavailable: {sorted(missing)!r}")
    return selected
