# ZANE status ledger

v0.1.0 - built and tested in a **Linux x86_64 sandbox (Python 3.12), not yet on a Mac**.

Rule for this file: a feature is *tested* only if an automated test or a recorded manual run exercised it. Everything else is listed as unverified or not built. Update it whenever that changes.

## Summary

- **275 automated tests pass, 1 skipped** (the live-API test, which I ran by hand and which passed). Three consecutive full runs were identical (no flakes). A clean-room install (`bash scripts/setup.sh` into a fresh venv, then the whole suite) also passed.
- **ZANE v1 is not complete.** The text-mode core, tool layer, permissions, memory and tasks exist. Basic terminal voice mode is now implemented with macOS Dictation input and `say` output; a desktop UI and menu bar do not.
- The macOS-specific tools were written to be verifiable but have **never run on a Mac**. Section 3 is the checklist.

## 1. V1 definition of done

| Requirement | Status |
|---|---|
| Launch on the MacBook Air M1 | **Unverified on a Mac** (setup + launch verified on Linux) |
| Natural-language text commands | Done, tested |
| Intelligent responses | Model loop tested with a scripted model; **never run against a real model** (no key here) |
| Speak to ZANE / hear spoken replies | Basic terminal mode added; uses macOS Dictation and `say`. Not yet verified interactively with a microphone. |
| Interrupt ZANE while speaking | **Not built** (state machine and cancel plumbing exist) |
| Open applications by text | Logic tested (exact argv, verification, injection attempts); **not run on a Mac**. Voice mode routes macOS Dictation text through the same CLI command handling; no separate voice action system is included |
| Basic validated file operations | Done, tested (list, find, read, mkdir, move, rename, write; no delete by design) |
| Real system information | Disk and process list: real, tested here. Battery and memory: parsers tested on fixtures **I wrote from knowledge of the formats, not captured from a Mac** |
| Inspect/troubleshoot projects | Done, tested (inspect, deps vs venv, git status/diff, run tests with approval) |
| Persistent tasks | Done, tested |
| Save/retrieve approved memories | Done, tested |
| Daily briefing | Done, tested |
| Approve/reject risky actions | Done, tested (terminal approver) |
| See what was executed | Done: `/audit` |
| Inspect failures + verification | Done: every result carries a status and a verification line |
| Restart without losing data | Tested (second app instance on the same database) |
| No unreasonable memory/storage | Measured on Linux, see section 5; **not measured on the M1** |

## 2. Verified here

Config and secrets (env > .env > Keychain order, key never in repr/logs), redaction, 3-level policy with persistence, approvals (default No, spoof-proof text), audit log, SQLite layer, memory (dedup, upsert, refuses credentials, relevance retrieval, export), tasks (priorities, deadlines, overdue, recurrence incl. month-end, search, export, briefing, planner), date parser, in-app reminders, intent router (22 positive and 18 negative phrasings), tool registry (validation, preflight, approval, watchdog timeout, cancellation, verification, truncation, platform gating), path guard (traversal, symlink escape, credential files), safe subprocess runner (argv only, timeout, cancel, env scrub), file tools, project tools (real `pytest`/`unittest` runs in temp projects, env-leak check), assistant loop (fast path, tool loop, approvals, untrusted-data fencing, provider failures, cancel, busy, step limit, history windowing, state transitions), Anthropic adapter (message conversion, response parsing, error classification), CLI/REPL, persistence, owner-only file permissions.

**Live check (manual):** the real `anthropic` SDK against the real API with a deliberately invalid key raised `AuthenticationError`; ZANE classified it `auth`, non-retryable, with no key in the message. This verifies the request path and error handling. It does **not** verify a successful completion, tool-use round trip, token accounting, or that `claude-sonnet-5` is accepted for your key.

## 3. Needs verification on your Mac (do these first)

1. `bash scripts/setup.sh` completes (arm64 wheels for the SDK's dependencies; Python version).
2. `bash scripts/run.sh doctor --raw`: compare each raw block with the parsed line above it: `pmset -g batt`, `sysctl kern.memorystatus_vm_pressure_level`, `sysctl vm.swapusage`, `memory_pressure` ("System-wide memory free percentage"), `ps -Ao pid,pcpu,pmem,rss,comm`. Any mismatch is a parser bug.
3. `.venv/bin/python -m pytest` on the Mac, ideally under both Apple's Python 3.9 and 3.12. (Static analysis says syntax needs only 3.8; it has never been executed on 3.9.)
4. App control: `open Safari`, `open VS Code`, `launch Terminal`, then `quit Safari` (approve). Expect "confirmed". **Specific risk:** on recent macOS Safari's process lives under a cryptex path; `is_running` checks both the nominal and the resolved path, but this is from memory. If Safari reports "NOT confirmed", compare with `ps -Ao comm | grep -i safari`.
5. The Automation prompt appears when quitting an app (allow it); `osascript` argument passing (`tell application (item 1 of argv) to quit`) works.
6. `bash scripts/run.sh key set` (typed into `security`'s own prompt), then `key status` shows "keychain".
7. Notifications: `add a task to test reminders`, then `task_remind` via the model or tests; or set a reminder 1 minute out and keep ZANE running. Needs Notifications/Automation permission for your terminal.
8. Files & Folders prompt when listing `~/Downloads`; `/projects add` on a real project.
9. With a real key: a conversation that needs tools ("check my disk and battery and tell me if anything needs attention"). Check `/usage`, confirm the model id is accepted (else set `ZANE_MODEL`), read the log for surprises.
10. Footprint: Activity Monitor RSS of the python process idle and during a model call.

## 4. Not built

Voice audition, custom push-to-talk STT, interruption, wake word - desktop UI, menu bar, global shortcut, launch-at-login - web research layer - local model provider - streaming responses - Apple Reminders registration - reading PDF/Word documents - persisted conversation history (memory, tasks, audit are persisted; chat history is per session) - prompt caching / tool selection to cut request size.

## 5. Measured footprint (Linux x86_64, indicative only)

| Measure | Value |
|---|---|
| Idle RSS, offline app with all tools and SQLite | ~19 MB |
| Startup | ~110 ms |
| RSS after the model path loads the SDK | ~69 MB |
| Virtualenv size (1 dependency + its dependencies) | ~74 MB |
| Source / tests | ~4,560 / ~1,810 lines |
| Tools registered / shown to the model on Linux | 37 / 32 |
| Tool definitions in each model request | ~14,000 characters (roughly 3-4k tokens) |

The last row is the obvious cost lever: send only the tools relevant to the request, or cache the tool block.

## 6. Phase 7 coverage

| Test area | Covered | Where |
|---|---|---|
| Application launching | Logic only (fakes) | test_applications |
| Valid and invalid commands | Yes | test_router, test_registry, test_applications |
| Permission denial | Yes | test_registry, test_cli_persistence |
| Missing API keys | Yes | test_assistant, test_provider_config |
| Network failures / model timeouts | Simulated (plus one live auth failure) | test_assistant |
| File access restrictions | Yes | test_schema_paths_runner, test_filesystem |
| Memory / task persistence, app restart | Yes | test_memory_tasks, test_cli_persistence |
| Voice interruption | **No (not built)** | - |
| Destructive-action prevention | Yes (no delete tool, no overwrite, confirmations) | test_filesystem, test_registry |
| Logging and secret handling | Yes | test_security, test_schema_paths_runner |

## 7. Known limitations

- Reminders are ZANE-only and fire only while ZANE runs.
- Cancelling a model call stops waiting but cannot abort the request in flight (it ends at the API timeout). Streaming would fix this.
- `ps` CPU values are averages, not live load.
- A monthly task due on the 29th-31st re-anchors to the shorter day after each completion (Jan 31 -> Feb 28 -> Mar 28).
- Memory search is keyword scoring, not embeddings (deliberate for 8 GB).
- The fast-path router matches English phrasings only.
- Default approved folder is `~/Downloads`; per-project approval is the intended way to grant more.
- Path checks are not defended against time-of-check/time-of-use races beyond exclusive file creation (acceptable for a single-user tool).

## 8. Recommendations for the remaining phases (unvalidated)

- **UI:** a menu-bar item (`rumps`) plus a `pywebview` window (WKWebView) hosting a local HTML command center, driven by `StateTracker` and an `Approver` that blocks on a dialog. Avoids Electron's RAM. Measure on the M1 before committing; PyObjC/AppKit is the fallback.
- **TTS:** start with `say`: audition with `say -v '?'` and listen; try Enhanced/Premium voices from System Settings. Run it as a non-blocking subprocess so stopping speech is `terminate()`. Choose the voice by ear; pitch tweaks have not been tested.
- **STT:** push-to-talk only. Apple's Speech framework via PyObjC, or a small whisper.cpp model run as a subprocess *only while the key is held*. Never record continuously.
- **Login item:** an opt-in launchd agent installed and removed by explicit, approved commands.
- **Research:** keep the provider replaceable; treat results as untrusted data like file content.
