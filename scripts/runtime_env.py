"""
Runtime bootstrap helpers shared by local automation scripts.

This module intentionally uses the *current* Python interpreter
(`sys.executable`) and never assumes a project-local virtualenv.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_FILE = ROOT / "requirements.txt"


def _format_cmd(cmd: list[str]) -> str:
    """Render a shell-friendly command preview for logs."""
    return " ".join(cmd)


def _module_available(module_name: str) -> bool:
    """Return True when a Python module is importable."""
    return importlib.util.find_spec(module_name) is not None


def install_requirements(*, python_executable: str = sys.executable) -> None:
    """
    Install project dependencies with the given Python interpreter.

    Raises:
        RuntimeError: when requirements.txt is missing.
        subprocess.CalledProcessError: when pip install fails.
    """
    if not REQUIREMENTS_FILE.exists():
        raise RuntimeError(
            f"requirements.txt not found at: {REQUIREMENTS_FILE}"
        )

    commands = [
        [
            python_executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "pip",
            "--disable-pip-version-check",
        ],
        [
            python_executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(REQUIREMENTS_FILE),
            "--disable-pip-version-check",
        ],
    ]

    for cmd in commands:
        print(f"[runtime_env] Running: {_format_cmd(cmd)}")
        subprocess.check_call(cmd, cwd=str(ROOT))


def ensure_python_modules(
    required: dict[str, str],
    *,
    auto_install: bool = True,
    python_executable: str = sys.executable,
) -> None:
    """
    Ensure required Python modules are importable.

    Args:
        required: mapping of module_name -> package_name.
        auto_install: install requirements.txt if anything is missing.
        python_executable: interpreter to use for installation.

    Raises:
        RuntimeError: when modules remain missing.
        subprocess.CalledProcessError: when auto-install fails.
    """
    missing = [
        package_name
        for module_name, package_name in required.items()
        if not _module_available(module_name)
    ]
    if not missing:
        return

    missing_list = ", ".join(sorted(set(missing)))
    print(f"[runtime_env] Missing Python packages: {missing_list}")

    if not auto_install:
        raise RuntimeError(
            "Missing required dependencies. "
            f"Install them with: {python_executable} -m pip install -r requirements.txt"
        )

    print("[runtime_env] Installing dependencies from requirements.txt ...")
    install_requirements(python_executable=python_executable)

    still_missing = [
        package_name
        for module_name, package_name in required.items()
        if not _module_available(module_name)
    ]
    if still_missing:
        still_missing_list = ", ".join(sorted(set(still_missing)))
        raise RuntimeError(
            "Dependencies are still missing after installation: "
            f"{still_missing_list}"
        )
