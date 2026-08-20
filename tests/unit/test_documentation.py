from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")


def test_local_markdown_links_resolve() -> None:
    markdown_files = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
    for markdown in markdown_files:
        for target in MARKDOWN_LINK.findall(markdown.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            path = (markdown.parent / target.split("#", 1)[0]).resolve()
            assert path.exists(), f"broken link in {markdown.relative_to(ROOT)}: {target}"


def test_architecture_contains_structurally_complete_mermaid_diagrams() -> None:
    architecture = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
    diagrams = re.findall(r"```mermaid\n(.*?)\n```", architecture, flags=re.DOTALL)
    assert len(diagrams) == 2
    assert diagrams[0].startswith("flowchart LR\n")
    assert diagrams[1].startswith("stateDiagram-v2\n")
    assert diagrams[0].count("subgraph ") == diagrams[0].count("\n    end")
    assert all("[*]" in diagram or "-->" in diagram for diagram in diagrams)


def test_public_landing_page_has_no_phase_or_scaffold_language() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8").casefold()
    assert "local pre-release" not in readme
    assert "current slice" not in readme
    assert "scaffold" not in readme
