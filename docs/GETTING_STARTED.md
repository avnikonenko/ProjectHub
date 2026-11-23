# Install and run

## Install a wheel

For most users, start with the [v0.2.5 wheel](https://github.com/avnikonenko/ProjectHub/releases/download/v0.2.5/projecthub_local-0.2.5-py3-none-any.whl).
It includes the interface and migrations and runs on Linux with Python 3.11+.
Open a terminal in the download directory. Pick pipx or Conda and use the
filename you downloaded.

### With pipx

[Install pipx](https://pipx.pypa.io/latest/how-to/install-pipx.html) if needed.

```bash
pipx install ./projecthub_local-0.2.5-py3-none-any.whl
projecthub
```

If the launcher is outside your `PATH`, run `pipx ensurepath` and reopen the
terminal.

### With Conda

Use this option if you already use Conda.

```bash
conda create -n projecthub -c conda-forge python=3.12 pip
conda activate projecthub
python -m pip install ./projecthub_local-0.2.5-py3-none-any.whl
projecthub
```

Install Git for repository inspection. Contributors can also
[build a wheel](DEVELOPER_HOME.md#build-a-wheel).

## Install from source

On Linux, install Python 3.11+ with `venv`, Git, and Node.js 22.18+ with npm.
Run these commands from your [ProjectHub checkout](https://github.com/avnikonenko/ProjectHub).

```bash
bash scripts/install.sh
projecthub
```

Keep the checkout. If `projecthub` is outside your `PATH`, run `bash scripts/projecthub`.

## Start a project

For AI generation, install and sign in to the CLI you want to use. Run
`codex login` for Codex or `claude auth login` for Claude Code. You can browse
saved projects and manage notes with ProjectHub alone. [AI setup guide](AI_GENERATION.md#ai-provider)

1. Open [ProjectHub](http://127.0.0.1:8766) and click **Add project**. You can link a local Git repository.
2. In **Sources**, link a Codex or Claude Code session, or use **Import conversation**.
3. Choose **Codex** or **Claude Code** in the project’s **AI settings**. In **Generate**, select **Overview** and **Knowledge**, then click **Regenerate selected**. [Generation guide](AI_GENERATION.md)
4. Open **Overview** and **Knowledge → Knowledge Summary & records** to read your saved results. Follow citations to check the original evidence.

Press Ctrl+C in the terminal to stop ProjectHub.

## Link a local session

Open **Sources → Link session**, choose Codex or Claude Code, and enter the
session ID. Preview the match before linking it. Select a profile if the
session belongs to a different local profile.

Linked Claude Code sessions appear under **Project sessions** in **Sources**,
with their saved transcripts in **History**. Use **Refresh session** to
preview and save new turns. ProjectHub
keeps the earlier source copies and reads the transcript without running Claude
Code. History, Search, and AI generation use the saved transcript.

## Import conversations

In **Sources → Import conversation**, choose a public share link or a UTF-8 file. Use the preview to check speaker roles, message counts, and source availability. After saving, manage the conversation in **Sources** or read the transcript in **History**. If a Claude link returns HTTP 403, download or export the conversation on Claude and use **Import from file**.

JSON files can contain one conversation with messages in their original order. Use `role` and `content` for each message. Include `id` and `timestamp` when the export provides them.

```json
{
  "title": "Import design discussion",
  "messages": [
    {"role": "user", "content": "Why validate before saving?"},
    {"role": "assistant", "content": "So malformed imports leave the project untouched."}
  ]
}
```

For Markdown, put each speaker heading on its own line. Keep headings that are part of code inside fenced code blocks.

```markdown
# Import design discussion

## User
Why validate before saving?

## Assistant
So malformed imports leave the project untouched.
```

ProjectHub also accepts a single Claude JSON conversation with `chat_messages`. For a multi-conversation account export, extract the object for the conversation you want and save it as its own JSON file. If the export includes a conversation UUID, ProjectHub can recognize a later export of the same conversation and ask you to confirm the update. One-sided conversations require confirmation as partial. Use **Source copies** in the saved transcript to download the original file or a copy of an earlier import. The project JSON export also includes these copies.

## Export a project

Open **Export project** in the project header and choose a format.

- **ZIP archive.** Extract it and open `index.md` to browse your saved results.
  It also includes Mermaid diagrams, structured records and revisions in
  `project.json`, and available copies of original sources.
- **JSON file.** Download the structured project records as a standalone file.
  This option is also available in Notes.

Export uses saved data and runs without AI.

## Configuration and backups

- **Settings.** Run `projecthub config` for the file path. The default is `~/.config/projecthub/config.toml`. Environment variables take precedence at startup. Restart after file edits. [Example configuration](https://github.com/avnikonenko/ProjectHub/blob/main/config.example.toml)
- **Backups.** Source and wheel installations default to `~/.local/share/projecthub`. Back up that directory to preserve history, notes, and tasks.
