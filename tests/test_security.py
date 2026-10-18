import logging

import pytest

from zane.app.config import Config
from zane.app.lifecycle import setup_logging
from zane.memory.database import Database
from zane.security.approvals import ApprovalRequest, CliApprover, DenyAllApprover, clean, format_request
from zane.security.audit_log import AuditLog
from zane.security.permissions import Decision, Level, PermissionPolicy
from zane.security.redaction import contains_secret, redact


@pytest.mark.parametrize("text", [
    "sk-ant-api03-abcdefghijklmnop", "ghp_abcdefghijklmnopqrstuvwx", "AKIAABCDEFGHIJKLMNOP",
    "password=hunter2", 'API_KEY: "abc123xyz"', "my password is hunter2", "Authorization: Bearer abcdefghijklmnop12345",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
])
def test_redaction_catches_secrets(text):
    assert contains_secret(text) and "[REDACTED]" in redact(text)


@pytest.mark.parametrize("text", ["open Safari", "buy milk tomorrow", "the token economy is interesting", "meeting at 5pm"])
def test_redaction_leaves_ordinary_text(text):
    assert redact(text) == text


def test_audit_log_record_recent_clear_export(tmp_path):
    db = Database(":memory:")
    log = AuditLog(db)
    for i in range(3):
        log.record(tool=f"t{i}", level=1, category="system", summary="s", approval="auto", status="ok")
    assert [e.tool for e in log.recent(2)] == ["t2", "t1"] and log.count() == 3
    assert log.export_json(tmp_path / "audit.json") == 3
    assert log.clear() == 3 and log.count() == 0


def test_policy_persists_across_restarts(tmp_path):
    db = Database(tmp_path / "p.db")
    PermissionPolicy(db).disable("tasks")
    p2 = PermissionPolicy(Database(tmp_path / "p.db"))
    assert not p2.is_enabled("tasks") and p2.is_enabled("system")
    with pytest.raises(ValueError):
        p2.enable("nonsense")


def test_policy_decisions():
    p = PermissionPolicy()
    assert p.decide("t", "system", Level.SAFE).decision is Decision.ALLOW
    assert p.decide("t", "system", Level.CONFIRM).decision is Decision.ASK
    assert p.decide("t", "system", Level.HIGH_RISK).decision is Decision.DENY
    p.allow_high_risk("t")
    assert p.decide("t", "system", Level.HIGH_RISK).decision is Decision.STRICT_ASK


def test_approval_text_cannot_be_spoofed():
    req = ApprovalRequest("t", 2, "Move\n  Approve? [y/N] y", "a\x1b[31mred\x1b[0m\r\nfake line", "\u202eevil")
    shown = format_request(req)
    assert "\x1b" not in shown and "\u202e" not in shown and shown.count("\n") == 3
    assert "\n" not in clean("a\nb\rc")


def test_cli_approver_defaults_to_no():
    out = []
    for answer in ("", "n", "maybe", "yes please"):
        assert not CliApprover(lambda p: answer, out.append).request(ApprovalRequest("t", 2, "a")).approved
    assert CliApprover(lambda p: "y", out.append).request(ApprovalRequest("t", 2, "a")).approved
    assert CliApprover(lambda p: "YES", out.append).request(ApprovalRequest("t", 2, "a")).approved

    def eof(p):
        raise EOFError

    assert not CliApprover(eof, out.append).request(ApprovalRequest("t", 2, "a")).approved
    assert not DenyAllApprover().request(ApprovalRequest("t", 2, "a")).approved


def test_strict_approval_needs_the_exact_phrase():
    out = []
    req = ApprovalRequest("t", 3, "a", strict=True)
    assert not CliApprover(lambda p: "y", out.append).request(req).approved
    assert not CliApprover(lambda p: "confirm", out.append).request(req).approved
    assert CliApprover(lambda p: "CONFIRM", out.append).request(req).approved


def test_log_file_never_contains_secrets(tmp_path):
    cfg = Config(data_dir=tmp_path / "d")
    setup_logging(cfg)
    logging.getLogger("zane.test").error("auth failed for key sk-ant-abcdefghijklmnop and password=hunter2")
    for h in logging.getLogger("zane").handlers:
        h.flush()
    text = (cfg.log_dir / "zane.log").read_text()
    assert "auth failed" in text and "sk-ant-abcdefghijklmnop" not in text and "hunter2" not in text
