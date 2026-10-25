#!/usr/bin/env python3
"""Install and verify dependencies bundled with iScreeM.

Command-line tools are resolved from ``iScreeM/tools`` first. Python structure
preparation dependencies use the dedicated ``iScreeM`` conda environment.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_P2RANK = str(ROOT / "tools" / "p2rank" / "prank")
DEFAULT_SMINA = str(ROOT / "tools" / "smina" / "smina")
DEFAULT_GNINA = str(ROOT / "tools" / "gnina" / "gnina")
DEFAULT_REDIS_SERVER = str(ROOT / "tools" / "redis" / "redis-server")
DEFAULT_REDIS_CLI = str(ROOT / "tools" / "redis" / "redis-cli")
DEFAULT_ENV = "iScreeM"


def executable(value: str) -> str | None:
    candidate = Path(value).expanduser()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return shutil.which(value)


def probe(command: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [command, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except OSError as exc:
        return False, str(exc)
    output = (result.stdout or result.stderr).strip().splitlines()
    if result.returncode == 0 or output:
        return True, output[0] if output else "probe succeeded"
    return False, f"exited {result.returncode}"


def redis_ready() -> bool:
    cli = executable(DEFAULT_REDIS_CLI) or shutil.which("redis-cli")
    if not cli:
        return False
    result = subprocess.run(
        [cli, "ping"], capture_output=True, text=True, timeout=5, check=False
    )
    return result.returncode == 0 and result.stdout.strip() == "PONG"


def ensure_redis(install: bool) -> tuple[bool, str]:
    server = executable(DEFAULT_REDIS_SERVER) or shutil.which("redis-server")
    if not server and install:
        brew = shutil.which("brew")
        if not brew:
            return False, "redis-server not found and Homebrew is unavailable"
        print("Redis is not installed; installing it with Homebrew...")
        result = subprocess.run([brew, "install", "redis"], check=False)
        if result.returncode:
            return (
                False,
                "Homebrew could not install Redis; update Command Line Tools and rerun setup",
            )
        server = shutil.which("redis-server")
    if not server:
        return False, "redis-server not found"
    if not redis_ready():
        brew = shutil.which("brew")
        if brew:
            subprocess.run(
                [brew, "services", "start", "redis"],
                check=False,
                stdout=subprocess.DEVNULL,
            )
        for _ in range(10):
            if redis_ready():
                break
            time.sleep(0.5)
        if not redis_ready():
            return (
                False,
                f"Redis is installed at {server}, but localhost:6379 is not responding",
            )
    return True, server


def ensure_iscreem_environment(env_name: str, install: bool) -> tuple[bool, str]:
    manager = shutil.which("mamba") or shutil.which("conda")
    if not manager:
        manager = next(
            (
                str(path)
                for path in (
                    Path.home() / "miniforge3/bin/mamba",
                    Path.home() / "miniforge3/bin/conda",
                    Path.home() / "miniconda3/bin/conda",
                    Path.home() / "anaconda3/bin/conda",
                    Path("/opt/homebrew/Caskroom/miniforge/base/bin/conda"),
                )
                if path.is_file()
            ),
            None,
        )
    if not manager:
        return False, "conda/mamba not found; install Miniforge first"
    env_file = ROOT / "environment.yml"
    if not env_file.is_file():
        return False, f"missing environment file: {env_file}"
    probe = subprocess.run(
        [manager, "run", "-n", env_name, "python", "-c", "import pdbfixer, openmm"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if probe.returncode == 0:
        return True, f"{env_name} ({manager})"
    if not install:
        return False, f"{env_name} is missing PDBFixer/OpenMM"
    print(f"Installing the {env_name} conda environment from {env_file}...")
    update = subprocess.run(
        [manager, "env", "update", "-n", env_name, "-f", str(env_file)],
        check=False,
    )
    if update.returncode:
        return False, f"conda could not create/update {env_name}"
    verify = subprocess.run(
        [manager, "run", "-n", env_name, "python", "-c", "import pdbfixer, openmm"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return (
        (True, f"{env_name} ({manager})")
        if verify.returncode == 0
        else (False, verify.stderr.strip()[-500:])
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install and verify iScreeM local dependencies"
    )
    parser.add_argument(
        "--p2rank", default=os.getenv("MDS_P2RANK_COMMAND", DEFAULT_P2RANK)
    )
    parser.add_argument(
        "--smina", default=os.getenv("MDS_SMINA_COMMAND", DEFAULT_SMINA)
    )
    parser.add_argument(
        "--gnina", default=os.getenv("MDS_GNINA_COMMAND", DEFAULT_GNINA)
    )
    parser.add_argument("--env-name", default=os.getenv("MDS_CONDA_ENV", DEFAULT_ENV))
    parser.add_argument(
        "--no-install-redis",
        action="store_true",
        help="Only check Redis; do not install it",
    )
    parser.add_argument(
        "--no-install-python",
        action="store_true",
        help="Only check the iScreeM Python environment",
    )
    args = parser.parse_args()

    failures = 0
    for label, value in (("P2Rank", args.p2rank), ("smina", args.smina)):
        path = executable(value)
        if not path:
            print(f"[missing] {label}: {value}")
            failures += 1
            continue
        ok, detail = probe(path)
        print(f"[{'ok' if ok else 'error'}] {label}: {path} ({detail})")
        failures += not ok

    if sys.platform == "darwin":
        print(
            f"[disabled] gnina: {args.gnina} (official binary supports Linux/WSL, not macOS)"
        )
    else:
        path = executable(args.gnina)
        if not path:
            print(f"[missing] gnina: {args.gnina}")
            failures += 1
        else:
            ok, detail = probe(path)
            print(f"[{'ok' if ok else 'error'}] gnina: {path} ({detail})")
            failures += not ok

    ok, detail = ensure_redis(not args.no_install_redis)
    print(f"[{'ok' if ok else 'error'}] Redis: {detail}")
    failures += not ok
    ok, detail = ensure_iscreem_environment(args.env_name, not args.no_install_python)
    print(f"[{'ok' if ok else 'error'}] Python ({args.env_name}): {detail}")
    failures += not ok
    if failures:
        print("Dependency setup is incomplete.", file=sys.stderr)
        return 1
    print("All iScreeM dependencies are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
