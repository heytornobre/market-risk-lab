from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).parents[2] / ".github/workflows/ci.yml"
IMMUTABLE_ACTION = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def test_workflow_is_minimal_read_only_and_immutably_pinned() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow[True]) == {"push", "pull_request"}
    assert "schedule" not in workflow[True]
    assert workflow["concurrency"]["cancel-in-progress"] is True
    job = workflow["jobs"]["quality"]
    assert job["timeout-minutes"] == 20
    assert job["runs-on"] == "ubuntu-latest"
    uses = [step["uses"] for step in job["steps"] if "uses" in step]
    assert len(uses) == 4
    assert all(IMMUTABLE_ACTION.fullmatch(action) for action in uses)
    assert all(re.search(rf"{re.escape(action)}\s+#\s+v\d", text) for action in uses)
    assert "upload-artifact" not in text
    gitleaks = next(step for step in job["steps"] if "gitleaks-action" in step.get("uses", ""))
    assert gitleaks["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    assert gitleaks["env"]["GITLEAKS_ENABLE_COMMENTS"] == "false"
    assert gitleaks["env"]["GITLEAKS_ENABLE_UPLOAD_ARTIFACT"] == "false"
    assert "deployment" not in text.lower()
    assert "tools/quality_gate.py" in text
    assert 'python-version: "3.12"' in text
