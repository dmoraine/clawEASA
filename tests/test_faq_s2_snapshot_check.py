"""S2 — opt-in snapshot check script: skip / refusal guards, and the opt-in run."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "faq_s2_snapshot_check.py"


def _run(env_extra: dict, home: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "CLAW_EASA_S2_SNAPSHOT_COPY"}
    env.update(HOME=str(home), PYTHONPATH=str(ROOT / "src"), **env_extra)
    return subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=60)


def test_skips_without_env(tmp_path):
    result = _run({}, tmp_path)
    assert result.returncode == 0 and "skipped" in result.stdout


def test_refuses_the_real_data_dir(tmp_path):
    real = tmp_path / ".local" / "share" / "claw-easa"  # HOME is tmp_path: nothing real
    real.mkdir(parents=True)
    result = _run({"CLAW_EASA_S2_SNAPSHOT_COPY": str(real)}, tmp_path)
    assert result.returncode == 2 and "refusing" in result.stderr
    assert list(real.iterdir()) == []


@pytest.mark.snapshot
@pytest.mark.skipif(not os.environ.get("CLAW_EASA_S2_SNAPSHOT_COPY"),
                    reason="CLAW_EASA_S2_SNAPSHOT_COPY not set (opt-in snapshot check)")
def test_snapshot_copy_has_no_duplicate_texts():
    import importlib.util

    spec = importlib.util.spec_from_file_location("faq_s2_snapshot_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--compare-raw"]) == 0
