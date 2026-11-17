# Install and run

## Install from source

On Linux, install Python 3.11 or later with `venv`, Git, and Node.js 22.18 or
later with npm. Clone or download the repository, then run the installer from
the checkout. [ProjectHub on GitHub](https://github.com/avnikonenko/ProjectHub)

```bash
bash scripts/install.sh
projecthub
```

Keep the checkout. If the launcher is outside your `PATH`, use
`bash scripts/projecthub` from the checkout.

## Install a wheel

If you have a ProjectHub wheel, you can install it on Linux with Python 3.11 or
later. The wheel includes the built interface and database migrations. Use the
filename of your downloaded or [locally built wheel](README.md#build-a-wheel).

```bash
pipx install ./projecthub_local-0.2.1-py3-none-any.whl
projecthub
```

If the launcher is outside your `PATH`, run `pipx ensurepath` and reopen the
terminal. To keep a wheel installation alongside an existing source launcher, use
a separate virtual environment and run its launcher directly.

```bash
python3 -m venv projecthub-venv
./projecthub-venv/bin/python -m pip install ./projecthub_local-0.2.1-py3-none-any.whl
./projecthub-venv/bin/projecthub
```

Install Git separately to inspect local repositories from a wheel installation.

## Start a project

1. Open `http://127.0.0.1:8766` after starting ProjectHub.
2. Click **Add project** and optionally link its local Git repository.
3. In **Sources**, link a Codex session or add a public ChatGPT shared conversation. You can then browse and search the saved history.
4. To generate a summary, install Codex separately and sign in with `codex login`. Then open **Generation**, select **Overview** and **Knowledge**, and click **Regenerate selected**. See the [AI generation guide](AI_GENERATION.md) for settings, progress, and coverage.

Press Ctrl+C in the terminal to stop ProjectHub.

## Configuration and backups

Run `projecthub config` to locate the editable settings file, which defaults to
`~/.config/projecthub/config.toml`. Environment variables override file settings
at startup. Restart ProjectHub after editing the file. See the
[configuration example](https://github.com/avnikonenko/ProjectHub/blob/main/config.example.toml).

Both installation methods store project data in `~/.local/share/projecthub` by
default. Back up this directory to preserve saved history, notes, and tasks.
