# Third-party software notices

This document describes the licensing boundary of the iScreeM source
repository. It is a practical compliance record, not legal advice.

## Scope of the iScreeM license

The root [MIT License](LICENSE) applies only to original iScreeM source code
and assets. It does not relicense third-party software, models, configuration
files, containers, or command-line tools. Each third-party component remains
subject to its own license.

The Git repository intentionally does **not** contain the smina, gnina, Redis,
or P2Rank executables, P2Rank models, molecular structures, docking output, or
project data. Those paths are excluded by `.gitignore`. Users install or
provide runtime tools separately.

## External command-line tools

| Component | Repository distribution | License information |
| --- | --- | --- |
| smina | Executable not included | Upstream contains Apache-2.0 and GPL-2.0 components. A distributor adding a GPL-covered binary must satisfy the GPL-2.0 source and notice requirements. [Source](https://github.com/mwojcikowski/smina) · [GPL-2.0 text](https://github.com/mwojcikowski/smina/blob/master/LICENSE.GNU) · [Apache-2.0 text](https://github.com/mwojcikowski/smina/blob/master/LICENSE.APACHE) |
| gnina | Executable not included | Upstream contains Apache-2.0 and GPL-2.0 components; official static binaries include GPL-covered components. A distributor adding a binary must satisfy the applicable GPL-2.0 requirements. [Source](https://github.com/gnina/gnina) · [GPL-2.0 text](https://github.com/gnina/gnina/blob/master/LICENSE.GNU) · [Apache-2.0 text](https://github.com/gnina/gnina/blob/master/LICENSE.APACHE) |
| P2Rank | Executable and models not included; upstream configuration examples and README are retained under `tools/p2rank` | MIT. The upstream copyright and license text are preserved in [`tools/p2rank/LICENSE.txt`](tools/p2rank/LICENSE.txt). [Source](https://github.com/rdk/p2rank) |
| Redis server | Executable not included | Docker development is pinned to Redis 7.2, which is BSD-3-Clause. Redis 7.4 uses RSALv2/SSPLv1; Redis 8 offers AGPL-3.0/RSALv2/SSPLv1. A locally installed version keeps its upstream terms. [Official license matrix](https://redis.io/legal/licenses/) |

The Python `redis` package used by iScreeM is the MIT-licensed redis-py client,
not the Redis server.

## Direct application dependencies

These packages are installed from their normal package registries and are not
vendored in this repository. The lock file and environment file determine the
exact installed versions; transitive dependencies retain their own terms.

### Frontend

| Package | License |
| --- | --- |
| React / React DOM | MIT |
| 3Dmol.js | BSD-3-Clause (with incorporated upstream notices) |
| Ketcher | Apache-2.0 |
| TanStack React Table | MIT |
| Lucide React | ISC |

See [`frontend/package-lock.json`](frontend/package-lock.json) for the complete
resolved dependency graph and each package's registry metadata.

### Backend and molecular tooling

| Package | License |
| --- | --- |
| FastAPI | MIT |
| Uvicorn | BSD-3-Clause |
| pydantic-settings | MIT |
| SQLAlchemy | MIT |
| python-multipart | Apache-2.0 |
| redis-py | MIT |
| RQ | BSD-2-Clause |
| RDKit | BSD-3-Clause |
| Meeko | LGPL-2.1 |
| SciPy | BSD-3-Clause |
| Gemmi | MPL-2.0 |
| ProDy | MIT |
| PDBFixer | MIT |
| OpenMM | MIT with separately identified bundled/component terms |
| PDB2PQR | BSD |

See [`backend/pyproject.toml`](backend/pyproject.toml) and
[`environment.yml`](environment.yml) for the declared dependency set. Binary
packages installed by conda or pip include their authoritative license files.

## Redistribution checklist

Publishing this source repository does not publish the ignored runtime tools.
If you later publish a desktop bundle, container image, installer, or archive
that includes third-party binaries, you must also:

1. inventory the exact versions and files in that artifact;
2. include every required copyright, license, and NOTICE file;
3. provide corresponding source or a valid source offer for GPL-covered
   binaries such as applicable smina/gnina builds;
4. preserve LGPL replacement/relinking rights where applicable;
5. review the license of the exact Redis version being distributed; and
6. regenerate notices for all bundled Python, conda, npm, system, and base
   image packages.

Molecular structures and user-generated results are not part of the source
distribution and remain the responsibility of their respective providers and
users.
