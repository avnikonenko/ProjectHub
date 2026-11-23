# ProjectHub

[![CI status](https://github.com/avnikonenko/ProjectHub/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/avnikonenko/ProjectHub/actions/workflows/ci.yml)
[![Docs: Guides](https://img.shields.io/badge/docs-guides-2f7d5a)](https://projecthub.readthedocs.io/en/latest/)
[![License: MIT](https://img.shields.io/badge/license-MIT-007ec6)](LICENSE)

<img src="frontend/src/projecthub-logo.png" alt="ProjectHub logo" width="240" />

**Keep your project knowledge organized and accessible across AI conversations and development sessions.**

Working on long-running projects often means spreading important information across Codex and Claude Code sessions, ChatGPT and Claude chats, and Git repositories. Decisions get buried, explanations become difficult to find, and returning to a project requires reconstructing its history.

**ProjectHub** is a local workspace that brings this information together.

## What you can do

- **Preserve knowledge.** Capture decisions, requirements, reasoning, and unresolved questions with links to original conversations.
- **Understand your project.** Generate overviews, searchable knowledge, Q&A, and diagrams of architecture, workflows, and dependencies.
- **Explore previous work.** Search conversations to see why decisions were made. Inspect a linked Git repository to see how features were implemented.
- **Continue with context.** Use the optional ProjectHub skill to bring relevant project knowledge into Codex or Claude Code sessions.
- **Stay organized.** Manage projects, notes, TODOs, and related conversations in one workspace.

Designed for developers, researchers, and anyone working on long-running projects with AI assistance.

## What AI generates

Choose **Codex** or **Claude Code** in the project’s **AI settings**. Open
**Generate** and choose what to make or update. **AI Q&A** uses linked sessions
and imported conversations to create questions and answers.
**Scheme** can use those conversations and read parts of a linked Git repository
to make diagrams. Use the links in saved results to see where the information came from.

| Saved result | What you get | When it helps |
| --- | --- | --- |
| **Project Overview** | A brief on the project's purpose, architecture, current work, and next steps. | “Where did we leave off?” |
| **Knowledge** | A summary plus searchable decisions, requirements, proposals, and open questions, with reasons and original evidence. | “Why did we choose SQLite?” |
| **AI Q&A** | A set of questions and source-linked answers drawn from earlier project discussions. | “What did we discuss about CSV and JSON exports?” |
| **Scheme** | Architecture and workflow diagrams with dependencies and supporting evidence. | “How does ProjectHub turn linked conversations into an overview?” |
| **Focused Scheme** | A separate diagram for a workflow or code path you describe. | “How do imported conversations become searchable?” |
| **Implementation Choices & Limits** | Code-backed findings about limits, defaults, filters, and fallbacks, with locations for their declarations and use. | “Where is import size capped, and what happens at the limit?” |

Use **Reviews → Ask Project** for a new question. Generate **AI tags** separately
to browse saved Knowledge and Q&A by topic.

**AI runs when you request it.** Opening projects and searching saved information do not trigger model requests.

![Overview of the ProjectHub project with example data](docs/images/projecthub-dashboard.png)

## Install and run

### Install a wheel

For most users, start with the [v0.2.5 wheel](https://github.com/avnikonenko/ProjectHub/releases/download/v0.2.5/projecthub_local-0.2.5-py3-none-any.whl).
ProjectHub runs on Linux, requires **Python 3.11+**, and includes a web interface.
Open a terminal in the download directory. Pick pipx or Conda and use the
filename you downloaded. [Other releases](https://github.com/avnikonenko/ProjectHub/releases)

**With pipx** ([install pipx](https://pipx.pypa.io/latest/how-to/install-pipx.html) if needed)

```bash
pipx install ./projecthub_local-0.2.5-py3-none-any.whl
projecthub
```

If the launcher is outside your PATH, run `pipx ensurepath` and reopen the terminal.
If you already have a source launcher named `projecthub`, you can try the wheel
in a separate Conda environment.

**With Conda**, if you already use it

```bash
conda create -n projecthub -c conda-forge python=3.12 pip
conda activate projecthub
python -m pip install ./projecthub_local-0.2.5-py3-none-any.whl
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
Already installed? [Upgrade ProjectHub](docs/GETTING_STARTED.md#upgrade-projecthub).

## Example: Get back up to speed on Project X

For AI generation, install and sign in to the CLI you want to use. Run
`codex login` for Codex or `claude auth login` for Claude Code. You can browse
saved projects and manage notes with ProjectHub alone. [AI setup guide](docs/AI_GENERATION.md#ai-provider)

1. Click **Add project**, name it **Project X**, and link its local Git repository if you have one.
2. In **Sources**, link a Codex or Claude Code session about Project X, or use **Import conversation** to add a past discussion.
3. In **Generate**, select **Overview** and **Knowledge**, then click **Regenerate selected**. Open **Overview** and **Knowledge → Knowledge Summary & records** to read your saved results and follow their citations.
4. When you return, open **Reviews → Ask Project** and ask “Why did we choose to validate records before importing them?” or “Which export options did we discuss, and what is still undecided?” With the optional terminal-agent skill, you can also ask Codex or Claude Code “Read ProjectHub context for Project X before we continue.”
5. In **Generate**, select **Scheme** and click **Regenerate selected**. Open **Scheme** to explore the architecture, workflow, and dependencies.

## Import conversations

Open **Sources → Import conversation**, paste a public share link or select a UTF-8 file, then review the message counts and sample before saving. Manage imported conversations under **Sources** and read them in **History**. They also appear in Search and can contribute to Knowledge. ProjectHub keeps a downloadable source copy for each saved import or update.

- **ChatGPT** supports public share links directly.
- **Claude** share links are best-effort. If Claude returns HTTP 403 or the messages are unavailable, select **Import from file**.
- **Tested file formats** include JSON with a `messages` array containing `role` and `content`, a single Claude conversation with `chat_messages`, and Markdown with each turn headed `## User`, `## Assistant`, or `## Claude`. See the [file examples](docs/GETTING_STARTED.md#import-conversations).

To import your Claude history, request the [official Claude data export](https://support.claude.com/en/articles/9450526-export-your-claude-data), unzip it, and save the one conversation you want as a JSON file. [Claude Exporter](https://github.com/secondfry/claude-exporter) and [Copy Chat](https://github.com/Neautrino/copy-gpt) can export individual chats. Their exact output formats are unverified in ProjectHub. Check the preview and use the tested JSON or Markdown structure if needed.

## Settings and AI

Open **AI settings** in the project header to choose Codex or Claude Code, a model, and reasoning effort, or to edit project AI instructions. Each provider uses its CLI sign-in on the machine running ProjectHub.

In Settings, choose a colour palette for the Portfolio theme. Light/dark mode and text size can be adjusted independently.

ProjectHub runs locally. AI generation sends selected project context to your chosen provider when requested. See [AI settings and generation](docs/AI_GENERATION.md) for progress, cancellation, and available settings.

### AI limitations

- **Long histories:** Analysis uses bounded context and may miss details. Follow source references to check conclusions.
- **AI accuracy:** Generated architecture and current-work summaries are interpretations, not proof that features work or scientific results are valid.

Open **Coverage** below a generated result to see what evidence was included and what was missed.

## Terminal-agent skill

The optional skill lets Codex and Claude Code read project context and manage notes
and TODOs on request. Run `projecthub install-skill` to install it for any assistants
found on your `PATH`. Use `--agent codex` or `--agent claude` to select one explicitly.
With ProjectHub running, open a new session in your chosen assistant and try these prompts.

- “Read ProjectHub context for Project X.”
- “Save this explanation as a note in Project X.”
- “Add a TODO to Project X: test the import.”
- “Mark ‘test the import’ finished in Project X.”

In Codex, you can also manage Codex session links.

- “Link this session to Project X.”
- “Unlink this session from Project X.”
- “Find all codex sessions related to X and link them to Project X.”

<details>
<summary>Optional terminal commands</summary>

Run these directly from your terminal to read the same project context.

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
