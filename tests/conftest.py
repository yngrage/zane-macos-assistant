import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from helpers import Clock, FakeRunner  # noqa: E402
from zane.app.config import Config  # noqa: E402
from zane.app.lifecycle import build_app  # noqa: E402
from zane.security.approvals import ScriptedApprover  # noqa: E402


@pytest.fixture
def clock():
    return Clock(datetime(2026, 10, 14, 9, 0))  # a Wednesday


@pytest.fixture
def root(tmp_path):
    r = (tmp_path / "root").resolve()
    r.mkdir()
    return r


@pytest.fixture
def make_app(tmp_path, root, clock):
    created = []

    def make(*, platform="darwin", answers=None, default_answer=False, runner=None, provider=None, approved=None, **overrides):
        cfg = Config(data_dir=tmp_path / "data", approved_dirs=[root] if approved is None else approved, reminders=False)
        app = build_app(
            cfg, approver=ScriptedApprover(answers, default=default_answer), provider=provider,
            runner=runner if runner is not None else FakeRunner(), platform=platform, now=clock,
            **{"sleep": lambda s: None, **overrides},
        )
        created.append(app)
        return app

    yield make
    for a in created:
        try:
            a.close()
        except Exception:
            pass
