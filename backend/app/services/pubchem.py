"""Resolve compound names to SMILES through PubChem PUG REST."""

import json
from collections.abc import Callable
from threading import Lock
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

PUBCHEM_PUG_REST = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
PUBCHEM_REQUEST_INTERVAL_SECONDS = 0.21
PUBCHEM_MAX_ATTEMPTS = 3
PUBCHEM_RETRY_DELAY_SECONDS = 0.5
_request_lock = Lock()
_last_request_started = 0.0


def fetch_pubchem_smiles(name: str, timeout: float = 15.0) -> str:
    global _last_request_started
    encoded = quote(name.strip(), safe="")
    url = f"{PUBCHEM_PUG_REST}/compound/name/{encoded}/property/SMILES/JSON"
    request = Request(url, headers={"User-Agent": "iScreeM/0.1 (PubChem PUG REST client)"})
    payload = None
    last_error: Exception | None = None
    for attempt in range(PUBCHEM_MAX_ATTEMPTS):
        try:
            with _request_lock:
                delay = PUBCHEM_REQUEST_INTERVAL_SECONDS - (
                    monotonic() - _last_request_started
                )
                if delay > 0:
                    sleep(delay)
                _last_request_started = monotonic()
                with urlopen(request, timeout=timeout) as response:
                    payload = json.load(response)
            break
        except HTTPError as exc:
            if exc.code == 404:
                raise ValueError(
                    f'PubChem did not find a compound named "{name}"'
                ) from exc
            if exc.code not in {429, 500, 502, 503, 504}:
                raise ValueError(f"PubChem request failed ({exc.code})") from exc
            last_error = exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
        if attempt + 1 < PUBCHEM_MAX_ATTEMPTS:
            sleep(PUBCHEM_RETRY_DELAY_SECONDS * (2**attempt))
    if payload is None:
        raise ValueError(
            "Could not connect to PubChem after 3 attempts. "
            "Check the internet or DNS connection and try again."
        ) from last_error
    properties = payload.get("PropertyTable", {}).get("Properties", [])
    if not properties:
        raise ValueError(f'PubChem returned no SMILES for "{name}"')
    smiles = next(
        (
            properties[0].get(key)
            for key in ("SMILES", "ConnectivitySMILES", "CanonicalSMILES")
            if properties[0].get(key)
        ),
        None,
    )
    if not smiles:
        raise ValueError(f'PubChem returned no SMILES for "{name}"')
    return str(smiles)


def resolve_ligand_queries(
    queries: list[str],
    lookup: Callable[[str], str] = fetch_pubchem_smiles,
) -> list[dict[str, str | None]]:
    try:
        from rdkit import Chem, rdBase
    except ImportError as exc:
        raise ValueError("RDKit is required to identify SMILES input") from exc

    resolved: list[dict[str, str | None]] = []
    for raw_query in queries:
        query = raw_query.strip()
        if not query:
            continue
        first, *name_parts = query.split()
        with rdBase.BlockLogs():
            molecule = Chem.MolFromSmiles(first)
        if molecule is not None:
            resolved.append(
                {
                    "smiles": Chem.MolToSmiles(molecule, isomericSmiles=True),
                    "name": " ".join(name_parts) or None,
                }
            )
            continue
        smiles = lookup(query)
        resolved.append({"smiles": smiles, "name": query})
    if not resolved:
        raise ValueError("Enter at least one SMILES string or compound name")
    return resolved
