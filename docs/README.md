# ProjectHub

**Keep your project knowledge organized and accessible across AI conversations and development sessions.**

On long-running projects, important information often ends up spread across Codex
and Claude Code sessions, ChatGPT and Claude chats, and Git repositories. When you return to a
project, it can be hard to find what you decided and why.

**ProjectHub** is a local workspace that brings this information together.

## What you can do

- **Preserve knowledge.** Capture decisions, requirements, reasoning, and unresolved questions with links to original conversations.
- **Understand your project.** Generate overviews, searchable knowledge, Q&A, and diagrams of architecture, workflows, and dependencies.
- **Explore previous work.** Search conversations to see why decisions were made. Inspect a linked Git repository to see how features were implemented.
- **Continue with context.** Use the optional ProjectHub skill to bring relevant project knowledge into Codex or Claude Code sessions.
- **Stay organized.** Manage projects, notes, TODOs, and related conversations in one workspace.

Designed for developers, researchers, and anyone working on long-running projects with AI assistance.

![ProjectHub overview with example data](images/projecthub-dashboard.png)

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
| **Implementation Choices & Limits** | Code-backed findings about limits, defaults, filters, and fallbacks, with locations for their declarations and use. | “Where is import size capped, and what happens at the limit?” |

## Use and configuration

- [Install ProjectHub and start a project](GETTING_STARTED.md)
- [Generate, refresh, or retry results](AI_GENERATION.md)
- [Browse Overview and Knowledge](PROJECT_KNOWLEDGE_UI.md)
- [Read diagrams and dependencies in Scheme](SCHEME.md)
- [Check evidence and freshness](PROJECT_EVIDENCE.md)
- [Use ProjectHub from a terminal agent](TERMINAL_AGENTS.md)

[Developer reference](DEVELOPER_HOME.md)
