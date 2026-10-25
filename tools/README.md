# Bundled command-line tools

iScreeM resolves command-line dependencies from this directory before the
system `PATH`.

- `smina/`: macOS universal docking executable and upstream license notices.
- `p2rank/`: P2Rank distribution (`prank`, models, configuration, libraries).
- `redis/`: Redis server/client used by RQ, plus their runtime libraries.
- `gnina/`: stable command path and installation notes. The official gnina
  pre-built executable is Linux/WSL-only, so iScreeM disables it on macOS.

Upstream source and license links are also available in the application's
**OSS / Licenses** section.
