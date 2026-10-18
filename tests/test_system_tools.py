import sys

import pytest

import zane.tools.system_info as si
from zane.tools.runner import run_cmd
from helpers import FakeRunner

BATT = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=21495907)\t87%; discharging; 5:12 remaining present: true\n"


@pytest.mark.parametrize("text,pct,state,remaining", [
    (BATT, 87, "discharging", "5:12"),
    ("Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t64%; charging; 1:07 remaining present: true\n", 64, "charging", "1:07"),
    ("Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged; 0:00 remaining present: true\n", 100, "charged", "0:00"),
    ("Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t55%; discharging; (no estimate) present: true\n", 55, "discharging", "calculating"),
    ("Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t99%; finishing charge; 0:05 remaining present: true\n", 99, "finishing charge", "0:05"),
])
def test_parse_battery(text, pct, state, remaining):
    info = si.parse_battery(text)
    assert (info["percent"], info["state"], info["remaining"]) == (pct, state, remaining)


def test_battery_tool(make_app):
    fr = FakeRunner().when(["pmset", "-g", "batt"], stdout=BATT)
    r = make_app(runner=fr).registry.execute("sys_battery", {})
    assert r.ok and r.output == "Battery 87% - discharging, 5:12 remaining (power source: Battery Power)"
    assert fr.calls == [["pmset", "-g", "batt"]]


def test_battery_tool_on_a_desktop_and_on_failure(make_app):
    desktop = FakeRunner().when(["pmset"], stdout="Now drawing from 'AC Power'\n")
    assert "No battery" in make_app(runner=desktop).registry.execute("sys_battery", {}).output
    broken = FakeRunner()  # pmset missing
    r = make_app(runner=broken).registry.execute("sys_battery", {})
    assert r.status == "failed" and "not found" in r.error


def test_memory_tool(make_app):
    fr = (FakeRunner().when(["sysctl", "-n", "hw.memsize"], stdout="8589934592\n")
          .when(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], stdout="1\n")
          .when(["sysctl", "-n", "vm.swapusage"], stdout="total = 1024.00M  used = 311.25M  free = 712.75M  (encrypted)\n")
          .when(["memory_pressure"], stdout="The system has 8589934592 (2097152 pages)\nSystem-wide memory free percentage: 54%\n"))
    r = make_app(runner=fr).registry.execute("sys_memory", {})
    assert r.ok and r.output == "Memory pressure: normal | RAM 8.0 GB | swap used 311 MB of 1024 MB | system-wide free memory 54%"
    crit = FakeRunner().when(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], stdout="4\n")
    assert "critical" in make_app(runner=crit).registry.execute("sys_memory", {}).output
    assert make_app(runner=FakeRunner()).registry.execute("sys_memory", {}).status == "failed"


PS = """  PID  %CPU %MEM      RSS COMM
    1   0.1  0.1     1024 /sbin/launchd
  123  42.0  3.0   245760 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
  456   1.0 10.5   860160 /Applications/Safari.app/Contents/MacOS/Safari
  789  17.5  1.0    51200 /usr/libexec/something
"""


def test_processes_tool_with_fixture(make_app):
    r = make_app(runner=FakeRunner().when(["ps", "-Ao", "pid,pcpu,pmem,rss,comm"], stdout=PS)).registry.execute("sys_processes", {"limit": 2})
    top_cpu, top_mem = r.data["top_cpu"], r.data["top_mem"]
    assert [p["command"] for p in top_cpu] == ["Google Chrome", "something"] and [p["command"] for p in top_mem] == ["Safari", "Google Chrome"]
    assert "Activity Monitor" in r.output


def test_real_system_tools_work_on_this_machine(make_app):
    app = make_app(runner=run_cmd, platform=sys.platform)
    disk = app.registry.execute("sys_disk", {})
    assert disk.ok and disk.data["total_gb"] > 0 and 0 <= disk.data["free_pct"] <= 100
    procs = app.registry.execute("sys_processes", {"limit": 3})
    assert procs.ok and procs.data["top_mem"]


def test_dev_env_flags_apple_python_and_missing_tools(make_app, monkeypatch):
    monkeypatch.setattr(si.platform, "machine", lambda: "x86_64")
    fr = (FakeRunner().when(["/usr/bin/python3", "--version"], stdout="Python 3.9.6\n").when(["/usr/bin/git", "--version"], stdout="git version 2.39.5\n")
          .when(["xcode-select", "-p"], returncode=2, stderr="xcode-select: error").when(["sysctl", "-n", "hw.optional.arm64"], stdout="1\n"))
    which = {"python3": "/usr/bin/python3", "git": "/usr/bin/git"}.get
    r = make_app(runner=fr, which=which).registry.execute("sys_dev_env", {})
    assert r.ok
    for expected in ("python3: Python 3.9.6", "git: git version 2.39.5", "brew: not found", "node: not found",
                     "Apple's system Python", "Rosetta", "xcode-select --install"):
        assert expected in r.output, expected
