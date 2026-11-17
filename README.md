# ProjectHub

[![CI status](https://github.com/avnikonenko/ProjectHub/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/avnikonenko/ProjectHub/actions/workflows/ci.yml)
[![Docs: Guides](https://img.shields.io/badge/docs-guides-2f7d5a)](docs/README.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-007ec6)](LICENSE)

<img src="frontend/src/projecthub-logo.png" alt="ProjectHub logo" width="240" />

**Keep the context of a project across long AI conversations.**

When work is spread across several Codex sessions and ChatGPT chats, returning to a project means remembering what you tried, which approach you chose, and what still needs doing. **ProjectHub collects that history in one local workspace** and **helps turn it into a useful project overview, searchable explanations, and diagrams**.

Use it for software, research, or other projects with a growing conversation history.

## What you can do

- **Connect conversations.** Link terminal Codex sessions, import public ChatGPT chats, and browse saved messages with inherited duplicates collapsed.
- **Get up to speed.** Read the Project Overview for a quick update. Browse decisions, rationale, and trade-offs in Knowledge and generated answers in AI Q&A.
- **Generate and organize.** Refresh selected views from **Generation**. Saved results retain source links and previous versions. Generate AI tags separately to filter Knowledge and Q&A.
- **Explore the code.** Use Scheme to see architecture, workflows, and dependencies. Link a Git repository for read-only branch comparisons, recent commits, and public GitHub issues.
- **Keep track of work.** Pin projects, search saved content, capture Markdown notes, and manage TODOs.
- **Continue with context.** Use Ask Project for follow-up questions, or preview project context before sending it to a Codex session.

Choose the Codex account, model, reasoning effort, and response instructions for each project. In Settings, choose a colour palette for the Portfolio theme. Light/dark mode and text size can be adjusted independently.

**AI runs when you request it.** Opening projects and searching saved information do not trigger model requests.

![Overview of the ProjectHub project with example data](docs/images/projecthub-dashboard.png)

## Install and run

### Install from a source checkout

Requires **Linux, Python 3.11+ with venv, Git, and Node.js 22.18+ with npm**.
From a cloned or downloaded checkout:

```bash
bash scripts/install.sh
projecthub
```

Keep the checkout for this installation. If its launcher is outside your PATH,
run `bash scripts/projecthub` from the checkout.

### Optional: install a wheel

Check [GitHub Releases](https://github.com/avnikonenko/ProjectHub/releases) for
a wheel, or [build one from source](docs/README.md#build-a-wheel). The wheel
runs on Linux with **Python 3.11+** and bundles the built interface and database migrations.

**With pipx**

```bash
pipx install ./projecthub_local-0.2.1-py3-none-any.whl
projecthub
```

If the launcher is outside your PATH, run `pipx ensurepath` and reopen the terminal.
If you already have a source launcher named `projecthub`, you can try the wheel
in a separate Conda environment.

**With Conda**

```bash
conda create -n projecthub -c conda-forge python=3.12 pip
conda activate projecthub
python -m pip install ./projecthub_local-0.2.1-py3-none-any.whl
projecthub
```

Use the wheel filename you downloaded. Install Git for repository inspection and
Codex for AI features. Source and wheel installations use the same [saved data directory](#your-data).

Open **http://127.0.0.1:8766** after starting ProjectHub. Stop with Ctrl+C.

## Example: Get back up to speed on Project X

1. Click **Add project**, name it **Project X**, and link its local Git repository if you have one.
2. In **Sources**, link a Codex session about Project X or use **Import shared chat** to add a past discussion.
3. In **Generation**, select **Overview** and **Knowledge**, then click **Regenerate selected**. Read the saved result and follow its citations to the original discussions.
4. When you return, open **Reviews → Ask Project** and ask “Why did we choose to validate records before importing them?” or “Which export options did we discuss, and what is still undecided?” With the optional Codex skill, you can also ask “Read ProjectHub context for Project X before we continue.”
5. In **Generation**, select **Scheme** and click **Regenerate selected**. Open **Scheme** to explore the architecture, workflow, and dependencies.

## Settings and AI

AI features use your existing **Codex ChatGPT sign-in**. Install Codex separately and run `codex login`. No OpenAI API key is required. Open **AI settings** in the project header to choose a profile, model, and reasoning effort or edit project AI instructions.

ProjectHub runs locally. AI generation sends selected project context to OpenAI when requested. See [AI settings and generation](docs/AI_GENERATION.md) for progress, cancellation, and available settings.

### AI limitations

- **Long histories:** Analysis uses bounded context and may miss details. Follow source references to check conclusions.
- **AI accuracy:** Generated architecture and current-work summaries are interpretations, not proof that features work or scientific results are valid.

Open **Coverage** below a generated result to see what evidence was included and what was missed.

## Terminal-agent skill

The optional Codex skill lets a terminal agent read project context and manage notes,
TODOs, and session links on request. Run `projecthub install-skill` to enable it.
With ProjectHub running, open a new Codex session and try these prompts:

- “Read ProjectHub context for Project X.”
- “Save this explanation as a note in Project X.”
- “Add a TODO to Project X: test the import.”
- “Mark ‘test the import’ finished in Project X.”
- “Link this session to Project X.”
- “Unlink this session from Project X.”
- “Find all codex sessions related to X and link them to Project X.”

<details>
<summary>Optional terminal commands</summary>

Run these directly if you want to read the same project context outside a Codex session.

```bash
projecthub context --project 'Project X'
projecthub read --project 'Project X' --kind knowledge
```

</details>

See the [terminal-agent guide](docs/TERMINAL_AGENTS.md) for installation options and commands.

## Your data

Settings default to `~/.config/projecthub/config.toml`; project data to `~/.local/share/projecthub`. Back up the data directory to preserve saved history, notes, and tasks.

[User guides and developer documentation](docs/README.md) · [Changelog](CHANGELOG.md) · [MIT license](LICENSE)

Made with Codex :)
