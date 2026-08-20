"""Deterministic local CI-equivalent quality and release-safety gate."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from public_safety import scan_archive, scan_tree

ROOT = Path(__file__).resolve().parents[1]


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
    findings = scan_tree(ROOT)
    if findings:
        details = "; ".join(f"{item.path}: {item.reason}" for item in findings)
        raise RuntimeError(f"public safety scan failed: {details}")
    build_and_inspect()
    print("[gate] PASS: local CI-equivalent validation completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
