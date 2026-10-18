import os
import sys
import threading

import pytest

from zane.core.cancel import CancelToken
from zane.tools.paths import PathGuard, PathRefused, sensitive_reason
from zane.tools.runner import run_cmd
from zane.tools.schema import I, S, apply_defaults, obj, validate
from pathlib import Path

SCHEMA = obj({"name": S("n", minLength=1, maxLength=5), "n": I("n", minimum=1, maximum=3, default=2), "mode": S("m", enum=["a", "b"])}, ["name"])


# ---- schema ----------------------------------------------------------------------------------
def test_schema_accepts_valid():
    assert validate(SCHEMA, {"name": "ab", "n": 3, "mode": "a"}) == []


def test_schema_rejects_missing_unknown_and_bad_types():
    assert any("name: required" in e for e in validate(SCHEMA, {}))
    assert any("unknown field" in e for e in validate(SCHEMA, {"name": "a", "extra": 1}))
    assert any("expected integer" in e for e in validate(SCHEMA, {"name": "a", "n": "2"}))


def test_schema_bool_is_not_an_integer():
    assert validate(SCHEMA, {"name": "a", "n": True})


def test_schema_bounds_enum_and_length():
    assert validate(SCHEMA, {"name": "abcdef"})
    assert validate(SCHEMA, {"name": "a", "n": 9})
    assert validate(SCHEMA, {"name": "a", "mode": "zzz"})


def test_schema_defaults():
    assert apply_defaults(SCHEMA, {"name": "a"})["n"] == 2


# ---- path guard ------------------------------------------------------------------------------
def guard(*roots):
    return PathGuard(lambda: list(roots))


def test_path_inside_root_allowed(root):
    (root / "a.txt").write_text("x")
    assert guard(root).resolve(str(root / "a.txt")) == root / "a.txt"


def test_path_traversal_rejected(root):
    with pytest.raises(PathRefused):
        guard(root).resolve(str(root / ".." / ".." / "etc" / "passwd"))


def test_symlink_escape_rejected(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "s.txt").write_text("secret")
    os.symlink(outside, root / "link")
    with pytest.raises(PathRefused):
        guard(root).resolve(str(root / "link" / "s.txt"))


@pytest.mark.parametrize("bad", ["relative/path.txt", "", "   ", "/tmp/x\x00y"])
def test_bad_paths_rejected(root, bad):
    with pytest.raises(PathRefused):
        guard(root).resolve(bad)


def test_no_roots_means_no_access(tmp_path):
    with pytest.raises(PathRefused):
        guard().resolve(str(tmp_path))


def test_roots_are_dynamic(root, tmp_path):
    roots = [root]
    g = PathGuard(lambda: roots)
    other = (tmp_path / "other").resolve()
    other.mkdir()
    with pytest.raises(PathRefused):
        g.resolve(str(other))
    roots.append(other)
    assert g.resolve(str(other)) == other


@pytest.mark.parametrize("name,flagged", [(".env", True), (".env.local", True), (".env.example", False), ("id_rsa", True),
                                          ("id_rsa.pub", False), ("server.pem", True), ("notes.txt", False), ("credentials.json", True)])
def test_sensitive_names(name, flagged):
    assert bool(sensitive_reason(Path("/p") / name)) is flagged


def test_credential_directories_are_sensitive():
    assert sensitive_reason(Path("/home/u/.ssh/config"))
    assert sensitive_reason(Path("/p/.git/config"))


def test_new_root_validation(tmp_path):
    with pytest.raises(PathRefused):
        PathGuard.validate_new_root("/")
    with pytest.raises(PathRefused):
        PathGuard.validate_new_root(Path.home())
    with pytest.raises(PathRefused):
        PathGuard.validate_new_root(tmp_path / "does-not-exist")
    good = tmp_path / "proj"
    good.mkdir()
    assert PathGuard.validate_new_root(good) == good.resolve()


# ---- runner ----------------------------------------------------------------------------------
def test_runner_captures_output():
    r = run_cmd([sys.executable, "-c", "print('hi')"], timeout=10)
    assert r.ok and r.stdout.strip() == "hi"


def test_runner_refuses_shell_strings():
    with pytest.raises(TypeError):
        run_cmd("echo hi")  # type: ignore[arg-type]


def test_runner_timeout_kills():
    r = run_cmd(["sleep", "5"], timeout=0.3)
    assert r.timed_out and not r.ok


def test_runner_cancel_kills():
    token = CancelToken()
    threading.Timer(0.2, token.cancel).start()
    r = run_cmd(["sleep", "5"], timeout=10, cancel=token)
    assert r.cancelled


def test_runner_command_not_found():
    r = run_cmd(["definitely-not-a-command-zane"])
    assert not r.ok and "not found" in r.error


def test_runner_does_not_leak_secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak-1234567890")
    monkeypatch.setenv("MY_SERVICE_TOKEN", "tok-should-not-leak")
    r = run_cmd([sys.executable, "-c", "import os;print(os.environ.get('ANTHROPIC_API_KEY'), os.environ.get('MY_SERVICE_TOKEN'))"], timeout=10)
    assert r.stdout.strip() == "None None"
