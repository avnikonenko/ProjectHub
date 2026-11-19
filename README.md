# ProjectHub

[![CI status](https://github.com/avnikonenko/ProjectHub/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/avnikonenko/ProjectHub/actions/workflows/ci.yml)
[![Docs: Guides](https://img.shields.io/badge/docs-guides-2f7d5a)](https://projecthub.readthedocs.io/en/latest/)
[![License: MIT](https://img.shields.io/badge/license-MIT-007ec6)](LICENSE)

<img src="frontend/src/projecthub-logo.png" alt="ProjectHub logo" width="240" />

**Keep your project knowledge organized and accessible across AI conversations and development sessions.**

Working on long-running projects often means spreading important information across multiple Codex sessions, ChatGPT conversations, and Git repositories. Decisions get buried, explanations become difficult to find, and returning to a project requires reconstructing its history.

**ProjectHub** is a local workspace that brings this information together.

## What you can do

- **Preserve knowledge.** Capture decisions, requirements, reasoning, and unresolved questions with links to original conversations.
- **Understand your project.** Generate overviews, searchable knowledge, Q&A, and diagrams of architecture, workflows, and dependencies.
- **Explore previous work.** Search conversations to see why decisions were made. Inspect a linked Git repository to see how features were implemented.
- **Continue with context.** Preview relevant project knowledge before sending it to a Codex session so you can build on previous work.
- **Stay organized.** Manage projects, notes, TODOs, and related conversations in one workspace.

Designed for developers, researchers, and anyone working on long-running projects with AI assistance.

## What AI generates

Open **Generation** and choose what to make or update. **AI Q&A** uses linked
Codex sessions and imported ChatGPT chats to create questions and answers.
**Scheme** can use those conversations and read parts of a linked Git repository
to make diagrams. Use the links in saved results to see where the information came from.

| Saved result | What you get | When it helps |
| --- | --- | --- |
| **Project Overview** | A brief on the project's purpose, architecture, current work, and next steps. | “Where did we leave off?” |
| **Knowledge** | A summary plus searchable decisions, requirements, proposals, and open questions, with reasons and original evidence. | “Why did we choose SQLite?” |
| **AI Q&A** | A set of questions and source-linked answers drawn from earlier project discussions. | “What did we discuss about CSV and JSON exports?” |
| **Scheme** | Architecture and workflow diagrams with dependencies and supporting evidence. | “How does ProjectHub turn linked conversations into an overview?” |
| **Implementation Choices & Limits** | Code-backed findings about limits, defaults, filters, and fallbacks, with locations for their declarations and use. | “Where is import size capped, and what happens at the limit?” |

Use **Reviews → Ask Project** for a new question. Generate **AI tags** separately
to browse saved Knowledge and Q&A by topic.

**AI runs when you request it.** Opening projects and searching saved information do not trigger model requests.

![Overview of the ProjectHub project with example data](docs/images/projecthub-dashboard.png)

## Install and run

### Install a wheel

For most users, start with the [v0.2.3 wheel](https://github.com/avnikonenko/ProjectHub/releases/download/v0.2.3/projecthub_local-0.2.3-py3-none-any.whl).
It runs on Linux with **Python 3.11+** and includes the interface and database
migrations. Open a terminal in the download directory. Pick pipx or Conda and use
the filename you downloaded. [Other releases](https://github.com/avnikonenko/ProjectHub/releases)

**With pipx** ([install pipx](https://pipx.pypa.io/latest/how-to/install-pipx.html) if needed)

```bash
pipx install ./projecthub_local-0.2.3-py3-none-any.whl
projecthub
```

If the launcher is outside your PATH, run `pipx ensurepath` and reopen the terminal.
If you already have a source launcher named `projecthub`, you can try the wheel
in a separate Conda environment.

**With Conda**, if you already use it

```bash
conda create -n projecthub -c conda-forge python=3.12 pip
conda activate projecthub
python -m pip install ./projecthub_local-0.2.3-py3-none-any.whl
projecthub
```

Install Git for repository inspection.

### Install the latest code from Git

The `main` branch may include changes newer than the latest wheel release.
Requires **Linux, Python 3.11+ with venv, Git, and Node.js 22.18+ with npm**.

```bash
git clone https://github.com/avnikonenko/ProjectHub.git
cd ProjectHub
bash scripts/install.sh
projecthub
```

Keep the checkout for this installation. If `projecthub` is outside your PATH,
run `bash scripts/projecthub` from the checkout. See [Build a wheel](docs/DEVELOPER_HOME.md#build-a-wheel)
to package your own build.

Both routes use the same [saved data directory](#your-data). Open
[ProjectHub](http://127.0.0.1:8766) after starting it. Stop with Ctrl+C.

## Example: Get back up to speed on Project X

For Codex sessions or AI generation, install the [Codex CLI](https://learn.chatgpt.com/docs/codex/cli)
and run `codex login`. Local notes and browsing work without Codex.

1. Click **Add project**, name it **Project X**, and link its local Git repository if you have one.
2. In **Sources**, link a Codex session about Project X or use **Import shared chat** to add a past discussion.
3. In **Generation**, select **Overview** and **Knowledge**, then click **Regenerate selected**. Open **Overview** and **Knowledge → Knowledge Summary & records** to read your saved results and follow their citations.
4. When you return, open **Reviews → Ask Project** and ask “Why did we choose to validate records before importing them?” or “Which export options did we discuss, and what is still undecided?” With the optional Codex skill, you can also ask “Read ProjectHub context for Project X before we continue.”
5. In **Generation**, select **Scheme** and click **Regenerate selected**. Open **Scheme** to explore the architecture, workflow, and dependencies.

## Settings and AI

AI features use your existing **Codex ChatGPT sign-in**. No OpenAI API key is required. Open **AI settings** in the project header to choose a profile, model, and reasoning effort or edit project AI instructions.

In Settings, choose a colour palette for the Portfolio theme. Light/dark mode and text size can be adjusted independently.

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

[User guides and developer documentation](https://projecthub.readthedocs.io/en/latest/) · [Changelog](CHANGELOG.md) · [MIT license](LICENSE)

Made with Codex :)
