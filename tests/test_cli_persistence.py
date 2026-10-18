import os

import pytest

from zane.app.config import Config
from zane.app.lifecycle import build_app
from zane.app.main import main
from zane.security.approvals import ScriptedApprover
from helpers import FakeRunner


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """Tests must not depend on the developer's real .env, environment or Keychain."""
    monkeypatch.setenv("ZANE_NO_DOTENV", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("zane.app.config.keychain_get", lambda *a, **k: None)


def run(tmp_path, lines, *args):
    out, it = [], iter(lines)

    def fake_input(prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    code = main(["--home", str(tmp_path / "home"), "--offline", *args], input_fn=fake_input, out=out.append)
    return code, "\n".join(out)


def test_one_shot_ask_gives_real_system_information(tmp_path):
    code, out = run(tmp_path, [], "ask", "how much storage is available?")
    assert code == 0 and "GB free of" in out


def test_session_creates_lists_and_audits_tasks(tmp_path):
    code, out = run(tmp_path, ["add a high-priority task to write the docs tomorrow", "/tasks", "/audit", "give me my daily briefing", "/quit"])
    assert code == 0 and "ZANE 0.1.0 online" in out and "offline mode" in out
    assert "Task created: #1 [HIGH] Write the docs" in out and "(tomorrow)" in out
    assert "task_add" in out and "ok" in out and "Briefing -" in out


def test_session_asks_before_remembering_and_default_is_no(tmp_path):
    _, out = run(tmp_path, ["remember that I prefer concise answers", "", "remember that I like tea", "y", "/memory", "/quit"])
    assert out.count("APPROVAL REQUIRED") == 2 and "Not executed: approval was declined." in out
    assert "Saved as memory #1" in out
    assert out.splitlines()[-1] == "#1 [preference] I like tea" and "#2" not in out  # only the approved note was stored


def test_audit_clear_needs_confirmation_and_leaves_a_trace(tmp_path):
    _, out = run(tmp_path, ["how much storage is available?", "/audit clear", "n", "/audit clear", "y", "/audit", "/quit"])
    assert "Not cleared." in out and "Cleared 1 entries." in out and "user:audit clear" in out


def test_permissions_are_user_controlled_and_enforced(tmp_path):
    _, out = run(tmp_path, ["/permissions disable tasks", "show me all overdue tasks", "/permissions enable tasks", "show me all overdue tasks", "/permissions", "/quit"])
    assert "Blocked: The 'tasks' permission is disabled" in out and "No tasks match." in out and "tasks         enabled" in out


def test_models_cannot_change_permissions_or_the_audit_log(tmp_path):
    app = build_app(Config(data_dir=tmp_path / "d", reminders=False), provider=None, runner=FakeRunner(), platform="darwin")
    for name in app.registry.names():
        assert not any(w in name for w in ("permission", "audit", "policy", "approve")), name
    app.close()


def test_project_registration_grants_and_revokes_file_access(tmp_path):
    proj = tmp_path / "robotics"
    proj.mkdir()
    (proj / "notes.txt").write_text("servo calibration notes")
    _, out = run(tmp_path, [f"/projects add robotics {proj}", "y", "/projects", f"/projects forget robotics", "y", "/quit"])
    assert "Registered 'robotics'" in out and f"robotics - {proj}" in out and "no longer has access" in out
    _, out2 = run(tmp_path, ["/projects add root /", "/quit"])
    assert "Refused: That directory is too broad" in out2


def test_doctor_reports_environment_and_smoke_tests_tools(tmp_path):
    code, out = run(tmp_path, [], "doctor")
    assert code == 0 and "environment report" in out and "Tool smoke test" in out and "NOT configured" in out
    for tool in ("sys_dev_env", "sys_disk", "sys_battery", "sys_memory", "sys_processes"):
        assert tool in out


def test_key_status_never_prints_the_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-printed-by-mistake-123456")
    out = []
    main(["--home", str(tmp_path / "h"), "key", "status"], out=out.append)
    assert out == ["API key: configured via environment"]


def test_data_survives_an_application_restart(tmp_path, clock):
    cfg = Config(data_dir=tmp_path / "d", approved_dirs=[tmp_path], reminders=False)
    a1 = build_app(cfg, approver=ScriptedApprover([True]), provider=None, runner=FakeRunner(), platform="darwin", now=clock)
    a1.registry.execute("task_add", {"title": "survive restart", "priority": "high", "due": "tomorrow"})
    a1.registry.execute("memory_save", {"content": "Restart test fact"})
    a1.policy.disable("development")
    a1.close()
    a2 = build_app(cfg, approver=ScriptedApprover(), provider=None, runner=FakeRunner(), platform="darwin", now=clock)
    assert [t.title for t in a2.tasks.list_tasks()] == ["survive restart"]
    assert [m.content for m in a2.memory.list()] == ["Restart test fact"]
    assert not a2.policy.is_enabled("development") and a2.audit.count() == 2
    a2.close()


def test_data_directory_and_database_are_owner_only(tmp_path):
    cfg = Config(data_dir=tmp_path / "private", reminders=False)
    app = build_app(cfg, provider=None, runner=FakeRunner(), platform="darwin")
    app.close()
    assert oct(os.stat(cfg.data_dir).st_mode & 0o777) == "0o700" and oct(os.stat(cfg.db_path).st_mode & 0o777) == "0o600"
