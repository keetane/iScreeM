# Local command-line tools

iScreeM resolves command-line dependencies from this directory before the
system `PATH`.

- `smina/`: local docking executable location and upstream license notices.
- `p2rank/`: local P2Rank location. Only its MIT-licensed README,
  configuration examples, and license are tracked; executable, models, and
  libraries are ignored.
- `redis/`: local Redis server/client location and version-specific licensing
  notes.
- `gnina/`: local executable location and installation notes. The official gnina
  pre-built executable is Linux/WSL-only, so iScreeM disables it on macOS.

The Git repository does not distribute these executables. Upstream source and
license links are available in the application's **OSS / Licenses** section and
in the root `THIRD_PARTY_NOTICES.md`.
