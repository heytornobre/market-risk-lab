"""Generic, public-safe release scanner with no confidential fund knowledge."""

from __future__ import annotations

import argparse
import re
import struct
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

APPROVED_IMAGE = PurePosixPath("docs/images/dashboard-synthetic.png")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
EXCLUDED_DIRECTORY_NAMES = frozenset(
    {".venv", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__", "build", "dist", "var"}
)
EXCLUDED_FILE_NAMES = frozenset({".coverage"})
DISALLOWED_DIRECTORY_NAMES = frozenset({"reports", "uploads", "artifacts"})
DATABASE_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})
OFFICE_SUFFIXES = frozenset({".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"})
ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".whl")
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"})
ALLOWED_PNG_CHUNKS = frozenset({b"IHDR", b"IDAT", b"IEND"})

_USER_PATH = re.compile(rb"(?:/(?:Users|home)/[^/\s]+|[A-Za-z]:\\Users\\[^\\\s]+)", re.IGNORECASE)
_KEY_HEADER = re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?" + b"PRIVATE" + rb" KEY-----")
_CREDENTIAL_ASSIGNMENT = re.compile(
    rb"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|client[_-]?secret)"
    rb"\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{8,}"
)
_CREDENTIAL_URL = re.compile(rb"(?i)https?://[^\s/:@]+:[^\s/@]+@[^\s]+")


@dataclass(frozen=True)
class Finding:
    path: str
    reason: str


def _is_excluded(relative: PurePosixPath) -> bool:
    return relative.name in EXCLUDED_FILE_NAMES or any(
        part in EXCLUDED_DIRECTORY_NAMES for part in relative.parts
    )


def _is_example_environment(path: PurePosixPath) -> bool:
    return path.name == ".env.example" or (
        path.name.startswith(".env.") and path.name.endswith(".example")
    )


def _generic_content_findings(path: str, content: bytes) -> list[Finding]:
    findings: list[Finding] = []
    rules = (
        (_USER_PATH, "absolute user path"),
        (_KEY_HEADER, "private-key header"),
        (_CREDENTIAL_ASSIGNMENT, "credential assignment"),
        (_CREDENTIAL_URL, "credential-bearing URL"),
    )
    for pattern, reason in rules:
        if pattern.search(content):
            findings.append(Finding(path, reason))
    return findings


def validate_png(content: bytes, path: str = str(APPROVED_IMAGE)) -> list[Finding]:
    if not content.startswith(PNG_SIGNATURE):
        return [Finding(path, "approved image is not a genuine PNG")]
    if len(content) < 33:
        return [Finding(path, "PNG is truncated")]
    width, height = struct.unpack(">II", content[16:24])
    findings: list[Finding] = []
    if not (800 <= width <= 2400 and 600 <= height <= 1800):
        findings.append(
            Finding(path, f"PNG dimensions are outside approved bounds: {width}x{height}")
        )
    position = 8
    seen_end = False
    while position + 12 <= len(content):
        length = struct.unpack(">I", content[position : position + 4])[0]
        chunk_type = content[position + 4 : position + 8]
        end = position + 12 + length
        if end > len(content):
            findings.append(Finding(path, "PNG contains a truncated chunk"))
            break
        if chunk_type not in ALLOWED_PNG_CHUNKS:
            findings.append(
                Finding(
                    path,
                    "PNG contains unexpected metadata chunk "
                    f"{chunk_type.decode('ascii', 'replace')}",
                )
            )
        position = end
        if chunk_type == b"IEND":
            seen_end = True
            break
    if not seen_end:
        findings.append(Finding(path, "PNG has no IEND chunk"))
    return findings


def _path_findings(relative: PurePosixPath, *, allow_archive: bool = False) -> list[Finding]:
    path = relative.as_posix()
    findings: list[Finding] = []
    if ".git" in relative.parts:
        findings.append(Finding(path, "embedded Git metadata"))
    if any(part in DISALLOWED_DIRECTORY_NAMES for part in relative.parts):
        findings.append(Finding(path, "generated report/upload/artifact path"))
    if relative.name.startswith(".env") and not _is_example_environment(relative):
        findings.append(Finding(path, "non-example environment file"))
    suffix = relative.suffix.lower()
    if suffix in DATABASE_SUFFIXES:
        findings.append(Finding(path, "database file"))
    if suffix in OFFICE_SUFFIXES:
        findings.append(Finding(path, "office document"))
    if not allow_archive and path.lower().endswith(ARCHIVE_SUFFIXES):
        findings.append(Finding(path, "archive file"))
    if suffix in IMAGE_SUFFIXES and relative != APPROVED_IMAGE:
        findings.append(Finding(path, "unexpected image file"))
    return findings


def scan_tree(root: Path) -> list[Finding]:
    root = root.resolve()
    findings: list[Finding] = []
    for path in sorted(root.rglob("*")):
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if _is_excluded(relative):
            continue
        if path.is_symlink():
            findings.append(Finding(relative.as_posix(), "symlink"))
            continue
        if not path.is_file():
            continue
        findings.extend(_path_findings(relative))
        content = path.read_bytes()
        if relative == APPROVED_IMAGE:
            findings.extend(validate_png(content, relative.as_posix()))
        else:
            try:
                content.decode("utf-8")
            except UnicodeDecodeError:
                findings.append(Finding(relative.as_posix(), "unexpected binary file"))
            else:
                findings.extend(_generic_content_findings(relative.as_posix(), content))
    return findings


def _archive_members(path: Path) -> list[tuple[str, bytes]]:
    if path.suffix == ".whl" or path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            return [
                (name, archive.read(name)) for name in archive.namelist() if not name.endswith("/")
            ]
    with tarfile.open(path, "r:*") as archive:
        members: list[tuple[str, bytes]] = []
        for member in archive.getmembers():
            if member.issym() or member.islnk():
                members.append((member.name, b"__SYMLINK__"))
            elif member.isfile():
                extracted = archive.extractfile(member)
                if extracted is not None:
                    members.append((member.name, extracted.read()))
        return members


def scan_archive(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        members = _archive_members(path)
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as error:
        return [Finding(path.name, f"invalid build archive: {error}")]
    for name, content in members:
        relative = PurePosixPath(name)
        findings.extend(_path_findings(relative, allow_archive=True))
        if content == b"__SYMLINK__":
            findings.append(Finding(name, "archive symlink"))
            continue
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(Finding(name, "unexpected binary in build archive"))
        else:
            findings.extend(_generic_content_findings(name, content))
    return findings


def _print_findings(findings: list[Finding]) -> int:
    if not findings:
        print("public-safety: PASS")
        return 0
    print("public-safety: FAIL", file=sys.stderr)
    for finding in findings:
        print(f"- {finding.path}: {finding.reason}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    tree_parser = subparsers.add_parser("tree")
    tree_parser.add_argument("root", type=Path, nargs="?", default=Path.cwd())
    archive_parser = subparsers.add_parser("archives")
    archive_parser.add_argument("paths", type=Path, nargs="+")
    arguments = parser.parse_args()
    if arguments.command == "tree":
        return _print_findings(scan_tree(arguments.root))
    findings = [finding for path in arguments.paths for finding in scan_archive(path)]
    return _print_findings(findings)


if __name__ == "__main__":
    raise SystemExit(main())
