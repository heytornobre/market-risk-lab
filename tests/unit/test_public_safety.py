from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from tools.public_safety import APPROVED_IMAGE, scan_tree, validate_png


def _chunk(kind: bytes, content: bytes) -> bytes:
    checksum = zlib.crc32(kind + content) & 0xFFFFFFFF
    return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", checksum)


def _png(*, metadata: bool = False) -> bytes:
    header = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1440, 1000, 8, 2, 0, 0, 0))
    text = _chunk(b"tEXt", b"Author\x00Synthetic User") if metadata else b""
    raw = b"\x00" + b"\x00\x00\x00" * 1440
    image = _chunk(b"IDAT", zlib.compress(raw * 1000))
    return header + ihdr + text + image + _chunk(b"IEND", b"")


@pytest.mark.parametrize(
    ("relative", "content", "reason"),
    [
        ("notes.txt", ("/" + "Users" + "/sample/private.txt").encode(), "absolute user path"),
        ("linux.txt", ("/" + "home" + "/sample/private.txt").encode(), "absolute user path"),
        (
            "windows.txt",
            ("C:" + "\\Users" + "\\sample\\private.txt").encode(),
            "absolute user path",
        ),
        (
            "key.txt",
            b"-----BEGIN " + b"PRIVATE" + b" KEY-----",
            "private-key header",
        ),
        (
            "settings.txt",
            ("api" + "_key = " + "synthetic-placeholder").encode(),
            "credential assignment",
        ),
        ("state.db", b"", "database file"),
        ("brief.docx", b"", "office document"),
        ("bundle.zip", b"", "archive file"),
        ("payload.bin", b"\x00\xff", "unexpected binary file"),
    ],
)
def test_scanner_detects_prohibited_content(
    tmp_path: Path, relative: str, content: bytes, reason: str
) -> None:
    (tmp_path / relative).write_bytes(content)
    assert reason in {finding.reason for finding in scan_tree(tmp_path)}


def test_scanner_detects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("synthetic", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(target)
    assert "symlink" in {finding.reason for finding in scan_tree(tmp_path)}


def test_environment_examples_are_allowed_but_real_variants_are_not(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("SETTING=replace-me\n", encoding="utf-8")
    assert scan_tree(tmp_path) == []
    (tmp_path / ".env.local").write_text("SETTING=replace-me\n", encoding="utf-8")
    assert "non-example environment file" in {finding.reason for finding in scan_tree(tmp_path)}


def test_approved_screenshot_exception_is_valid() -> None:
    candidate = Path(__file__).parents[2]
    screenshot = candidate / APPROVED_IMAGE
    assert validate_png(screenshot.read_bytes()) == []


def test_invalid_png_and_sensitive_metadata_are_rejected() -> None:
    assert any("not a genuine PNG" in finding.reason for finding in validate_png(b"not png"))
    assert any("metadata chunk" in finding.reason for finding in validate_png(_png(metadata=True)))


def test_unexpected_image_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "other.png").write_bytes(_png())
    assert "unexpected image file" in {finding.reason for finding in scan_tree(tmp_path)}
