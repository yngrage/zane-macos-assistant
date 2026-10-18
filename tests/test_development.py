import sys

import pytest

from zane.tools.runner import run_cmd
from helpers import FakeRunner

PASSING = "def test_ok():\n    assert 1 + 1 == 2\n"
FAILING = "def test_bad():\n    assert 1 + 1 == 3\n"
MARKER = "from pathlib import Path\n\ndef test_marker():\n    Path(__file__).with_name('ran.marker').write_text('x')\n"


def make_venv(project, packages=()):
    py = project / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    py.chmod(0o755)
    (project / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\nversion = 3.12.4\n")
    sp = project / ".venv" / "lib" / "python3.12" / "site-packages"
    sp.mkdir(parents=True)
    for p in packages:
        (sp / f"{p}-1.0.0.dist-info").mkdir()


def project(root, name="proj", body=PASSING, venv=True, packages=()):
    p = root / name
    (p / "tests").mkdir(parents=True)
    (p / "tests" / "test_sample.py").write_text(body)
    if venv:
        make_venv(p, packages)
    return p


def real(make_app, **kw):
    return make_app(runner=run_cmd, platform=sys.platform, **kw)


def test_inspect_reports_structure_venv_and_missing_requirements(make_app, root):
    p = project(root, packages=("pytest", "numpy", "typing_extensions"))
    (p / "requirements.txt").write_text("requests>=2\npytest\n# comment\n-r other.txt\ngit+https://x/y.git\nnumpy; python_version>'3.8'\nTyping-Extensions==4\n")
    (p / "README.md").write_text("# hi")
    r = make_app().registry.execute("dev_project_inspect", {"path": str(p)})
    assert r.ok and r.untrusted
    for expected in ("Markers: requirements.txt, README.md", "Virtualenv: .venv (Python 3.12.4)", "Requirements: 4 declared, 1 missing",
                     "1 requirement(s) not installed in .venv: requests", "Not a git repository"):
        assert expected in r.output, expected
    assert "No tests/ directory" not in r.output


def test_inspect_flags_a_missing_virtualenv(make_app, root):
    p = project(root, venv=False)
    (p / "requirements.txt").write_text("requests\n")
    out = make_app().registry.execute("dev_project_inspect", {"path": str(p)}).output
    assert "No virtual environment" in out and "python3 -m venv .venv" in out


def test_inspect_outside_approved_dirs_is_refused(make_app, tmp_path):
    assert make_app().registry.execute("dev_project_inspect", {"path": str(tmp_path)}).status == "blocked"


def test_git_status_uses_exact_safe_argv(make_app, root):
    p = project(root)
    fr = FakeRunner().when(["git"], stdout="## main...origin/main\n M a.py\n")
    r = make_app(runner=fr).registry.execute("dev_git_status", {"path": str(p)})
    assert r.ok and "## main" in r.output and r.untrusted
    assert fr.calls[0] == ["git", "-c", "core.fsmonitor=false", "-C", str(p), "status", "--short", "--branch"]
    assert fr.kwargs[0]["env"] == {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}


def test_git_status_on_a_non_repo_fails_cleanly(make_app, root):
    fr = FakeRunner().when(["git"], returncode=128, stderr="fatal: not a git repository (or any of the parent directories): .git\n")
    r = make_app(runner=fr).registry.execute("dev_git_status", {"path": str(project(root))})
    assert r.status == "failed" and "not a git repository" in r.error


def test_git_diff_disables_external_diff_and_textconv(make_app, root):
    fr = FakeRunner().when(["git"], stdout=" a.py | 2 +-\n")
    make_app(runner=fr).registry.execute("dev_git_diff", {"path": str(project(root)), "staged": True})
    argv = fr.calls[0]
    assert argv[argv.index("diff"):] == ["diff", "--no-ext-diff", "--no-textconv", "--cached", "--stat"]


def test_run_tests_passing_requires_approval_and_is_verified(make_app, root):
    p = project(root, body=MARKER)
    app = real(make_app, answers=[True])
    r = app.registry.execute("dev_run_tests", {"path": str(p)})
    assert r.ok and "Tests passed: 1 passed" in r.output and "confirmed: 1 passed" in r.verification
    assert (p / "tests" / "ran.marker").exists()
    req = app.approver.requests[0]
    assert req.level == 2 and "pytest" in req.action and "your user permissions" in req.consequences and str(p) in req.target


def test_run_tests_declined_executes_nothing(make_app, root):
    p = project(root, body=MARKER)
    r = real(make_app, answers=[False]).registry.execute("dev_run_tests", {"path": str(p)})
    assert r.status == "denied" and not (p / "tests" / "ran.marker").exists()


def test_failing_tests_are_reported_as_failures_not_success(make_app, root):
    r = real(make_app, answers=[True]).registry.execute("dev_run_tests", {"path": str(project(root, body=FAILING))})
    assert r.status == "failed" and "Tests failed (exit 1): 1 failed" in r.error and "assert 1 + 1 == 3" in r.output and r.untrusted


def test_no_tests_collected_is_reported(make_app, root):
    p = project(root, body="x = 1\n")
    r = real(make_app, answers=[True]).registry.execute("dev_run_tests", {"path": str(p)})
    assert r.status == "failed" and "No tests were collected" in r.error


def test_run_tests_refuses_without_a_project_venv_and_never_asks(make_app, root):
    app = real(make_app, default_answer=True)
    r = app.registry.execute("dev_run_tests", {"path": str(project(root, venv=False))})
    assert r.status == "blocked" and "virtual environment" in r.error and app.approver.requests == []


def test_run_tests_only_accepts_allowlisted_runners(make_app, root):
    r = make_app().registry.execute("dev_run_tests", {"path": str(project(root)), "runner": "bash -c 'rm -rf ~'"})
    assert r.status == "invalid"


def test_unittest_runner(make_app, root):
    p = project(root, body="import unittest\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertTrue(True)\n")
    (p / "tests" / "__init__.py").write_text("")
    r = real(make_app, answers=[True]).registry.execute("dev_run_tests", {"path": str(p), "runner": "unittest"})
    assert r.ok and "Ran 1 test" in r.output and "OK" in r.output


def test_test_runs_do_not_inherit_api_keys(make_app, root, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-leak-check-1234567890")
    body = ("import os\nfrom pathlib import Path\n\ndef test_env():\n"
            "    Path(__file__).with_name('env.txt').write_text(str(os.environ.get('ANTHROPIC_API_KEY')))\n")
    p = project(root, body=body)
    assert real(make_app, answers=[True]).registry.execute("dev_run_tests", {"path": str(p)}).ok
    assert (p / "tests" / "env.txt").read_text() == "None"
