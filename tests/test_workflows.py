"""The pieces of the scheduled workflows that can be run here.

A workflow run that goes green without doing its job is this project's
recurring failure, so the shell that decides green or red is tested like the
rest.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / ".github" / "scripts" / "commit_and_push.sh"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def clone(tmp_path):
    """A bare 'origin' with one commit on main, and a working clone of it."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "--initial-branch=main", str(origin))
    work = tmp_path / "work"
    git(tmp_path, "clone", str(origin), str(work))
    git(work, "checkout", "-b", "main")
    (work / "data.csv").write_text("a\n")
    git(work, "add", "data.csv")
    git(work, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-m", "first")
    git(work, "push", "-u", "origin", "main")
    return origin, work


def run_script(work, message="snapshot"):
    env = {**os.environ, "PUSH_RETRY_SLEEP": "0"}
    return subprocess.run(["bash", str(SCRIPT), message], cwd=work, capture_output=True, text=True, env=env)


def test_a_staged_snapshot_is_committed_and_pushed(clone):
    origin, work = clone
    (work / "data.csv").write_text("a\nb\n")
    git(work, "add", "data.csv")
    r = run_script(work, "Google Scholar rolling snapshot 2026-11-23")
    assert r.returncode == 0, r.stderr
    assert "Google Scholar rolling snapshot 2026-11-23" in git(origin, "log", "--oneline", "main")


def test_nothing_staged_is_not_an_error(clone):
    _, work = clone
    r = run_script(work)
    assert r.returncode == 0 and "nothing new" in r.stdout


def test_main_having_moved_is_rebased_over(clone, tmp_path):
    origin, work = clone
    other = tmp_path / "other"
    git(tmp_path, "clone", str(origin), str(other))
    (other / "roster.csv").write_text("x\n")
    git(other, "add", "roster.csv")
    git(other, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-m", "an edit made during the run")
    git(other, "push", "origin", "main")
    (work / "data.csv").write_text("a\nb\n")
    git(work, "add", "data.csv")
    (work / "untracked-by-this-run.txt").write_text("left over\n")
    assert run_script(work).returncode == 0
    log = git(origin, "log", "--oneline", "main")
    assert "snapshot" in log and "an edit made during the run" in log


def test_a_push_that_cannot_be_made_fails_the_step(clone):
    """Both collector workflows ended their push loop with nothing after it: three
    rejections and the step still passed, so the run went green, the site was
    redeployed, and the week's readings were gone with the runner."""
    origin, work = clone
    git(work, "remote", "set-url", "origin", str(origin) + "-gone")
    (work / "data.csv").write_text("a\nb\n")
    git(work, "add", "data.csv")
    r = run_script(work)
    assert r.returncode == 1
    assert r.stdout.count("push rejected, retrying") == 3 and "::error::" in r.stdout


def test_the_collectors_use_the_shared_push_and_no_loop_of_their_own():
    for name in ("collect-scholar-weekly.yml", "collect-openalex.yml"):
        text = (REPO / ".github" / "workflows" / name).read_text()
        assert "commit_and_push.sh" in text, name
        assert "git push" not in text, f"{name} pushes on its own again"
