"""Synthetic clocks and commands only: no model, quote, ledger or scheduler effects."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.weekday_task import JOBS, KST, run_weekday


@pytest.mark.parametrize("day", [31, 1])
@pytest.mark.parametrize("job", list(JOBS))
def test_weekend_does_not_start_a_child(job, day):
    now = datetime(2026, 10 if day == 31 else 11, day, 12, tzinfo=KST)
    def forbidden(*args, **kwargs):
        pytest.fail("Weekend started a child process")
    assert run_weekday(now, job=job, run=forbidden) == 0


def test_weekend_boundary_uses_kst_not_utc():
    # Friday UTC is already Saturday in Korea. Even a batch must be skipped.
    assert run_weekday(datetime(2026, 10, 30, 15, tzinfo=timezone.utc), batch="missing.bat",
                       run=lambda *a, **k: pytest.fail("Weekend batch ran")) == 0


@pytest.mark.parametrize("hour, minute, expected_calls", [
    (3, 59, 0), (4, 0, 1), (15, 59, 1), (16, 0, 0),
])
@pytest.mark.parametrize("use_batch", [False, True])
def test_operating_hours_use_kst_for_modules_and_batches(tmp_path, hour, minute, expected_calls, use_batch):
    batch = tmp_path / "test.bat"
    batch.write_text("@exit /b 0", encoding="ascii")
    now = datetime(2026, 11, 4, hour, minute, tzinfo=KST).astimezone(timezone.utc)
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    command = {"batch": str(batch)} if use_batch else {"job": "public_quotes"}
    assert run_weekday(now, root=tmp_path, run=execute, **command) == 0
    assert len(calls) == expected_calls


def test_weekday_preserves_module_arguments_and_exit_code(tmp_path):
    calls = []
    def execute(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=17)
    assert run_weekday(datetime(2026, 11, 2, 7, 30, tzinfo=KST), "morning_retry",
                       root=tmp_path, run=execute) == 17
    assert calls[0][0][-4:] == ["-B", "-m", "tools.morning_retry_once", "--execute"]
    assert calls[0][1]["cwd"] == tmp_path.resolve()


def test_weekday_batch_path_with_spaces_and_traversal(tmp_path):
    batch = tmp_path / "test batch.bat"
    batch.write_text("@exit /b 0", encoding="ascii")
    calls = []
    def execute(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)
    now = datetime(2026, 11, 2, 15, 45, tzinfo=KST)
    assert run_weekday(now, batch=str(batch), root=tmp_path, run=execute) == 0
    assert calls[0][-3:] == ["/d", "/c", str(batch)]
    with pytest.raises(ValueError, match="project-root"):
        run_weekday(now, batch=str(tmp_path.parent / "outside.bat"), root=tmp_path, run=execute)


def test_naive_clock_rejected():
    with pytest.raises(ValueError, match="timezone"):
        run_weekday(datetime(2026, 11, 2), "public_quotes")
