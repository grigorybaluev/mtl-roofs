"""The eval-smoke command line must actually run and leave a report (#53).

It once passed for months because ``python -m mtl_roofs.cli`` imported the module and
exited 0 without calling the app. These tests run the real task string from
``pixi.toml``, so a no-op entry point cannot come back unnoticed.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _smoke_command(out: Path, fixtures: Path | None = None) -> list[str]:
    """The ``eval-smoke`` task from pixi.toml, with this interpreter and a temp ``--out``."""
    tasks = tomllib.loads((ROOT / "pixi.toml").read_text())["tasks"]
    argv = shlex.split(tasks["eval-smoke"])
    assert argv[0] == "python"
    argv[0] = sys.executable
    argv[argv.index("--out") + 1] = str(out)
    if fixtures is not None:
        argv[argv.index("--fixtures") + 1] = str(fixtures)
    return argv


def test_module_entry_point_runs_the_app() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "mtl_roofs", "version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "mtl-roofs" in result.stdout


def test_eval_smoke_task_writes_a_report(tmp_path: Path) -> None:
    out = tmp_path / "smoke"
    subprocess.run(_smoke_command(out), cwd=ROOT, check=True, capture_output=True)
    report = (out / "report.md").read_text()
    assert "eval-smoke" in report


def test_eval_smoke_fails_on_a_regression(tmp_path: Path) -> None:
    """Once the baseline has numbers, the guard must be able to fail."""
    fixtures = tmp_path / "fixtures"
    shutil.copytree(ROOT / "tests" / "fixtures", fixtures)
    path = fixtures / "baseline_metrics.json"
    baseline = json.loads(path.read_text())
    baseline["metrics"]["overall"]["vertical_rmse_m"] = 0.0  # unreachable
    path.write_text(json.dumps(baseline))
    out = tmp_path / "smoke"
    result = subprocess.run(_smoke_command(out, fixtures), cwd=ROOT, capture_output=True)
    assert result.returncode != 0
    assert "Regression" in (out / "report.md").read_text()
