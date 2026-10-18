import os

import pytest


def ex(app, tool, **args):
    return app.registry.execute(tool, args)


def test_list_hides_dotfiles_puts_folders_first_and_is_untrusted(make_app, root):
    (root / "b.txt").write_text("hello")
    (root / "sub").mkdir()
    (root / ".hidden").write_text("x")
    app = make_app()
    r = ex(app, "fs_list", path=str(root))
    lines = r.output.splitlines()
    assert lines[1] == "sub/" and lines[2].startswith("b.txt") and ".hidden" not in r.output and r.untrusted
    assert ".hidden" in ex(app, "fs_list", path=str(root), show_hidden=True).output
    assert ex(app, "fs_list", path=str(root.parent)).status == "blocked"
    assert ex(app, "fs_list", path=str(root / "b.txt")).status == "failed"


def test_find_is_bounded_and_skips_heavy_and_hidden_dirs(make_app, root):
    for p in ("a/b/c.py", "node_modules/x.py", ".hidden/y.py", "z.txt", "a/d.py"):
        (root / p).parent.mkdir(parents=True, exist_ok=True)
        (root / p).write_text("x")
    app = make_app()
    found = ex(app, "fs_find", path=str(root), extension="py").data["matches"]
    assert sorted(os.path.relpath(f, root) for f in found) == ["a/b/c.py", "a/d.py"]
    assert len(ex(app, "fs_find", path=str(root), extension=".py", limit=1).data["matches"]) == 1
    assert ex(app, "fs_find", path=str(root), name="z.*").data["matches"] == [str(root / "z.txt")]
    assert ex(app, "fs_find", path=str(root)).status == "failed"


def test_read_head_tail_binary_truncation_and_redaction(make_app, root):
    (root / "big.log").write_text("".join(f"line {i:04d}\n" for i in range(500)))
    (root / "bin.dat").write_bytes(b"\x00\x01\x02" * 100)
    (root / "cfg.txt").write_text("API_KEY=abc123secret\nhello world\n")
    app = make_app()
    head = ex(app, "fs_read", path=str(root / "big.log"), max_bytes=1000)
    tail = ex(app, "fs_read", path=str(root / "big.log"), max_bytes=1000, tail=True)
    assert "line 0000" in head.output and "line 0499" not in head.output and "first 1000 of" in head.output
    assert "line 0499" in tail.output and "line 0000" not in tail.output and tail.untrusted
    assert ex(app, "fs_read", path=str(root / "bin.dat")).status == "failed"
    cfg = ex(app, "fs_read", path=str(root / "cfg.txt"))
    assert "abc123secret" not in cfg.output and "hello world" in cfg.output and "redacted" in cfg.output


@pytest.mark.parametrize("name", [".env", ".env.production", "id_rsa", "server.pem", "credentials.json"])
def test_credential_files_are_never_read_even_inside_an_approved_folder(make_app, root, name):
    (root / name).write_text("TOP SECRET")
    r = ex(make_app(), "fs_read", path=str(root / name))
    assert r.status == "blocked" and "TOP SECRET" not in r.output + r.error


def test_traversal_and_symlink_escapes_are_refused(make_app, root, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("outside")
    os.symlink(secret, root / "link.txt")
    app = make_app()
    assert ex(app, "fs_read", path=str(root / ".." / "secret.txt")).status == "blocked"
    assert ex(app, "fs_read", path=str(root / "link.txt")).status == "blocked"
    assert ex(app, "fs_read", path="/etc/passwd").status == "blocked"


def test_mkdir(make_app, root):
    app = make_app()
    r = ex(app, "fs_mkdir", path=str(root / "new" / "deep"))
    assert r.ok and (root / "new" / "deep").is_dir() and "confirmed" in r.verification
    assert ex(app, "fs_mkdir", path=str(root / "new")).status == "blocked"
    assert ex(app, "fs_mkdir", path=str(root.parent / "x")).status == "blocked"


def test_write_new_file_needs_approval_and_is_verified(make_app, root):
    app = make_app(answers=[True, False])
    p = root / "new.txt"
    r = ex(app, "fs_write", path=str(p), content="hello")
    assert r.ok and p.read_text() == "hello" and "confirmed" in r.verification
    assert (app.approver.requests[0].action, app.approver.requests[0].target) == ("Create new file", str(p))
    q = root / "declined.txt"
    assert ex(app, "fs_write", path=str(q), content="x").status == "denied" and not q.exists()


def test_overwrite_is_never_silent_and_keeps_a_backup(make_app, root):
    p = root / "a.txt"
    p.write_text("old")
    app = make_app(answers=[True])
    blocked = ex(app, "fs_write", path=str(p), content="new")
    assert blocked.status == "blocked" and p.read_text() == "old" and app.approver.requests == []
    r = ex(app, "fs_write", path=str(p), content="new", overwrite=True)
    assert r.ok and p.read_text() == "new"
    baks = list(root.glob("a.txt.zane-bak-*"))
    assert len(baks) == 1 and baks[0].read_text() == "old"
    assert app.approver.requests[0].action == "OVERWRITE existing file" and "backup" in app.approver.requests[0].consequences.lower()


def test_write_refuses_credential_files_directories_and_missing_parents(make_app, root):
    (root / "d").mkdir()
    app = make_app(default_answer=True)
    for path in (root / ".env", root / "d", root / "nope" / "x.txt", root.parent / "x.txt"):
        assert ex(app, "fs_write", path=str(path), content="x").status == "blocked", path
    assert app.approver.requests == []


def test_move_flow_with_approval_and_verification(make_app, root):
    (root / "a.txt").write_text("x")
    (root / "sub").mkdir()
    app = make_app(answers=[True, False])
    r = ex(app, "fs_move", source=str(root / "a.txt"), destination=str(root / "sub" / "a.txt"))
    assert r.ok and (root / "sub" / "a.txt").exists() and not (root / "a.txt").exists() and "confirmed" in r.verification
    assert "->" in app.approver.requests[0].target
    assert ex(app, "fs_move", source=str(root / "sub" / "a.txt"), destination=str(root / "back.txt")).status == "denied"
    assert (root / "sub" / "a.txt").exists()


def test_move_never_overwrites_and_never_leaves_the_approved_area(make_app, root, tmp_path):
    (root / "a.txt").write_text("A")
    (root / "b.txt").write_text("B")
    (root / "dir").mkdir()
    os.symlink(root / "a.txt", root / "ln")
    app = make_app(default_answer=True)
    cases = [
        (root / "a.txt", root / "b.txt"),  # destination exists
        (root / "a.txt", tmp_path / "out.txt"),  # destination outside
        (tmp_path / "x", root / "x"),  # source outside
        (root / "ln", root / "ln2"),  # symlink source
        (root / "dir", root / "dir" / "inner"),  # folder into itself
        (root, root / "moved"),  # the approved root itself
        (root / "a.txt", root / "missing-dir" / "a.txt"),  # destination folder missing
    ]
    for src, dst in cases:
        assert ex(app, "fs_move", source=str(src), destination=str(dst)).status == "blocked", (src, dst)
    assert app.approver.requests == [] and (root / "b.txt").read_text() == "B" and (root / "a.txt").read_text() == "A"


def test_rename(make_app, root):
    (root / "a.txt").write_text("x")
    (root / "b.txt").write_text("y")
    app = make_app(default_answer=True)
    for bad in ("sub/a.txt", "..", ".", "", "a\\b", "b.txt"):
        assert ex(app, "fs_rename", path=str(root / "a.txt"), new_name=bad).status in ("blocked", "invalid"), bad
    assert (root / "a.txt").exists() and app.approver.requests == []
    r = ex(app, "fs_rename", path=str(root / "a.txt"), new_name="renamed.txt")
    assert r.ok and (root / "renamed.txt").exists() and not (root / "a.txt").exists()


def test_credential_files_cannot_be_moved_or_renamed_either(make_app, root):
    (root / ".env").write_text("A=1")
    app = make_app(default_answer=True)
    assert ex(app, "fs_rename", path=str(root / ".env"), new_name="notes.txt").status == "blocked"
    assert ex(app, "fs_move", source=str(root / ".env"), destination=str(root / "x.txt")).status == "blocked"
