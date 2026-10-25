import re

_SDF_DELIMITER = re.compile(r"(?m)^\$\$\$\$[ \t]*(?:\r?\n|$)")


def split_sdf_records(text: str) -> list[str]:
    """Split SDF records without shifting a blank molecule header.

    The record delimiter's line ending belongs to the delimiter.  Leaving it
    at the start of the next record moves the V2000 counts line from line four
    to line five when the molecule's three header lines are intentionally
    blank, causing parsers such as 3Dmol.js to return an empty model.
    """
    return [record.rstrip() for record in _SDF_DELIMITER.split(text) if record.strip()]
