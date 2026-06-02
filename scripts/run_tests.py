"""
Run pytest with the current Python interpreter.

Usage:
  python scripts/run_tests.py
  python scripts/run_tests.py tests/test_rag_service.py -q
  python scripts/run_tests.py --no-install -k "test_name"
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.runtime_env import ensure_python_modules


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])

    auto_install = True
    if "--no-install" in args:
        args.remove("--no-install")
        auto_install = False

    try:
        ensure_python_modules(
            {"pytest": "pytest"},
            auto_install=auto_install,
            python_executable=sys.executable,
        )
    except Exception as exc:
        print(f"[run_tests] ERROR: {exc}")
        return 1

    pytest_args = args or ["tests"]
    cmd = [sys.executable, "-m", "pytest", *pytest_args]
    print(f"[run_tests] Running: {' '.join(cmd)}")

    result = subprocess.run(cmd, cwd=str(ROOT))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
