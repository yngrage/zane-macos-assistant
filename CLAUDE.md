# CLAUDE.md - ZANE

ZANE is a personal AI operator for macOS: single user, MacBook Air M1, 8 GB RAM, macOS Sonoma. It plans, acts through validated tools, verifies results and reports honestly. **Read `docs/STATUS.md` first** - it is the source of truth for what is built, tested and unverified.

## Commands
- Setup: `bash scripts/setup.sh` · Run: `bash scripts/run.sh` (also `ask "..."`, `doctor --raw`, `key set`, `--offline`)
- Tests: `.venv/bin/python -m pytest` (~275 tests, ~5 s, no network). Live API check: `ZANE_NETWORK_TESTS=1 .venv/bin/python -m pytest -k live`
- Python 3.9-compatible syntax is required (Apple's system Python): no runtime `X | Y`, no `match`. Check: `vermin -t=3.9- zane`

## Non-negotiable rules
1. Never claim a feature works unless a test or a recorded run proves it. Update `docs/STATUS.md` when status changes.
2. **No shell strings.** Run commands with `ctx.run([...])` (argv arrays; `run_cmd` rejects strings). Child env is scrubbed of secrets. Never put secrets in argv.
3. Every action goes through `ToolRegistry.execute`. Never call a tool handler directly from the assistant or a UI.
4. Security controls (permissions, approved folders, audit clear) are **user-only** (slash commands / UI). Never expose them as tools.
5. No delete tool, no silent overwrite, no `sudo`, no bypassing macOS prompts or Gatekeeper.
6. Tool output derived from files, command output, git or the web is `untrusted=True`. Policy must never depend on the model resisting injected text.
7. Keep the footprint small: one dependency, no multiple models resident, no always-on background work.

## Adding a tool
`Tool(name, description, schema, handler, level, category, preflight, describe, verify, platforms)` in `zane/tools/<module>.py`, registered in that module's `register()`.
- `preflight` refuses *before* asking the user (path guard, existence, policy).
- `describe` builds the approval text from **validated args only** (level 2+).
- `verify` re-checks the real effect and returns `Verification(True|False|None, detail)`. A tool must never report `ok` on an unconfirmed effect.
- Test with `tests/helpers.py::FakeRunner` (asserts argv is a list of strings) and the `make_app` fixture. Assert the exact argv.

## Map
`app/` config, composition (`build_app`), CLI, doctor · `ai/` provider interface + Anthropic adapter · `core/` assistant loop, intent router (fast path), context/persona, state, cancel · `tools/` registry, paths, runner, tool modules · `security/` policy, approvals, audit, redaction, keychain · `memory/` SQLite + memory service · `tasks/` task manager, dates, reminders.

## Next work, in order
1. Verify on the real Mac: `docs/STATUS.md` section 3. Fix parser/verification mismatches found by `doctor --raw`.
2. Run a real conversation with a key; confirm the model id and the tool-use round trip.
3. Verify basic terminal voice mode (`voice/`): macOS Dictation text input and `say` replies; interactive microphone behavior is not yet verified. Future custom STT should be push-to-talk only.
4. Phase 6 UI + menu bar: implement an `Approver` that blocks the worker thread on a dialog; subscribe to `StateTracker`.
5. Cut request size (tool selection by intent or cached tool block), then streaming.
