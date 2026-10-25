import os
import platform
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config import settings


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


def run_command(args: list[str], cwd: Path, timeout: int | None = None) -> CommandResult:
    """Run an allowlisted, already-assembled command without a shell."""
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout or settings.command_timeout_seconds)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
        raise TimeoutError(f"Command timed out after {timeout or settings.command_timeout_seconds}s")
    return CommandResult(args, proc.returncode, stdout, stderr)


def probe_tool(command: str, probes: tuple[tuple[str, ...], ...] = (("--version",), ("--help",))):
    executable = shutil.which(command)
    if not executable:
        return {"name": command, "available": False, "path": None, "version": None, "error": "not found on PATH"}
    last_error = "executable did not respond to probe"
    for extra in probes:
        try:
            done = subprocess.run([executable, *extra], capture_output=True, text=True, timeout=8, check=False)
            output = (done.stdout or done.stderr).strip()
            if done.returncode == 0 or (extra == ("--help",) and output):
                first = output.splitlines()[0][:300] if output else "probe succeeded"
                return {"name": command, "available": True, "path": executable, "version": first, "error": None}
            last_error = f"probe exited {done.returncode}"
        except Exception as exc:  # tool boundary: retain actionable probe error
            last_error = str(exc)
    return {"name": command, "available": False, "path": executable, "version": None, "error": last_error}


def all_tools():
    import importlib.util

    gnina_path = shutil.which(settings.gnina_command)
    if platform.system() == "Darwin":
        gnina = {
            "name": settings.gnina_command,
            "available": False,
            "path": gnina_path,
            "version": None,
            "error": "gnina's official pre-built executable is Linux/WSL-only and cannot run on macOS",
        }
    else:
        gnina = probe_tool(settings.gnina_command)

    cli = {
        "smina": probe_tool(settings.smina_command),
        "gnina": gnina,
        "p2rank": probe_tool(settings.p2rank_command),
        "redis": probe_tool(settings.redis_server_command),
        "pdb2pqr": probe_tool(settings.pdb2pqr_command),
        "plip": probe_tool("plip"),
        "meeko_receptor": probe_tool("mk_prepare_receptor.py"),
        "pymol": probe_tool("pymol"),
    }
    for name, module in (
        ("rdkit", "rdkit"),
        ("meeko", "meeko"),
        ("pdbfixer", "pdbfixer"),
        ("openmm", "openmm"),
    ):
        found = importlib.util.find_spec(module) is not None
        cli[name] = {"name": name, "available": found, "path": None, "version": "Python package installed" if found else None, "error": None if found else "Python package not installed"}
    return cli
