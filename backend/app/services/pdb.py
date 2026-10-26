from collections import defaultdict
from math import dist
from pathlib import Path

METALS = {"LI", "NA", "K", "MG", "CA", "MN", "FE", "CO", "NI", "CU", "ZN", "CD", "HG"}
WATERS = {"HOH", "WAT", "DOD"}
AMINO_ACIDS = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    # Common covalently modified amino acids emitted as HETATM by PDBFixer.
    # NEP is phosphohistidine in 6ETJ and is still one polymer residue.
    "MSE": "M", "NEP": "H", "SEP": "S", "TPO": "T", "PTR": "Y",
    "HYP": "P", "CSO": "C", "CSD": "C", "CME": "C", "MLY": "K",
}
POLYMER_HETERO = set(AMINO_ACIDS) - {
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS",
    "ILE", "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP",
    "TYR", "VAL",
}


def _sequence_entry(name: str, number: str) -> dict:
    return {"name": name, "number": str(number), "one_letter": AMINO_ACIDS.get(name.upper(), "X")}


def _sequences(chains: dict[str, dict[str, str]]) -> list[dict]:
    return [{"id": key, "sequence": "".join(AMINO_ACIDS.get(name.upper(), "X") for name in residues.values()), "residues": [_sequence_entry(name, number) for number, name in residues.items()]} for key, residues in sorted(chains.items())]


def parse_structure(path: Path) -> dict:
    """Parse PDB, mmCIF/CIF and return a common structure summary."""
    if path.suffix.lower() in {".cif", ".mmcif"}:
        try:
            import gemmi
        except ImportError as exc:
            raise ValueError("CIF/mmCIF support requires the gemmi package") from exc
        try:
            structure = gemmi.read_structure(str(path))
        except Exception as exc:
            raise ValueError(f"Could not parse CIF/mmCIF: {exc}") from exc
        chains: dict[str, set[tuple[str, str]]] = defaultdict(set)
        sequence_chains: dict[str, dict[str, str]] = defaultdict(dict)
        hetero: dict[tuple[str, str, str], int] = defaultdict(int)
        waters: dict[tuple[str, str, str], int] = defaultdict(int)
        metals: dict[str, int] = defaultdict(int)
        water_count = 0
        atom_count = 0
        altlocs: set[str] = set()
        for model in structure:
            for chain in model:
                chain_id = chain.name or "_"
                for residue in chain:
                    residue_name = residue.name.strip()
                    residue_key = (residue_name, str(residue.seqid.num))
                    is_water = residue_name.upper() in WATERS
                    # Many valid mmCIF files omit an entity table. Treat Unknown
                    # non-water residues as polymer so chain/residue counts remain useful.
                    entity_name = residue.entity_type.name if residue.entity_type else "Unknown"
                    is_polymer = not is_water and (
                        entity_name in {"Polymer", "Unknown"} or residue_name.upper() in POLYMER_HETERO
                    )
                    if is_polymer:
                        chains[chain_id].add(residue_key)
                        sequence_chains[chain_id].setdefault(str(residue.seqid.num), residue_name)
                    for atom in residue:
                        atom_count += 1
                        if atom.altloc not in ("\x00", " "):
                            altlocs.add(str(atom.altloc))
                        element = atom.element.name.upper()
                        if is_water:
                            water_count += 1
                            waters[(residue_name, chain_id, str(residue.seqid.num))] += 1
                        elif element in METALS:
                            metals[element] += 1
                        elif not is_polymer:
                            hetero[(residue_name, chain_id, str(residue.seqid.num))] += 1
        if not atom_count:
            raise ValueError("No atoms found in CIF/mmCIF structure")
        warnings = [f"Alternative conformations detected: {', '.join(sorted(altlocs))}"] if altlocs else []
        return {
            "format": "cif",
            "atom_count": atom_count,
            "residue_count": sum(map(len, chains.values())),
            "chains": [{"id": key, "residue_count": len(value)} for key, value in sorted(chains.items())],
            "sequences": _sequences(sequence_chains),
            "hetero_molecules": [
                {"name": name, "chain": chain, "residue_number": number, "atom_count": count}
                for (name, chain, number), count in sorted(hetero.items())
            ],
            "water_atom_count": water_count,
            "water_molecules": [
                {"name": name, "chain": chain, "residue_number": number, "atom_count": count}
                for (name, chain, number), count in sorted(waters.items())
            ],
            "metals": [{"element": key, "count": value} for key, value in sorted(metals.items())],
            "warnings": warnings,
        }
    stats = parse_pdb(path)
    stats["format"] = "pdb"
    return stats


def parse_pdb(path: Path) -> dict:
    chains: dict[str, set[tuple[str, str]]] = defaultdict(set)
    sequence_chains: dict[str, dict[str, str]] = defaultdict(dict)
    hetero: dict[tuple[str, str, str], int] = defaultdict(int)
    waters: dict[tuple[str, str, str], int] = defaultdict(int)
    metals: dict[str, int] = defaultdict(int)
    water_count = 0
    atom_count = 0
    warnings: list[str] = []
    altlocs: set[str] = set()
    with path.open("r", errors="replace") as handle:
        for line in handle:
            record = line[:6].strip()
            if record not in {"ATOM", "HETATM"}:
                continue
            atom_count += 1
            altloc = line[16:17].strip()
            if altloc:
                altlocs.add(altloc)
            residue = line[17:20].strip()
            chain = line[21:22].strip() or "_"
            seq = line[22:27].strip()
            element = line[76:78].strip().upper() or line[12:14].strip().upper()
            if record == "ATOM":
                chains[chain].add((residue, seq))
                sequence_chains[chain].setdefault(seq, residue)
            elif residue in WATERS:
                water_count += 1
                waters[(residue, chain, seq)] += 1
            elif residue in POLYMER_HETERO:
                # Modified amino acids such as NEP are HETATM records in
                # PDBFixer/PDB2PQR output, but belong to the protein chain.
                chains[chain].add((residue, seq))
                sequence_chains[chain].setdefault(seq, residue)
            elif element in METALS and len(residue) <= 3:
                metals[element] += 1
            else:
                hetero[(residue, chain, seq)] += 1
    if altlocs:
        warnings.append(f"Alternative conformations detected: {', '.join(sorted(altlocs))}")
    if not atom_count:
        raise ValueError("No ATOM or HETATM records found")
    return {
        "format": "pdb",
        "atom_count": atom_count,
        "residue_count": sum(map(len, chains.values())),
        "chains": [{"id": key, "residue_count": len(value)} for key, value in sorted(chains.items())],
        "sequences": _sequences(sequence_chains),
        "hetero_molecules": [
            {"name": name, "chain": chain, "residue_number": number, "atom_count": count}
            for (name, chain, number), count in sorted(hetero.items())
        ],
        "water_atom_count": water_count,
        "water_molecules": [
            {"name": name, "chain": chain, "residue_number": number, "atom_count": count}
            for (name, chain, number), count in sorted(waters.items())
        ],
        "metals": [{"element": key, "count": value} for key, value in sorted(metals.items())],
        "warnings": warnings,
    }


def select_docking_receptor_source(
    candidates: list[tuple[str, Path | None]],
) -> tuple[Path, str] | None:
    """Select the first existing receptor, preferring prepared candidates."""
    for preparation, path in candidates:
        if path is not None and path.is_file():
            return path, preparation
    return None


def write_docking_receptor(source: Path, destination: Path, keep_interacting_waters: bool = False, contact_distance: float = 3.6) -> dict[str, int]:
    """Write the receptor used for docking without organic HETATM records.

    When requested, complete water residues having a polar contact to a
    receptor N/O/S atom are retained.  Other HETATM and all CONECT records are
    discarded so dangling bonds cannot leak into the docking input.
    """
    if source.suffix.lower() in {".cif", ".mmcif"}:
        try:
            import gemmi
        except ImportError as exc:
            raise ValueError("CIF/mmCIF docking requires the gemmi package") from exc
        try:
            pdb_text = gemmi.read_structure(str(source)).make_pdb_string()
        except (RuntimeError, ValueError) as exc:
            raise ValueError(f"Could not convert CIF/mmCIF receptor to PDB: {exc}") from exc
        lines = pdb_text.splitlines(keepends=True)
    else:
        lines = source.read_text(errors="replace").splitlines(keepends=True)
    receptor_polar: list[tuple[float, float, float]] = []
    waters: dict[tuple[str, str, str, str], list[tuple[str, tuple[float, float, float], str]]] = {}
    for line in lines:
        record = line[:6].strip().upper()
        if record not in {"ATOM", "HETATM"}:
            continue
        try:
            xyz = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
        except ValueError:
            continue
        element = (line[76:78].strip() or line[12:16].strip()[:1]).upper()
        if record == "ATOM" and element in {"N", "O", "S"}:
            receptor_polar.append(xyz)
        elif record == "HETATM" and line[17:20].strip().upper() in {"HOH", "WAT", "DOD"}:
            key = (line[17:20].strip(), line[21:22].strip(), line[22:26].strip(), line[26:27].strip())
            waters.setdefault(key, []).append((line, xyz, element))
    retained = set()
    if keep_interacting_waters:
        for key, atoms in waters.items():
            if any(element in {"N", "O", "S"} and any(dist(xyz, receptor) <= contact_distance for receptor in receptor_polar) for _, xyz, element in atoms):
                retained.add(key)

    kept: list[str] = []
    atom_count = water_atom_count = 0
    for line in lines:
        record = line[:6].strip().upper()
        if record == "CONECT":
            continue
        if record == "ATOM":
            atom_count += 1
            kept.append(line)
            continue
        if record == "HETATM":
            key = (line[17:20].strip(), line[21:22].strip(), line[22:26].strip(), line[26:27].strip())
            if key in retained:
                water_atom_count += 1
                kept.append(line)
            continue
        kept.append(line)
    if not atom_count:
        raise ValueError("Receptor source contains no ATOM records after removing HETATM")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(kept))
    return {"receptor_atom_count": atom_count, "water_atom_count": water_atom_count, "water_residue_count": len(retained)}


def write_receptor_without_hetatm(source: Path, destination: Path) -> int:
    """Write a docking receptor PDB containing polymer ATOM records only.

    CONECT records are removed as well because they can reference discarded
    HETATM serials. The original/prepared structure remains untouched and can
    still be used by the viewer.
    """
    return write_docking_receptor(source, destination)["receptor_atom_count"]


def _pdb_atom_identity(line: str) -> tuple[str, str, str, str, str] | None:
    if line[:6].strip().upper() != "HETATM":
        return None
    return (
        line[21:22].strip(),
        line[22:27].strip(),
        line[17:20].strip(),
        line[12:16].strip(),
        line[16:17].strip(),
    )


def has_hetero_connectivity(path: Path) -> bool:
    """Return whether a PDB contains a CONECT record for a HETATM serial."""
    lines = path.read_text(errors="replace").splitlines()
    hetero_serials = {
        int(line[6:11])
        for line in lines
        if _pdb_atom_identity(line) is not None and line[6:11].strip().isdigit()
    }
    return any(
        line[:6].strip().upper() == "CONECT"
        and line[6:11].strip().isdigit()
        and int(line[6:11]) in hetero_serials
        for line in lines
    )


def restore_hetero_connectivity(template: Path, prepared: Path) -> int:
    """Restore HETATM bond orders removed by PDB2PQR.

    PDB encodes double/triple bonds by repeating a target serial in CONECT.
    Atom serials may change during preparation, so records are translated by
    chain, residue, atom name, and alternate-location identity.
    """
    template_lines = template.read_text(errors="replace").splitlines()
    prepared_lines = prepared.read_text(errors="replace").splitlines()
    template_atoms: dict[int, tuple[str, str, str, str, str]] = {}
    prepared_atoms: dict[tuple[str, str, str, str, str], int] = {}
    for line in template_lines:
        identity = _pdb_atom_identity(line)
        if identity is not None and line[6:11].strip().isdigit():
            template_atoms[int(line[6:11])] = identity
    for line in prepared_lines:
        identity = _pdb_atom_identity(line)
        if identity is not None and line[6:11].strip().isdigit():
            prepared_atoms[identity] = int(line[6:11])

    translated: list[str] = []
    for line in template_lines:
        if line[:6].strip().upper() != "CONECT":
            continue
        fields = [line[index : index + 5].strip() for index in range(6, len(line), 5)]
        if not fields or not fields[0].isdigit():
            continue
        source_identity = template_atoms.get(int(fields[0]))
        source_serial = prepared_atoms.get(source_identity) if source_identity else None
        if source_serial is None:
            continue
        targets = []
        for field in fields[1:]:
            if not field.isdigit():
                continue
            target_identity = template_atoms.get(int(field))
            target_serial = prepared_atoms.get(target_identity) if target_identity else None
            if target_serial is not None:
                targets.append(target_serial)
        for start in range(0, len(targets), 4):
            chunk = targets[start : start + 4]
            if chunk:
                translated.append(f"CONECT{source_serial:5d}{''.join(f'{x:5d}' for x in chunk)}")

    if not translated:
        return 0
    body = [line for line in prepared_lines if line[:6].strip().upper() not in {"CONECT", "END"}]
    prepared.write_text("\n".join([*body, *translated, "END"]) + "\n")
    return len(translated)


HETERO_BOND_ORDER_REMARK = "REMARK 950 ISCREEM HETERO BOND ORDERS"


def infer_organic_hetero_bond_orders(path: Path) -> int:
    """Infer organic HETATM bond orders and encode them in CONECT records."""
    lines = path.read_text(errors="replace").splitlines()
    if HETERO_BOND_ORDER_REMARK in lines:
        return 0
    groups: dict[tuple[str, str, str, str], list[tuple[str, str]]] = defaultdict(list)
    for line in lines:
        identity = _pdb_atom_identity(line)
        if identity is None:
            continue
        chain, residue_number, residue_name, _, _ = identity
        element = (line[76:78].strip() or line[12:16].strip()[:1]).upper()
        groups[(chain, residue_number, residue_name, line[26:27].strip())].append(
            (line, element)
        )

    try:
        from rdkit import Chem
        from rdkit.Chem import rdDetermineBonds
    except ImportError:
        return 0

    inferred: dict[tuple[int, int], int] = {}
    for (_, _, residue_name, _), entries in groups.items():
        elements = {element for _, element in entries}
        if residue_name.upper() in WATERS | POLYMER_HETERO or "C" not in elements:
            continue
        block = "\n".join([*(line for line, _ in entries), "END"]) + "\n"
        base = Chem.MolFromPDBBlock(
            block, sanitize=False, removeHs=False, proximityBonding=True
        )
        if base is None:
            continue
        molecule = None
        for charge in (0, -1, 1, -2, 2, -3, 3, -4, 4):
            candidate = Chem.Mol(base)
            try:
                rdDetermineBonds.DetermineBondOrders(candidate, charge=charge)
                Chem.SanitizeMol(candidate)
                Chem.Kekulize(candidate, clearAromaticFlags=True)
            except (ValueError, RuntimeError):
                continue
            molecule = candidate
            break
        if molecule is None:
            continue
        for bond in molecule.GetBonds():
            begin_info = bond.GetBeginAtom().GetPDBResidueInfo()
            end_info = bond.GetEndAtom().GetPDBResidueInfo()
            if begin_info is None or end_info is None:
                continue
            first, second = sorted(
                (begin_info.GetSerialNumber(), end_info.GetSerialNumber())
            )
            order = max(1, min(3, round(bond.GetBondTypeAsDouble())))
            inferred[(first, second)] = order

    if not inferred:
        return 0

    adjacency: dict[int, list[int]] = defaultdict(list)
    for line in lines:
        if line[:6].strip().upper() != "CONECT":
            continue
        fields = [
            line[index : index + 5].strip() for index in range(6, len(line), 5)
        ]
        if not fields or not fields[0].isdigit():
            continue
        source = int(fields[0])
        adjacency[source].extend(int(field) for field in fields[1:] if field.isdigit())

    for (first, second), order in inferred.items():
        adjacency[first] = [target for target in adjacency[first] if target != second]
        adjacency[second] = [target for target in adjacency[second] if target != first]
        adjacency[first].extend([second] * order)
        adjacency[second].extend([first] * order)

    connectivity: list[str] = []
    for source, targets in sorted(adjacency.items()):
        for start in range(0, len(targets), 4):
            chunk = targets[start : start + 4]
            if chunk:
                connectivity.append(
                    f"CONECT{source:5d}{''.join(f'{target:5d}' for target in chunk)}"
                )
    body = [
        line
        for line in lines
        if line[:6].strip().upper() not in {"CONECT", "END"}
        and line != HETERO_BOND_ORDER_REMARK
    ]
    path.write_text(
        "\n".join([*body, HETERO_BOND_ORDER_REMARK, *connectivity, "END"]) + "\n"
    )
    return len(inferred)


def receptor_atom_coordinates(path: Path) -> list[list[float]]:
    """Return polymer ATOM coordinates for a receptor-wide search box."""
    coordinates: list[list[float]] = []
    with path.open("r", errors="replace") as handle:
        for line in handle:
            if line[:6].strip().upper() != "ATOM":
                continue
            try:
                coordinates.append([float(line[30:38]), float(line[38:46]), float(line[46:54])])
            except ValueError:
                continue
    if not coordinates:
        raise ValueError("Prepared receptor contains no coordinates for global search")
    return coordinates


def residue_coordinates(path: Path, residues: list[str]) -> list[list[float]]:
    wanted = {r.replace("_", "").replace(" ", "").upper() for r in residues}
    coords = []
    with path.open("r", errors="replace") as handle:
        for line in handle:
            if line[:6].strip() not in {"ATOM", "HETATM"}:
                continue
            residue = f"{line[17:20].strip()}{line[21:22].strip()}{line[22:27].strip()}".replace(" ", "").upper()
            alt = f"{line[17:20].strip()}{line[22:27].strip()}".replace(" ", "").upper()
            if residue in wanted or alt in wanted:
                try:
                    coords.append([float(line[30:38]), float(line[38:46]), float(line[46:54])])
                except ValueError:
                    continue
    return coords
