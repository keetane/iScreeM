import csv
import json
import re
from pathlib import Path


def _column(value: str) -> str:
    return re.sub(r"[\s_\-().]", "", value.casefold())


def _smiles_rows(path: Path):
    if path.suffix.lower() == ".csv":
        with path.open(newline="", errors="replace") as handle:
            reader = csv.reader(handle)
            first = next(reader, None)
            if first is None:
                return
            header = [_column(value) for value in first]
            smiles_index = next(
                (i for i, value in enumerate(header) if value == "smiles" or "smiles" in value), -1
            )
            if smiles_index < 0:
                raise ValueError("CSV must contain a SMILES column")
            names = {
                "ligand",
                "ligandname",
                "name",
                "mole",
                "molecule",
                "moleculename",
                "compound",
                "compoundname",
                "title",
                "id",
                "code",
            }
            name_index = next(
                (
                    i
                    for i, value in enumerate(header)
                    if value in names or "ligand" in value or "molecule" in value
                ),
                -1,
            )
            for index, row in enumerate(reader, 1):
                if smiles_index >= len(row) or not row[smiles_index].strip():
                    continue
                yield (
                    row[smiles_index].strip(),
                    row[name_index].strip()
                    if 0 <= name_index < len(row)
                    else f"Ligand {index}",
                )
            return
    with path.open(errors="replace") as handle:
        for index, line in enumerate(handle, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            fields = stripped.split()
            yield fields[0], " ".join(fields[1:]) or f"Ligand {index}"


def _sdf_records(source: Path):
    with source.open("rb") as handle:
        start = handle.tell()
        first_line = handle.readline()
        while first_line:
            while True:
                line = handle.readline()
                if not line or line.strip() == b"$$$$":
                    break
            end = handle.tell()
            if end > start and (first_line.strip() or end - start > 4):
                yield {
                    "record_offset": start,
                    "record_length": end - start,
                    "record_name": first_line.decode("utf-8", errors="replace").strip(),
                }
            if not line:
                break
            start = handle.tell()
            first_line = handle.readline()


def _ligand_code(upload_id: str, index: int) -> str:
    safe_upload_id = re.sub(r"[^\w.-]+", "_", upload_id, flags=re.UNICODE).strip("._")
    return f"upload_{safe_upload_id[:120]}_{index:06d}"


def iter_docking_upload_ligands(manifest: dict):
    """Yield one uploaded ligand at a time without retaining the library in memory."""
    source = Path(manifest.get("source_path", "")).resolve()
    source_format = str(manifest.get("source_format") or source.suffix.lstrip(".")).lower()
    upload_id = str(manifest.get("upload_id") or source.parent.name)

    # Uploads created before streaming manifests stored every ligand entry.
    legacy_entries = manifest.get("ligands")
    if legacy_entries:
        for entry in legacy_entries:
            yield {
                **entry,
                "source_path": str(source) if manifest.get("source_path") else None,
                "source_format": source_format,
                "upload_id": upload_id,
            }
        return

    if source_format == "sdf":
        for index, item in enumerate(_sdf_records(source), 1):
            yield {
                "ligand_code": _ligand_code(upload_id, index),
                "name": item["record_name"] or f"Ligand {index}",
                "smiles": "",
                "source_path": str(source),
                "source_format": source_format,
                "upload_id": upload_id,
                "record_offset": item["record_offset"],
                "record_length": item["record_length"],
            }
        return
    if source_format in {"csv", "smi", "smiles"}:
        for index, (smiles, name) in enumerate(_smiles_rows(source), 1):
            yield {
                "ligand_code": _ligand_code(upload_id, index),
                "name": str(name or "").strip() or f"Ligand {index}",
                "smiles": smiles,
                "source_path": str(source),
                "source_format": source_format,
                "upload_id": upload_id,
            }
        return
    raise ValueError("Supported docking ligand formats: .sdf, .csv, .smi, .smiles")


def prepare_docking_upload(source: Path, destination_root: Path, original_name: str) -> dict:
    """Validate and count an upload without retaining per-ligand metadata."""
    suffix = source.suffix.lower()
    if suffix == ".sdf":
        ligand_count = sum(1 for _ in _sdf_records(source))
    elif suffix in {".csv", ".smi", ".smiles"}:
        ligand_count = sum(1 for _ in _smiles_rows(source))
    else:
        raise ValueError("Supported docking ligand formats: .sdf, .csv, .smi, .smiles")
    if not ligand_count:
        raise ValueError("No ligand records were found")

    destination_root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "manifest_version": 2,
        "streaming": True,
        "upload_id": destination_root.name,
        "original_name": original_name,
        "source_path": str(source.resolve()),
        "source_format": suffix.lstrip("."),
        "ligand_count": ligand_count,
    }
    (destination_root / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def load_docking_upload(project_root: Path, upload_id: str) -> dict:
    if not re.fullmatch(r"[\w.-]+", upload_id, flags=re.UNICODE) or upload_id in {".", ".."}:
        raise ValueError("Invalid docking upload id")
    root = (project_root / "docking_uploads" / upload_id).resolve()
    allowed = (project_root / "docking_uploads").resolve()
    if allowed not in root.parents:
        raise ValueError("Invalid docking upload path")
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Docking ligand upload was not found")
    manifest = json.loads(manifest_path.read_text())
    source_path_value = manifest.get("source_path")
    if source_path_value:
        source_path = Path(source_path_value).resolve()
        if root not in source_path.parents or not source_path.is_file():
            raise ValueError("Docking ligand upload source is missing")
    else:
        # Backward compatibility for uploads created before deferred preparation.
        for ligand in manifest.get("ligands", []):
            path = Path(ligand.get("sdf_path", "")).resolve()
            if root not in path.parents or not path.is_file():
                raise ValueError("Docking ligand upload is incomplete")
    return manifest


def materialize_sdf_record(entry: dict, destination: Path) -> Path:
    """Copy one indexed SDF record when its docking task starts."""
    source = Path(entry.get("source_path", "")).resolve()
    offset = int(entry.get("record_offset", -1))
    length = int(entry.get("record_length", 0))
    if not source.is_file() or offset < 0 or length <= 0:
        raise ValueError("Deferred SDF ligand source is invalid")
    if offset + length > source.stat().st_size:
        raise ValueError("Deferred SDF ligand record is outside the uploaded file")
    with source.open("rb") as handle:
        handle.seek(offset)
        record = handle.read(length)
    if not record.strip():
        raise ValueError("Deferred SDF ligand record is empty")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(record)
    return destination
