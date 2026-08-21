"""Deterministic local CI-equivalent quality and release-safety gate."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

try:
    from public_safety import scan_archive, scan_tree
except ModuleNotFoundError:  # Imported as tools.quality_gate by focused tests.
    from tools.public_safety import scan_archive, scan_tree

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_INDEX_MODES = frozenset({"100644", "100755", "120000"})


class PublishableEntry(NamedTuple):
    relative: PurePosixPath
    index_mode: str | None


def _git(root: Path, arguments: list[str], *, expected: tuple[int, ...] = (0,)) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
    )
    if completed.returncode not in expected:
        raise RuntimeError(
            f"Git command failed with status {completed.returncode}: git {' '.join(arguments)}"
        )
    return completed.stdout


def _is_git_worktree(root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    if result.stdout.strip() != "true":
        raise RuntimeError("Git reported an unsupported non-worktree repository")
    top_level = Path(
        _git(root, ["rev-parse", "--show-toplevel"]).decode("utf-8", "surrogateescape").strip()
    ).resolve()
    if top_level != root.resolve():
        raise RuntimeError("quality gate root must be the Git worktree root")
    return True


def _relative_path(raw: bytes) -> PurePosixPath:
    decoded = os.fsdecode(raw)
    pieces = decoded.split("/")
    if (
        not decoded
        or decoded.startswith("/")
        or "\\" in decoded
        or any(piece in {"", ".", ".."} for piece in pieces)
    ):
        raise RuntimeError("Git returned an unsafe repository path")
    relative = PurePosixPath(*pieces)
    if relative.parts[0] == ".git":
        raise RuntimeError("Git inventory unexpectedly included its control directory")
    return relative


def _nul_paths(output: bytes) -> list[PurePosixPath]:
    if output and not output.endswith(b"\0"):
        raise RuntimeError("Git returned a malformed NUL-delimited path list")
    return [_relative_path(raw) for raw in output.split(b"\0") if raw]


def _publishable_entries(root: Path) -> list[PublishableEntry]:
    indexed: dict[PurePosixPath, str] = {}
    staged = _git(root, ["ls-files", "--stage", "-z"])
    if staged and not staged.endswith(b"\0"):
        raise RuntimeError("Git returned malformed index metadata")
    for record in (item for item in staged.split(b"\0") if item):
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise RuntimeError("Git returned malformed index metadata")
        mode = fields[0].decode("ascii", "strict")
        stage = fields[2]
        relative = _relative_path(raw_path)
        if stage != b"0" or mode not in SUPPORTED_INDEX_MODES or relative in indexed:
            raise RuntimeError("Git index contains an unsupported repository entry")
        indexed[relative] = mode

    tracked = set(_nul_paths(_git(root, ["ls-files", "--cached", "-z"])))
    if tracked != set(indexed):
        raise RuntimeError("Git tracked-file inventory is inconsistent with index metadata")
    untracked = set(_nul_paths(_git(root, ["ls-files", "--others", "--exclude-standard", "-z"])))
    if tracked & untracked:
        raise RuntimeError("Git returned overlapping tracked and untracked paths")

    entries = [
        PublishableEntry(relative, indexed[relative])
        for relative in tracked
        if (root / relative).exists() or (root / relative).is_symlink()
    ]
    entries.extend(PublishableEntry(relative, None) for relative in untracked)
    return sorted(entries, key=lambda item: item.relative.as_posix())


def _copy_entry(root: Path, snapshot: Path, entry: PublishableEntry) -> None:
    source = root.joinpath(*entry.relative.parts)
    target = snapshot.joinpath(*entry.relative.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.parent.resolve().is_relative_to(snapshot.resolve()):
        raise RuntimeError("snapshot parent escaped through a repository symlink")
    try:
        source_status = source.lstat()
    except OSError as error:
        raise RuntimeError("publishable repository entry disappeared during snapshot") from error

    source_is_link = stat.S_ISLNK(source_status.st_mode)
    if entry.index_mode == "120000" and not source_is_link:
        raise RuntimeError("tracked symlink does not match its index mode")
    if entry.index_mode in {"100644", "100755"} and not stat.S_ISREG(source_status.st_mode):
        raise RuntimeError("tracked file does not match its index mode")
    if entry.index_mode is None and not (source_is_link or stat.S_ISREG(source_status.st_mode)):
        raise RuntimeError("untracked repository entry has an unsupported file type")

    if source_is_link:
        target.symlink_to(os.readlink(source))
        if os.readlink(target) != os.readlink(source):
            raise RuntimeError("snapshot symlink target is inconsistent")
        return
    shutil.copy2(source, target, follow_symlinks=False)
    target_status = target.lstat()
    if source.read_bytes() != target.read_bytes() or stat.S_IMODE(
        source_status.st_mode
    ) != stat.S_IMODE(target_status.st_mode):
        raise RuntimeError("snapshot file content or mode is inconsistent")


@contextlib.contextmanager
def publishable_snapshot(root: Path) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="market-risk-lab-publishable-") as directory:
        snapshot = Path(directory)
        for entry in _publishable_entries(root):
            _copy_entry(root, snapshot, entry)
        yield snapshot


def scan_publishable_content(root: Path) -> list[Any]:
    if not _is_git_worktree(root):
        return scan_tree(root)
    with publishable_snapshot(root) as snapshot:
        return scan_tree(snapshot)


def run(command: list[str], *, capture: bool = False, expected: int = 0) -> str:
    print(f"[gate] {' '.join(command)}", flush=True)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=capture,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    if completed.returncode != expected:
        if capture:
            sys.stdout.write(completed.stdout)
            sys.stderr.write(completed.stderr)
        raise RuntimeError(
            f"command returned {completed.returncode}, expected {expected}: {' '.join(command)}"
        )
    return completed.stdout if capture else ""


def json_command(arguments: list[str]) -> dict[str, Any]:
    return json.loads(run(["uv", "run", "market-risk-lab", *arguments], capture=True))


def normalized_results(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "risk_results": payload["risk_results"],
        "stress_results": payload["stress_results"],
        "factor_metrics": payload["factor_metrics"],
    }


def end_to_end() -> None:
    with tempfile.TemporaryDirectory(prefix="market-risk-lab-ci-") as directory:
        temporary = Path(directory)
        generated = temporary / "synthetic"
        database = temporary / "market-risk.db"
        json_command(
            [
                "demo",
                "generate",
                "--specification",
                str(ROOT / "data/synthetic/fixture-spec.toml"),
                "--output-dir",
                str(generated),
            ]
        )
        first_migration = json_command(["db", "migrate", "--database", str(database)])
        second_migration = json_command(["db", "migrate", "--database", str(database)])
        if (
            first_migration["applied_migrations"] != [1, 2]
            or second_migration["applied_migrations"] != []
        ):
            raise RuntimeError("database migrations are not idempotent")
        first_load = json_command(
            ["demo", "load", "--data-dir", str(generated), "--database", str(database)]
        )
        second_load = json_command(
            ["demo", "load", "--data-dir", str(generated), "--database", str(database)]
        )
        if first_load != second_load:
            raise RuntimeError("fixture loading is not idempotent")
        first_run = json_command(["risk", "run", "--database", str(database)])
        second_run = json_command(["risk", "run", "--database", str(database)])
        json_command(["demo", "inspect", "--database", str(database)])
        first_results = json_command(
            ["risk", "inspect", "--database", str(database), "--run-id", first_run["run_id"]]
        )
        second_results = json_command(
            ["risk", "inspect", "--database", str(database), "--run-id", second_run["run_id"]]
        )
        if normalized_results(first_results) != normalized_results(second_results):
            raise RuntimeError("repeated deterministic calculations differ")
        failure = run(
            [
                "uv",
                "run",
                "market-risk-lab",
                "risk",
                "run",
                "--database",
                str(database),
                "--confidence-levels",
                "invalid",
            ],
            capture=True,
            expected=1,
        )
        if "Traceback" in failure or str(temporary) in failure:
            raise RuntimeError("expected CLI error exposed a traceback or local temporary path")


def build_and_inspect() -> None:
    with tempfile.TemporaryDirectory(prefix="market-risk-lab-build-") as directory:
        temporary = Path(directory)
        distribution = temporary / "dist"
        run(["uv", "build", "--out-dir", str(distribution)])
        archives = sorted(
            path
            for path in distribution.iterdir()
            if path.suffix == ".whl" or path.name.endswith(".tar.gz")
        )
        if len(archives) != 2 or not any(path.suffix == ".whl" for path in archives):
            raise RuntimeError("expected exactly one wheel and one source distribution")
        findings = [finding for path in archives for finding in scan_archive(path)]
        if findings:
            details = "; ".join(f"{item.path}: {item.reason}" for item in findings)
            raise RuntimeError(f"build archive safety scan failed: {details}")
        wheel = next(path for path in archives if path.suffix == ".whl")
        environment = temporary / "wheel-venv"
        run(["uv", "venv", "--python", "3.12", str(environment)])
        executable = environment / "bin" / "python"
        run(["uv", "pip", "install", "--python", str(executable), str(wheel)])
        status = run([str(environment / "bin" / "market-risk-lab"), "status"], capture=True)
        if "local public-release candidate" not in status:
            raise RuntimeError("built-wheel status smoke test returned unexpected output")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-sync",
        action="store_true",
        help="Skip only when frozen sync already ran in this job",
    )
    arguments = parser.parse_args()
    if not arguments.skip_sync:
        run(["uv", "sync", "--frozen", "--extra", "dashboard"])
    run(["uv", "run", "ruff", "format", "--check", "src", "tests", "tools"])
    run(["uv", "run", "ruff", "check", "src", "tests", "tools"])
    run(["uv", "run", "mypy", "src"])
    run(
        [
            "uv",
            "run",
            "pytest",
            "--cov=market_risk_engine",
            "--cov-branch",
            "--cov-report=term-missing",
            "--cov-fail-under=80",
        ]
    )
    end_to_end()
    findings = scan_publishable_content(ROOT)
    if findings:
        details = "; ".join(f"{item.path}: {item.reason}" for item in findings)
        raise RuntimeError(f"public safety scan failed: {details}")
    build_and_inspect()
    print("[gate] PASS: local CI-equivalent validation completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
