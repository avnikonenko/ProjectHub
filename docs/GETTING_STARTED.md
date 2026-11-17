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

Check [GitHub Releases](https://github.com/avnikonenko/ProjectHub/releases) for
a wheel, or [build one from source](README.md#build-a-wheel). The wheel runs on
Linux with Python 3.11 or later and includes the built interface and database
migrations. Use the filename of the wheel you downloaded or built.

### With pipx

```bash
pipx install ./projecthub_local-0.2.1-py3-none-any.whl
projecthub
```

If the launcher is outside your `PATH`, run `pipx ensurepath` and reopen the
terminal.

### With Conda

You can install the wheel alongside an existing source launcher using a named
Conda environment.

```bash
conda create -n projecthub -c conda-forge python=3.12 pip
conda activate projecthub
python -m pip install ./projecthub_local-0.2.1-py3-none-any.whl
projecthub
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

Source and wheel installations store project data in `~/.local/share/projecthub` by
default. Back up this directory to preserve saved history, notes, and tasks.
