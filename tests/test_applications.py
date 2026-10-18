import os
from pathlib import Path

import pytest

from helpers import FakeRunner


@pytest.fixture
def apps(tmp_path):
    d = tmp_path / "Applications"
    d.mkdir()
    for n in ("Safari", "Terminal", "Visual Studio Code", "Google Chrome", "Activity Monitor", "Notes", "Numbers"):
        (d / f"{n}.app").mkdir()
    return d


def running(*paths):
    return "\n".join(f"{os.path.realpath(p)}/Contents/MacOS/{Path(p).stem}" for p in paths) + "\n"


def runner_with(apps, *running_apps):
    return FakeRunner().when(["open"]).when(["ps", "-Ao", "comm"], stdout=running(*[apps / f"{a}.app" for a in running_apps]))


def test_open_uses_exact_argv_and_verifies_against_the_process_list(make_app, apps):
    fr = runner_with(apps, "Safari")
    r = make_app(runner=fr, app_dirs=[apps]).registry.execute("app_open", {"name": "safari"})
    assert r.ok and r.output == "Opened Safari." and "confirmed: Safari is running" in r.verification
    assert ["open", "-a", str(apps / "Safari.app")] in fr.calls


def test_aliases_and_unique_prefixes(make_app, apps):
    fr = runner_with(apps, "Visual Studio Code")
    app = make_app(runner=fr, app_dirs=[apps])
    assert app.registry.execute("app_open", {"name": "VS Code"}).ok
    assert ["open", "-a", str(apps / "Visual Studio Code.app")] in fr.calls
    assert app.registry.execute("app_open", {"name": "activity"}).status in ("ok", "unverified")


def test_unknown_and_ambiguous_apps_are_refused_without_running_anything(make_app, apps):
    fr = runner_with(apps)
    app = make_app(runner=fr, app_dirs=[apps])
    assert app.registry.execute("app_open", {"name": "Photoshop"}).status == "blocked"
    amb = app.registry.execute("app_open", {"name": "N"})
    assert amb.status == "blocked" and "ambiguous" in amb.error and "Notes" in amb.error and "Numbers" in amb.error
    assert fr.calls == []


@pytest.mark.parametrize("evil", ["Safari; rm -rf ~", "-a", "../../System/Applications/Calculator", "Safari\nQuit", "$(reboot)",
                                  "`id`", "Safari && echo hi", "", "Safari | cat", "/Applications/Safari.app"])
def test_injection_shaped_names_never_reach_a_command(make_app, apps, evil):
    fr = runner_with(apps, "Safari")
    r = make_app(runner=fr, app_dirs=[apps]).registry.execute("app_open", {"name": evil})
    assert r.status in ("blocked", "invalid") and fr.calls == []


def test_open_that_does_not_produce_a_running_app_is_not_reported_as_success(make_app, apps):
    r = make_app(runner=runner_with(apps), app_dirs=[apps]).registry.execute("app_open", {"name": "Safari"})
    assert r.status == "unverified" and "NOT confirmed" in r.verification and not r.ok


def test_open_when_the_process_list_is_unreadable_is_unverified(make_app, apps):
    fr = FakeRunner().when(["open"])  # no `ps` rule -> command not found
    r = make_app(runner=fr, app_dirs=[apps]).registry.execute("app_open", {"name": "Safari"})
    assert r.status == "unverified" and "could not verify" in r.verification


def test_open_failure_is_reported(make_app, apps):
    fr = FakeRunner().when(["open"], returncode=1, stderr="Unable to find application")
    r = make_app(runner=fr, app_dirs=[apps]).registry.execute("app_open", {"name": "Safari"})
    assert r.status == "failed" and "open failed" in r.error


def test_open_with_a_path_only_for_safe_targets(make_app, apps, root):
    proj = root / "proj"
    proj.mkdir()
    (root / "notes.txt").write_text("hi")
    script = root / "tool"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    (root / "run.sh").write_text("echo hi")
    (root / "x.command").write_text("echo hi")
    (root / ".env").write_text("A=1")
    fr = runner_with(apps, "Visual Studio Code")
    app = make_app(runner=fr, app_dirs=[apps])
    for ok in (proj, root / "notes.txt"):
        assert app.registry.execute("app_open", {"name": "Visual Studio Code", "path": str(ok)}).ok
        assert ["open", "-a", str(apps / "Visual Studio Code.app"), str(ok)] in fr.calls
    n = len(fr.calls)
    for bad in (script, root / "run.sh", root / "x.command", root / ".env", root.parent):
        assert app.registry.execute("app_open", {"name": "Terminal", "path": str(bad)}).status == "blocked", bad
    assert len(fr.calls) == n  # nothing was launched for the refused ones


def quit_runner(apps, *, closes):
    state = {"running": True}

    def ps(argv):
        from zane.tools.runner import CmdResult

        return CmdResult(0, running(apps / "Safari.app") if state["running"] else "")

    def osa(argv):
        from zane.tools.runner import CmdResult

        if closes:
            state["running"] = False
        return CmdResult(0)

    return FakeRunner().on(["ps", "-Ao", "comm"], ps).on(["osascript"], osa)


def test_quit_asks_first_passes_the_name_only_as_argv_and_verifies(make_app, apps):
    fr = quit_runner(apps, closes=True)
    app = make_app(runner=fr, app_dirs=[apps], answers=[True])
    r = app.registry.execute("app_quit", {"name": "Safari"})
    assert r.ok and "no longer running" in r.verification
    osa = next(c for c in fr.calls if c[0] == "osascript")
    assert osa == ["osascript", "-e", "on run argv", "-e", "tell application (item 1 of argv) to quit", "-e", "end run", "Safari"]
    assert "Safari" not in " ".join(osa[:-1])
    assert app.approver.requests[0].action == "Quit application" and app.approver.requests[0].level == 2


def test_quit_that_does_not_take_effect_is_reported_honestly(make_app, apps):
    r = make_app(runner=quit_runner(apps, closes=False), app_dirs=[apps], answers=[True]).registry.execute("app_quit", {"name": "Safari"})
    assert r.status == "unverified" and "still running" in r.verification


def test_quit_declined_does_nothing(make_app, apps):
    fr = quit_runner(apps, closes=True)
    r = make_app(runner=fr, app_dirs=[apps], answers=[False]).registry.execute("app_quit", {"name": "Safari"})
    assert r.status == "denied" and not any(c[0] == "osascript" for c in fr.calls)


def test_quit_does_not_launch_an_app_that_is_not_running(make_app, apps):
    fr = runner_with(apps)  # nothing running
    r = make_app(runner=fr, app_dirs=[apps], answers=[True]).registry.execute("app_quit", {"name": "Safari"})
    assert r.ok and "not running" in r.output and not any(c[0] == "osascript" for c in fr.calls)


def test_application_tools_are_macos_only(make_app, apps):
    app = make_app(platform="linux", app_dirs=[apps])
    for name, args in (("app_open", {"name": "Safari"}), ("app_quit", {"name": "Safari"}), ("folder_open", {"location": "downloads"})):
        assert app.registry.execute(name, args).status == "unavailable"


def test_folder_open(make_app, root):
    (root / "proj").mkdir()
    (root / "f.txt").write_text("x")
    fr = FakeRunner().when(["open"])
    app = make_app(runner=fr)
    assert app.registry.execute("folder_open", {"location": "downloads"}).ok
    assert ["open", str(Path.home() / "Downloads")] in fr.calls
    assert app.registry.execute("folder_open", {"path": str(root / "proj")}).ok
    assert app.registry.execute("folder_open", {"path": str(root.parent)}).status == "blocked"
    assert app.registry.execute("folder_open", {"path": str(root / "f.txt")}).status == "blocked"
    assert app.registry.execute("folder_open", {}).status == "blocked"
    assert app.registry.execute("folder_open", {"location": "root"}).status == "invalid"
