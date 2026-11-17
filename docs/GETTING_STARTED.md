# Install and run

## Install from source

On Linux, install Python 3.11+ with `venv`, Git, and Node.js 22.18+ with npm.
Run these commands from your [ProjectHub checkout](https://github.com/avnikonenko/ProjectHub).

```bash
bash scripts/install.sh
projecthub
```

Keep the checkout. If `projecthub` is outside your `PATH`, run `bash scripts/projecthub`.

## Install a wheel

[Download the v0.2.1 wheel](https://github.com/avnikonenko/ProjectHub/releases/download/v0.2.1/projecthub_local-0.2.1-py3-none-any.whl)
or [build one from source](README.md#build-a-wheel). The wheel includes the
interface and migrations and runs on Linux with Python 3.11+.

### With pipx

```bash
pipx install ./projecthub_local-0.2.1-py3-none-any.whl
projecthub
```

If the launcher is outside your `PATH`, run `pipx ensurepath` and reopen the
terminal.

### With Conda

```bash
conda create -n projecthub -c conda-forge python=3.12 pip
conda activate projecthub
python -m pip install ./projecthub_local-0.2.1-py3-none-any.whl
projecthub
```

Use your downloaded wheel's filename. Install Git for repository inspection.

## Start a project

1. Open [ProjectHub](http://127.0.0.1:8766) and click **Add project**. You can link a local Git repository.
2. In **Sources**, link a Codex session or import a public ChatGPT share.
3. For AI generation, install Codex and run `codex login`. In **Generation**, select **Overview** and **Knowledge**, then click **Regenerate selected**. See the [generation guide](AI_GENERATION.md).

Press Ctrl+C in the terminal to stop ProjectHub.

## Configuration and backups

- **Settings.** Run `projecthub config` for the file path. The default is `~/.config/projecthub/config.toml`. Environment variables take precedence at startup. Restart after file edits. [Example configuration](https://github.com/avnikonenko/ProjectHub/blob/main/config.example.toml)
- **Backups.** Source and wheel installations default to `~/.local/share/projecthub`. Back up that directory to preserve history, notes, and tasks.
