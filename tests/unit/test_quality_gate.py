from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tools import quality_gate


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "--quiet")
    (tmp_path / ".gitignore").write_text("ignored.db\ngenerated/\n", encoding="utf-8")
    (tmp_path / "safe.txt").write_text("synthetic public content\n", encoding="utf-8")
    _git(tmp_path, "add", "--", ".gitignore", "safe.txt")
    return tmp_path


def test_expected_root_git_metadata_is_not_scanned(repository: Path) -> None:
    assert (repository / ".git").is_dir()
    assert quality_gate.scan_publishable_content(repository) == []


def test_tracked_prohibited_content_is_detected(repository: Path) -> None:
    target = repository / "tracked.db"
    target.write_bytes(b"")
    _git(repository, "add", "--", "tracked.db")
    assert "database file" in {
        finding.reason for finding in quality_gate.scan_publishable_content(repository)
    }


def test_nonignored_untracked_prohibited_content_is_detected(repository: Path) -> None:
    (repository / "untracked.db").write_bytes(b"")
    assert "database file" in {
        finding.reason for finding in quality_gate.scan_publishable_content(repository)
    }


def test_ignored_and_generated_content_follow_existing_policy(repository: Path) -> None:
    (repository / "ignored.db").write_bytes(b"")
    generated = repository / "generated"
    generated.mkdir()
    (generated / "state.db").write_bytes(b"")
    assert quality_gate.scan_publishable_content(repository) == []


def test_nested_publishable_git_metadata_is_not_permitted(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nested = repository / "publishable" / ".git"
    nested.mkdir(parents=True)
    (nested / "config").write_text("synthetic\n", encoding="utf-8")
    original = quality_gate._publishable_entries

    def include_nested(root: Path) -> list[quality_gate.PublishableEntry]:
        return [
            *original(root),
            quality_gate.PublishableEntry(Path("publishable/.git/config"), None),
        ]

    monkeypatch.setattr(quality_gate, "_publishable_entries", include_nested)
    assert "embedded Git metadata" in {
        finding.reason for finding in quality_gate.scan_publishable_content(repository)
    }


def test_snapshot_preserves_modes_and_symlinks_and_is_cleaned_up(repository: Path) -> None:
    executable = repository / "tool.sh"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    link = repository / "tool-link"
    link.symlink_to("tool.sh")
    _git(repository, "add", "--", "tool.sh", "tool-link")

    with quality_gate.publishable_snapshot(repository) as snapshot:
        snapshot_path = snapshot
        assert (snapshot / "tool.sh").read_bytes() == executable.read_bytes()
        assert (snapshot / "tool.sh").stat().st_mode & 0o777 == 0o755
        assert (snapshot / "tool-link").is_symlink()
        assert os.readlink(snapshot / "tool-link") == "tool.sh"
    assert not snapshot_path.exists()
