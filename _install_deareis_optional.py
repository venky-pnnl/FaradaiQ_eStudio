"""Transactional DearEIS optional-environment installer for FaradaIQ Studio.

This controller intentionally does not upgrade pip in place. A missing or broken
.deareis_venv is moved aside, a fresh environment is created at the final path,
DearEIS is installed and imported, and the old environment is deleted only after
verification. On failure, the old environment is restored.
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

MIN_PYTHON = (3, 11)
PREFERRED_WINDOWS = ((3, 12), (3, 13), (3, 11), (3, 14))


def env_python(env_dir: Path) -> Path:
    if os.name == "nt":
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"


def _remove_readonly(func, path, _exc_info) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except OSError:
        pass


def safe_remove_tree(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        shutil.rmtree(path, onerror=_remove_readonly)
    except OSError:
        return False
    return not path.exists()


class InstallLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = self.path.open("w", encoding="utf-8", errors="replace")

    def close(self) -> None:
        self._fp.close()

    def write(self, message: str = "") -> None:
        print(message, flush=True)
        self._fp.write(message + "\n")
        self._fp.flush()

    def run(self, command: Sequence[str], env: Optional[dict] = None) -> int:
        rendered = subprocess.list2cmdline([str(x) for x in command])
        self.write(f"> {rendered}")
        try:
            process = subprocess.Popen(
                [str(x) for x in command],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
        except OSError as exc:
            self.write(f"ERROR starting command: {exc}")
            return 127
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip("\r\n")
            print(line, flush=True)
            self._fp.write(line + "\n")
        code = process.wait()
        self._fp.write(f"[exit {code}]\n")
        self._fp.flush()
        return code


def _capture(command: Sequence[str]) -> Tuple[int, str]:
    try:
        result = subprocess.run(
            [str(x) for x in command],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return result.returncode, result.stdout.strip()


def _candidate_key(command: Sequence[str]) -> Tuple[str, ...]:
    return tuple(str(x).lower() if os.name == "nt" else str(x) for x in command)


def _valid_seed(command: Sequence[str]) -> bool:
    code, _ = _capture(
        list(command)
        + [
            "-c",
            (
                "import struct,sys,venv; "
                "raise SystemExit(0 if sys.version_info >= (3,11) "
                "and struct.calcsize('P')*8 == 64 else 3)"
            ),
        ]
    )
    return code == 0


def _existing_base_python(existing_python: Path) -> Optional[Path]:
    if existing_python.exists():
        code, output = _capture(
            [str(existing_python), "-c", "import sys; print(sys._base_executable)"]
        )
        if code == 0 and output:
            candidate = Path(output.splitlines()[-1].strip())
            if candidate.exists():
                return candidate
    cfg = existing_python.parent.parent / "pyvenv.cfg"
    if cfg.exists():
        try:
            for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.lower().startswith("home") and "=" in line:
                    home = Path(line.split("=", 1)[1].strip())
                    name = "python.exe" if os.name == "nt" else "python"
                    candidate = home / name
                    if candidate.exists():
                        return candidate
        except OSError:
            pass
    return None


def seed_candidates(existing_python: Path) -> Iterable[List[str]]:
    base = _existing_base_python(existing_python)
    if base is not None:
        yield [str(base)]

    controller_base = Path(getattr(sys, "_base_executable", sys.executable))
    if controller_base.exists():
        yield [str(controller_base)]

    if os.name == "nt":
        for major, minor in PREFERRED_WINDOWS:
            yield ["py", f"-{major}.{minor}"]
        yield ["python"]
        yield ["python3"]
    else:
        yield [sys.executable]
        yield ["python3"]
        yield ["python"]


def find_seed(existing_python: Path) -> Optional[List[str]]:
    seen = set()
    for candidate in seed_candidates(existing_python):
        key = _candidate_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        if _valid_seed(candidate):
            return candidate
    return None


def auto_install_python_312(log: InstallLog) -> bool:
    if os.name != "nt":
        return False
    winget = shutil.which("winget")
    if winget:
        log.write("No usable Python 3.11+ runtime found. Installing user-scope Python 3.12...")
        code = log.run(
            [
                winget,
                "install",
                "-e",
                "--id",
                "Python.Python.3.12",
                "--scope",
                "user",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ]
        )
        return code == 0
    py_launcher = shutil.which("py")
    if py_launcher:
        log.write("No usable Python 3.11+ runtime found. Trying: py install 3.12")
        return log.run([py_launcher, "install", "3.12"]) == 0
    return False


def check_environment(env_dir: Path, module: str = "deareis") -> int:
    python = env_python(env_dir)
    if not python.exists():
        print("DearEIS status: MISSING")
        print("Reason: .deareis_venv has not been created yet.")
        return 2
    code, output = _capture(
        [
            str(python),
            "-c",
            (
                "import importlib,sys\n"
                "print('Python executable:', sys.executable)\n"
                "print('Python version:', sys.version.replace(chr(10),' '))\n"
                "if sys.version_info < (3,11): raise SystemExit(3)\n"
                f"m=importlib.import_module({module!r})\n"
                "print('DearEIS status: INSTALLED')\n"
                "print('DearEIS version:', getattr(m,'__version__','version unknown'))"
            ),
        ]
    )
    if output:
        print(output)
    if code == 0:
        return 0
    if code == 3:
        print("DearEIS status: SKIPPED")
        print("Reason: the optional environment uses Python older than 3.11.")
        return 3
    if "ModuleNotFoundError" in output or "No module named" in output:
        print("DearEIS status: MISSING")
        print(f"Reason: {module} is not installed inside .deareis_venv.")
        return 2
    print("DearEIS status: BROKEN")
    return 4


def _stale_backup_destination(backup: Path) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return backup.with_name(f"{backup.name}.__stale_{stamp}_{os.getpid()}")


def transactional_install(
    root: Path,
    requirements: Path,
    verify_module: str = "deareis",
    seed_command: Optional[Sequence[str]] = None,
    log_path: Optional[Path] = None,
) -> int:
    root = root.resolve()
    env_dir = root / ".deareis_venv"
    backup = root / ".deareis_venv.__backup_v292"
    python = env_python(env_dir)
    if log_path is None:
        log_path = root / "logs" / "deareis_install_v337.log"
    log = InstallLog(log_path)
    old_moved = False
    try:
        log.write("FaradaIQ eStudio DearEIS transactional installer v337")
        log.write(f"Root: {root}")
        log.write(f"Log:  {log_path}")

        if check_environment(env_dir, verify_module) == 0:
            log.write("DearEIS is already ready. No environment changes were made.")
            return 0

        if not requirements.exists():
            log.write(f"ERROR: Requirements file not found: {requirements}")
            return 1

        selected = list(seed_command) if seed_command else find_seed(python)
        if selected is None and auto_install_python_312(log):
            selected = find_seed(python)
        if selected is None:
            log.write("ERROR: No usable 64-bit Python 3.11+ runtime was found.")
            return 1
        log.write(f"Seed Python: {subprocess.list2cmdline(selected)}")

        if backup.exists() and not safe_remove_tree(backup):
            stale = _stale_backup_destination(backup)
            try:
                backup.rename(stale)
                log.write(f"WARNING: Preserved a locked stale backup at: {stale}")
            except OSError as exc:
                log.write(f"ERROR: Could not clear stale backup {backup}: {exc}")
                return 1

        if env_dir.exists():
            try:
                env_dir.rename(backup)
                old_moved = True
                log.write(f"Preserved previous environment: {backup}")
            except OSError as exc:
                log.write(f"ERROR: Existing .deareis_venv is in use or could not be moved: {exc}")
                return 1

        child_env = os.environ.copy()
        child_env.update(
            {
                "PYTHONNOUSERSITE": "1",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_NO_INPUT": "1",
            }
        )

        if log.run(selected + ["-m", "venv", str(env_dir)], env=child_env) != 0:
            raise RuntimeError("Failed to create the clean DearEIS environment")
        if not python.exists():
            raise RuntimeError(f"Virtual environment Python was not created: {python}")

        if log.run([str(python), "-m", "pip", "--version"], env=child_env) != 0:
            log.write("pip is missing in the new environment; bootstrapping it without an upgrade...")
            if log.run([str(python), "-m", "ensurepip", "--default-pip"], env=child_env) != 0:
                raise RuntimeError("Failed to bootstrap pip")

        install_command = [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--no-cache-dir",
            "--prefer-binary",
            "-r",
            str(requirements),
        ]
        if log.run(install_command, env=child_env) != 0:
            raise RuntimeError("DearEIS package installation failed")

        verify_code = log.run(
            [
                str(python),
                "-c",
                (
                    "import importlib,sys; "
                    f"m=importlib.import_module({verify_module!r}); "
                    "print('DearEIS import OK:', getattr(m,'__version__','version unknown')); "
                    "print('Python:', sys.executable)"
                ),
            ],
            env=child_env,
        )
        if verify_code != 0:
            raise RuntimeError("DearEIS installed but import verification failed")

        if old_moved and backup.exists() and not safe_remove_tree(backup):
            log.write(f"WARNING: DearEIS is ready, but the old backup remains at: {backup}")
        log.write("DearEIS installation and verification succeeded.")
        return 0
    except Exception as exc:
        log.write(f"ERROR: {exc}")
        log.write("Rolling back to the previous DearEIS environment...")
        safe_remove_tree(env_dir)
        if old_moved and backup.exists():
            try:
                backup.rename(env_dir)
                log.write("Previous DearEIS environment restored.")
            except OSError as restore_exc:
                log.write(f"WARNING: Automatic rollback failed: {restore_exc}")
                log.write(f"Preserved environment remains at: {backup}")
        return 1
    finally:
        log.close()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--requirements", type=Path, default=None)
    parser.add_argument("--verify-module", default="deareis")
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--seed-python", type=Path, help="Use this interpreter to seed the private DearEIS environment.")
    parser.add_argument("--auto", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    requirements = args.requirements or (root / "requirements_deareis_optional.txt")
    seed = [str(args.seed_python.absolute())] if args.seed_python else None
    if seed and not _valid_seed(seed):
        print(f"The selected eStudio Python could not create an isolated DearEIS runtime: {args.seed_python}", file=sys.stderr)
        return 1
    return transactional_install(
        root=root,
        requirements=requirements.resolve(),
        verify_module=args.verify_module,
        seed_command=seed,
        log_path=args.log,
    )


if __name__ == "__main__":
    raise SystemExit(main())
