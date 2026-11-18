# Install and run

## Install a wheel

For most users, start with the [v0.2.1 wheel](https://github.com/avnikonenko/ProjectHub/releases/download/v0.2.1/projecthub_local-0.2.1-py3-none-any.whl).
It includes the interface and migrations and runs on Linux with Python 3.11+.
Open a terminal in the download directory. Pick pipx or Conda and use the
filename you downloaded.

### With pipx

[Install pipx](https://pipx.pypa.io/latest/how-to/install-pipx.html) if needed.

```bash
pipx install ./projecthub_local-0.2.1-py3-none-any.whl
projecthub
```

If the launcher is outside your `PATH`, run `pipx ensurepath` and reopen the
terminal.

### With Conda

Use this option if you already use Conda.

```bash
conda create -n projecthub -c conda-forge python=3.12 pip
conda activate projecthub
python -m pip install ./projecthub_local-0.2.1-py3-none-any.whl
projecthub
```

Install Git for repository inspection. Contributors can also
[build a wheel](README.md#build-a-wheel).

## Install from source

On Linux, install Python 3.11+ with `venv`, Git, and Node.js 22.18+ with npm.
Run these commands from your [ProjectHub checkout](https://github.com/avnikonenko/ProjectHub).

```bash
bash scripts/install.sh
projecthub
```

Keep the checkout. If `projecthub` is outside your `PATH`, run `bash scripts/projecthub`.

## Start a project

For Codex sessions or AI generation, install the [Codex CLI](https://learn.chatgpt.com/docs/codex/cli)
and run `codex login` before linking a session or requesting generation. Local
notes and browsing work without Codex.

1. Open [ProjectHub](http://127.0.0.1:8766) and click **Add project**. You can link a local Git repository.
2. In **Sources**, link a Codex session or import a public ChatGPT share.
3. In **Generation**, select **Overview** and **Knowledge**, then click **Regenerate selected**. See the [generation guide](AI_GENERATION.md).
4. Open **Overview** and **Knowledge → Knowledge Summary & records** to read your saved results. Follow citations to check the original evidence.

Press Ctrl+C in the terminal to stop ProjectHub.

## Configuration and backups

- **Settings.** Run `projecthub config` for the file path. The default is `~/.config/projecthub/config.toml`. Environment variables take precedence at startup. Restart after file edits. [Example configuration](https://github.com/avnikonenko/ProjectHub/blob/main/config.example.toml)
- **Backups.** Source and wheel installations default to `~/.local/share/projecthub`. Back up that directory to preserve history, notes, and tasks.
