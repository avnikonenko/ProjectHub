"""Matched, nonstandard repository evidence is structural and bounded."""
import pytest

from backend.app.integrations.codex.scheme_named_files import named_file_excerpt, named_file_kind


@pytest.mark.parametrize(('path', 'kind'), [
    ('Dockerfile', 'dockerfile'), ('services/api/Containerfile.prod', 'dockerfile'),
    ('dev.dockerfile', 'dockerfile'), ('Makefile', 'makefile'), ('scripts/compile.mk', 'makefile'),
    ('schema.sql', 'sql'), ('.github/workflows/deploy.yml', 'yaml'), ('private.pdf', None),
])
def test_named_file_kinds(path, kind):
    assert named_file_kind(path) == kind


@pytest.mark.parametrize(('path', 'source', 'expected', 'excluded'), [
    ('Dockerfile', '''FROM python:3.12 AS toolchain
ARG TOKEN=PRIVATE_ARGUMENT
RUN <<EOF
FROM PRIVATE_HEREDOC
EOF
FROM python:3.12-slim
COPY --from=toolchain /private/source /app
RUN python -c "PRIVATE_RECIPE"
''', ['L1: FROM python AS toolchain', 'L7: COPY from stage toolchain', 'L8: RUN python'],
     ['PRIVATE_', '/private', '3.12']),
    ('Makefile', '''.PHONY: deploy
deploy: build
\t@docker run --env TOKEN=PRIVATE_RECIPE
deploy: private TOKEN = PRIVATE_ASSIGNMENT
define TEMPLATE
deploy: PRIVATE_TEMPLATE
endef
''', ['L2: Make rule deploy depends on build', 'L3: Make recipe for deploy docker'], ['PRIVATE_']),
    ('schema.sql', '''/* nested comment /* marker */ CREATE TABLE PRIVATE_COMMENT; */
CREATE TABLE "conversation" (id INTEGER PRIMARY KEY, body TEXT DEFAULT "PRIVATE_DEFAULT");
CREATE TABLE turn (conversation_id INTEGER REFERENCES "conversation"(id));
INSERT INTO turn VALUES ('PRIVATE_SEED');
CREATE UNIQUE INDEX by_conversation ON turn(conversation_id);
CREATE VIEW recent AS SELECT * FROM turn WHERE notes = $tag$PRIVATE_DOLLAR$tag$;
''', ['L2: CREATE TABLE "conversation" column id INTEGER', 'L2: CREATE TABLE "conversation" column body TEXT',
      'L3: CREATE TABLE turn REFERENCES "conversation"', 'L5: CREATE INDEX by_conversation ON turn',
      'L6: CREATE VIEW recent FROM turn'], ['PRIVATE_', 'INSERT']),
    ('.github/workflows/deploy.yml', '''name: PRIVATE_WORKFLOW
jobs:
  deploy:
    steps:
      - run: |
          configured: PRIVATE_BLOCK
          false_key: PRIVATE_LITERAL
      - uses: PRIVATE_ACTION
''', ['L4: YAML key jobs.deploy.steps', 'L5: YAML key jobs.deploy.steps.run',
      'L8: YAML key jobs.deploy.steps.uses'], ['PRIVATE_', 'false_key', 'configured']),
])
def test_named_extractors_keep_safe_structure(path, source, expected, excluded):
    excerpt = named_file_excerpt(source, path, limit=1800, priority=lambda _: 0)
    assert all(value in excerpt for value in expected)
    assert all(value not in excerpt for value in excluded)


def test_excerpt_budget_prioritizes_matching_declarations():
    excerpt = named_file_excerpt('FROM python\nRUN uv add secret\nENTRYPOINT ["private"]\n', 'Dockerfile',
                                 limit=14, priority=lambda declaration: int(declaration == 'RUN uv'))
    assert excerpt == 'L2: RUN uv'


def test_yaml_scalar_continuations_are_never_rendered_as_child_keys():
    source = ('jobs: # Keep job declarations\n  build:\n    label: "multiline\n'
              '      PRIVATE_SCALAR: literal"\n    steps:\n      - run: build command\n')
    excerpt = named_file_excerpt(source, 'deploy.yaml', limit=1800, priority=lambda _: 0)
    assert 'jobs.build.steps.run' in excerpt
    assert 'PRIVATE_SCALAR' not in excerpt
