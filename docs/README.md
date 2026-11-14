# Documentation

Start with the user guides for setup and everyday use. Developer references cover
implementation, tests, and deployment boundaries.

## Use and configuration

- [Install and run](../README.md#install-and-run)
- [Configuration example](../config.example.toml)
- [AI generation, progress, cancellation, and parallel reading](AI_GENERATION.md)
- [Project evidence and freshness](PROJECT_EVIDENCE.md)
- [Project Overview and Knowledge](PROJECT_KNOWLEDGE_UI.md)
- [Scheme](SCHEME.md)

## For contributors

Run `projecthub check` or `bash scripts/check.sh` for backend/frontend tests,
TypeScript checks, and the production build.

- [Architecture](ARCHITECTURE.md)
- [HTTP API contract](API_CONTRACT.md)
- [Data model](DATA_MODEL.md)
- [Knowledge pipeline and isolated testing](KNOWLEDGE_PIPELINE.md)
- [Validation commands and scope](TEST_REPORT.md)
- [Security and deployment boundaries](SECURITY_REVIEW.md)
- [Engineering decisions](DECISIONS.md)
- [Implementation status](IMPLEMENTATION_REPORT.md)
- [Implementation plan](IMPLEMENTATION_PLAN.md) and [Scheme implementation contract](scheme-implementation.md)
