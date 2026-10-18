# ZANE — a personal AI assistant for macOS

ZANE is an experimental, text-first assistant that can use a small set of validated tools on your Mac. It can open supported apps, report selected system information, and work with files only in folders you approve. It can also keep tasks and notes between sessions.

**ZANE is not an unrestricted computer-control agent.** It has no general shell, screen-clicking, file-delete, email, browser, or wake-word capability. Actions that change files or quit an app ask for approval. Read [the status ledger](docs/STATUS.md) for what has and has not been verified.

## What it can do

- Open supported installed apps; ask before quitting an app.
- Report battery, memory, and disk information on macOS.
- List, find, and read files in approved folders; create folders; move, rename, and write files with safeguards.
- Inspect registered software projects and, with approval, run their tests.
- Keep local tasks, reminders, and user-approved notes.
- Accept speech through macOS Dictation in voice mode and speak answers with the built-in `say` command.

The assistant routes every action through argument validation, folder/policy checks, user approval where required, execution timeouts, verification, and an audit log. ZANE deliberately has no shell tool or delete-file tool.

## Requirements

- macOS for app control and voice. Some read-only commands also run on other platforms, but this project is designed for macOS.
- Python 3.9 or newer.
- An Anthropic API key for free-form model reasoning. Without one, the deterministic direct commands work offline.
- Internet access for Anthropic model requests and for installing the Python dependency.

The SDK requirement is selected by Python version: Python 3.9 uses the compatible 0.x SDK line; Python 3.10+ uses the 1.x line.

## Install

Clone the repository and run the setup script:

```bash
git clone https://github.com/yngrage/zane-macos-assistant.git
cd zane-macos-assistant
bash scripts/setup.sh
```

Setup creates a project-local `.venv`, installs Python dependencies, creates a placeholder `.env` if needed, and prints an environment report. It does not install system software or use `sudo`.

## Configure Claude

On macOS, store your key in Keychain:

```bash
bash scripts/run.sh key set
bash scripts/run.sh key status
```

Enter the key only into the hidden prompt. Do not commit `.env` or share your key. The API key gives this program access to your Anthropic API account; model usage may incur charges under Anthropic's current pricing and account terms. See [Anthropic API pricing](https://docs.anthropic.com/en/docs/about-claude/pricing).

The default model is `claude-sonnet-5`. If it is unavailable to your account, set `ZANE_MODEL` in your local `.env` to a model ID currently available to you. Anthropic's model list changes over time; check [the official model overview](https://docs.anthropic.com/en/docs/about-claude/models/overview).

## Run it

Text mode:

```bash
bash scripts/run.sh
```

Voice mode:

1. In **System Settings → Keyboard → Dictation**, turn on Dictation and choose a shortcut.
2. Start ZANE:

   ```bash
   bash scripts/run.sh --voice
   ```
3. Focus the terminal prompt, use your Dictation shortcut, speak, and press Return. ZANE speaks its response. Type `/quit` to exit.

You can also double-click `Start ZANE Voice.command` in Finder.

**Dictation privacy:** macOS says Dictation sends information such as voice input, contacts, and location to Apple to process requests. ZANE does not record continuously or transmit raw audio to Anthropic. When a key is configured, recognized text and relevant conversation context are sent to Anthropic for model requests. Review Apple's Dictation and Anthropic's API privacy terms before use.

## Example requests

```text
open Safari
how much storage is available?
what is my battery level?
add a high-priority task to finish the robotics setup tomorrow
plan my next six hours
give me my daily briefing
remember that I prefer concise answers
```

Use `/help` inside ZANE for commands such as `/tasks`, `/briefing`, `/memory`, `/projects`, `/permissions`, `/audit`, and `/usage`.

To allow file access to another project, register its folder with an explicit approval:

```text
/projects add robotics ~/Projects/robotics
```

ZANE's default approved folder is `~/Downloads`. It does not get access to your whole Mac by default.

## Approvals and data

- Level 1 actions are limited to low-risk enabled tools.
- Level 2 actions ask for approval each time; declining is the default.
- Level 3 actions are blocked unless a specific tool is allow-listed by the user and then require typed confirmation.
- Files are limited to approved folders. Credential-looking files are refused and text reads redact secrets.
- ZANE's database, task data, logs, and exports are stored locally under `~/Library/Application Support/ZANE` with owner-only permissions.
- Conversation history is session-only; tasks, memories, and audit entries persist locally.
- No background daemon, launch-at-login item, menu bar app, desktop UI, or wake word is included.

For the complete tool list, controls, and known limitations, see [docs/STATUS.md](docs/STATUS.md). Development guidance is in [CLAUDE.md](CLAUDE.md).

## Development

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

The test suite uses fakes for GUI and system actions; passing tests do not prove every macOS behavior on every machine. See the status ledger for the recorded test environment and remaining macOS checks.

## License

See `LICENSE` for the terms under which you may use and redistribute this project.
