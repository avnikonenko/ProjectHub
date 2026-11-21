"""Readable project archives rendered from saved records only."""
from contextlib import contextmanager
import codecs
import html
import io
import posixpath
import re
from urllib.parse import parse_qs, quote, urlsplit

from sqlalchemy import select

from .integrations.codex.scheme import render_mermaid
from .models import CodexSession, Note, Task


_PAGES = (
    ('Overview', 'overview.md'), ('Knowledge', 'knowledge.md'), ('AI Q&A', 'ai-qa.md'),
    ('Scheme', 'scheme.md'), ('Implementation Choices & Limits', 'implementation-choices.md'),
    ('TODOs', 'todos.md'), ('Notes', 'notes.md'), ('Sources', 'sources/index.md'),
)
_MARKDOWN = re.compile(r'([\\`*_\[\]#|])')


def _escape(value):
    return _MARKDOWN.sub(r'\\\1', html.escape(str(value), quote=False))


def _inline(value):
    return _escape(' '.join(str(value).split()))


def _link(label, target):
    return f"[{_inline(label)}]({quote(target, safe='/:?#&=%+@~!$;,')})"


def _external_url(value):
    if not isinstance(value, str) or re.search(r'[\s\\\x00-\x1f\x7f]', value):
        return None
    try:
        parsed = urlsplit(value)
        parsed.port
        if parsed.scheme == 'https' and parsed.hostname and parsed.username is None and parsed.password is None:
            return value
    except ValueError:
        pass
    return None


def _local_link(label, target, page='index.md'):
    if (not isinstance(target, str) or not target or target.startswith('/')
            or re.search(r'[:\\\x00-\x1f\x7f]', target)
            or any(part in ('', '.', '..') for part in target.split('/'))):
        return None
    relative = posixpath.relpath(target, posixpath.dirname(page) or '.')
    return f'[{_inline(label)}]({quote(relative)})'


def _records(value):
    if isinstance(value, (list, tuple)):
        yield from (record for record in value if isinstance(record, dict))


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _strings(value):
    if isinstance(value, (list, tuple)):
        yield from (entry for entry in value if isinstance(entry, str) and entry)


@contextmanager
def _page(archive, path, title):
    with archive.open(path, 'w', force_zip64=True) as member:
        with io.TextIOWrapper(member, encoding='utf-8', errors='backslashreplace', newline='\n') as out:
            out.write(f'# {_inline(title)}\n\n')
            if path != 'index.md':
                out.write(_local_link('Project index', 'index.md', path) + '\n\n')
            yield out


def _field(out, name, value):
    if (isinstance(value, str) and value.strip()) or type(value) in (int, float):
        out.write(f'- **{name}** · {_inline(value)}\n')


def _quote(out, value):
    if isinstance(value, str) and value.strip():
        out.write('\n')
        for line in value.splitlines():
            # Escape the content of the blockquote so source text remains data.
            out.write(f'> {_escape(line)}\n')
        out.write('\n')


def _meta(out, snapshot):
    _field(out, 'Saved', snapshot.get('generated_at'))
    publication = snapshot.get('publication_id') or _mapping(snapshot.get('project_state')).get('publication_id')
    _field(out, 'Publication', publication)
    _field(out, 'Focus', snapshot.get('focus'))
    for key in ('provider', 'model', 'effort'):
        _field(out, key.capitalize(), _mapping(snapshot.get('generation')).get(key))
    out.write('\n')


def _coverage(out, coverage, level=2):
    if isinstance(coverage, list):
        messages = list(_strings(coverage))
        counts = {}
    elif isinstance(coverage, dict):
        messages = [*_strings(coverage.get('limitations')), *_strings(coverage.get('notes'))]
        counts = {key: len(coverage[key]) for key in ('processed', 'unprocessed', 'unavailable', 'excluded')
                  if isinstance(coverage.get(key), list)}
        if type(coverage.get('partial')) is bool:
            counts['Partial coverage'] = 'Yes' if coverage['partial'] else 'No'
    else:
        return
    if messages or counts:
        out.write(f'\n{"#" * level} Coverage\n\n')
        for name, count in counts.items():
            _field(out, name.replace('_', ' ').capitalize(), count)
        for message in dict.fromkeys(messages):
            out.write(f'- {_inline(message)}\n')
        out.write('\n')


def _owned(record, project_id):
    return record.get('project_id') in (None, project_id)


def _sources(value, project_id):
    return {source['id']: source for source in _records(value)
            if isinstance(source.get('id'), str) and _owned(source, project_id)}


def _numbered(sources):
    return {key: (index, value) for index, (key, value) in enumerate(sources.items(), 1)}


def _refs(ids, numbered, prefix='source'):
    return ' '.join(f'[{numbered[key][0]}](#{prefix}-{numbered[key][0]})'
                    for key in dict.fromkeys(_strings(ids)) if key in numbered)


def _source_links(source, source_paths, page):
    links = []
    for field, kind, label in (('note_id', 'notes', 'Saved transcript'), ('note_id', 'uploads', 'Saved upload'),
                               ('session_id', 'sessions', 'Saved Codex session')):
        identifier = source.get(field)
        if type(identifier) is int:
            path = source_paths.get(kind, {}).get(identifier)
            if link := _local_link(label, path, page):
                links.append(link)
    note = source.get('note_id')
    if not links and type(note) is int and note in source_paths.get('_saved_notes', ()) and page != 'notes.md':
        links.append(_link(f'Note {note}', f'notes.md#note-{note}'))
    for item in _records(source.get('shared_sessions')):
        identifier = item.get('session_id')
        if type(identifier) is int:
            path = source_paths.get('sessions', {}).get(identifier)
            if link := _local_link(item.get('session_title') or 'Also in saved Codex session', path, page):
                links.append(link)
    for raw in (source.get('url'), source.get('reference')):
        if url := _external_url(raw):
            links.append(_link('Original source', url))
            break
    return ' · '.join(dict.fromkeys(links))


def _source_details(out, numbered, source_paths, page, prefix='source', level=2, *, excerpts=True):
    if not numbered:
        return
    out.write(f'\n{"#" * level} Sources\n\n')
    for number, source in numbered.values():
        heading = 'Knowledge summary source' if prefix == 'knowledge-summary-source' else 'Source'
        out.write(f'{"#" * (level + 1)} {heading} {number}\n\n')
        out.write(f'{_inline(source.get("label") or source["id"])}\n\n')
        if links := _source_links(source, source_paths, page):
            out.write(links + '\n\n')
        for key, name in (('path', 'Path'), ('line_start', 'First line'), ('line_end', 'Last line'),
                          ('provenance', 'Provenance'), ('revision', 'Revision'), ('turn_id', 'Turn')):
            _field(out, name, source.get(key))
        if excerpts:
            _quote(out, source.get('text'))
        out.write('\n')


def _brief(out, brief, project_id, source_paths, path, *, level=2, prefix='source'):
    _meta(out, brief)
    numbered = _numbered(_sources(brief.get('sources'), project_id))
    for key, entries in _mapping(brief.get('sections')).items():
        points = entries if isinstance(entries, list) else [entries] if isinstance(entries, str) else []
        if not points:
            continue
        title = key.replace('_', ' ').capitalize()
        out.write(f'{"#" * level} {_inline(title)}\n\n')
        for point in points:
            fields = _mapping(point)
            value = fields.get('text') if fields else point
            if not isinstance(value, str):
                continue
            classification = fields.get('classification')
            status = f'**{_inline(classification.capitalize())}** · ' if isinstance(classification, str) else ''
            out.write(f'- {status}{_inline(value)} {_refs(fields.get("source_ids"), numbered, prefix)}\n')
        out.write('\n')
    _coverage(out, brief.get('coverage'), level)
    _source_details(out, numbered, source_paths, path, prefix, level)


def _overview(archive, project, brief, source_paths):
    path = 'overview.md'
    with _page(archive, path, 'Project Overview') as out:
        _field(out, 'Project status', project.status)
        if remote := _external_url(project.git_remote):
            out.write('- ' + _link('Repository', remote) + '\n')
        _quote(out, project.description)
        if project.goals:
            out.write('## Project goals\n')
            _quote(out, project.goals)
        if brief:
            _brief(out, brief, project.id, source_paths, path)
        else:
            out.write('\nNo saved Overview.\n')


def _knowledge(archive, project, snapshot, source_paths):
    path = 'knowledge.md'
    with _page(archive, path, 'Project Knowledge') as out:
        if not snapshot:
            out.write('No published Knowledge snapshot.\n')
            return
        _meta(out, snapshot)
        if summary := _mapping(snapshot.get('summary')):
            out.write('## Saved Knowledge summary\n\n')
            _brief(out, summary, project.id, source_paths, path, level=3, prefix='knowledge-summary-source')
        numbered = _numbered(_sources(snapshot.get('sources'), project.id))
        item_numbers = {item['id']: number for number, item in enumerate(
            (item for item in _records(snapshot.get('items')) if _owned(item, project.id)
             and isinstance(item.get('id'), str)), 1)}
        if item_numbers:
            out.write('## Extracted knowledge\n\n')
        for item in _records(snapshot.get('items')):
            if not _owned(item, project.id) or not isinstance(item.get('id'), str) or item['id'] not in item_numbers:
                continue
            out.write(f'### Item {item_numbers[item["id"]]}\n\n**{_inline(item.get("statement") or "")}**\n\n')
            for key, label in (('design_status', 'Design status'), ('category', 'Category'), ('scope', 'Scope'),
                               ('source_state', 'Saved evidence availability'), ('rationale', 'Rationale'),
                               ('alternatives', 'Alternatives'), ('limitations', 'Limitations'),
                               ('reconciliation_state', 'Reconciliation'), ('reconciliation_note', 'Context review')):
                _field(out, label, item.get(key))
            for key in ('supersedes', 'superseded_by', 'conflicts_with'):
                related = [_link(f'Item {item_numbers[value]}', f'#item-{item_numbers[value]}')
                           if value in item_numbers else _inline(value) for value in _strings(item.get(key))]
                if related:
                    out.write(f'- **{key.replace("_", " ").capitalize()}** · {", ".join(related)}\n')
            for reference in _records(item.get('evidence')):
                label = reference.get('label') or reference.get('source_id') or 'Recorded excerpt'
                role = reference.get('role') or 'Unknown speaker'
                cite = _refs([reference.get('source_id')], numbered)
                out.write(f'\n**{_inline(role.capitalize() if isinstance(role, str) else role)}** · {_inline(label)} {cite}\n')
                _quote(out, reference.get('excerpt'))
            out.write('\n')
        if not item_numbers:
            out.write('No saved knowledge items.\n')
        _coverage(out, snapshot.get('coverage'))
        _source_details(out, numbered, source_paths, path, excerpts=False)


def _qa(archive, project, snapshot, source_paths):
    path = 'ai-qa.md'
    with _page(archive, path, 'AI Q&A') as out:
        if not snapshot:
            out.write('No saved AI Q&A.\n')
            return
        _meta(out, snapshot)
        numbered = _numbered(_sources(snapshot.get('sources'), project.id))
        for item in _records(snapshot.get('items')):
            if not _owned(item, project.id):
                continue
            out.write(f'## {_inline(item.get("question") or "Saved question")}\n')
            _quote(out, item.get('answer'))
            if citations := _refs(item.get('source_ids'), numbered):
                out.write(citations + '\n\n')
            for variant in _records(item.get('variants')):
                out.write('**Alternative answer**\n')
                _quote(out, variant.get('answer'))
                if citations := _refs(variant.get('source_ids'), numbered):
                    out.write(citations + '\n\n')
        _coverage(out, snapshot.get('coverage') or _mapping(snapshot.get('generation')).get('coverage'))
        _source_details(out, numbered, source_paths, path)


def _diagrams(archive, project, scheme):
    exported = {}
    for name in ('architecture', 'workflow', 'saved-architecture'):
        if name == 'saved-architecture':
            mermaid = project.architecture_mermaid
        else:
            graph = _mapping(scheme.get(name))
            mermaid = graph.get('mermaid')
            if not mermaid and graph.get('nodes'):
                try:
                    mermaid = render_mermaid(graph)
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue  # Keep older or malformed saved graphs in project.json.
        if isinstance(mermaid, str) and mermaid.strip():
            path = f'diagrams/{name}.mmd'
            with archive.open(path, 'w', force_zip64=True) as member:
                member.write(mermaid.encode('utf-8', errors='backslashreplace'))
                if not mermaid.endswith('\n'):
                    member.write(b'\n')
            exported[name] = path
    return exported


def _scheme(archive, project, snapshot, source_paths):
    path = 'scheme.md'
    diagrams = _diagrams(archive, project, snapshot)
    with _page(archive, path, 'Scheme') as out:
        if manual := diagrams.get('saved-architecture'):
            out.write(_local_link('Manually saved architecture (Mermaid)', manual) + '\n\n')
        if not snapshot:
            out.write('No saved generated Scheme.\n')
            return
        _meta(out, snapshot)
        inventory = _mapping(snapshot.get('dependency_inventory'))
        sources = {**_sources(inventory.get('sources'), project.id), **_sources(snapshot.get('sources'), project.id)}
        numbered = _numbered(sources)
        for key in ('architecture', 'workflow'):
            graph = _mapping(snapshot.get(key))
            if not graph:
                continue
            out.write(f'## {key.capitalize()}\n\n')
            if key in diagrams:
                out.write(_local_link('Mermaid diagram', diagrams[key]) + '\n')
            _quote(out, graph.get('explanation'))
            labels = {node['id']: node.get('label') or node['id'] for node in _records(graph.get('nodes'))
                      if isinstance(node.get('id'), str)}
            for node in _records(graph.get('nodes')):
                fields = [node.get(key) for key in ('label', 'status', 'description') if node.get(key)]
                citations = _refs(node.get('source_ids'), numbered)
                out.write(f'- {" · ".join(_inline(value) for value in fields)} {citations}\n')
            if graph.get('edges'):
                out.write('\n**Connections**\n\n')
            for edge in _records(graph.get('edges')):
                source, target = (_inline(labels.get(value, value)) if isinstance(value, str) else ''
                                  for value in (edge.get('source'), edge.get('target')))
                relation = edge.get('label') or edge.get('type') or ''
                out.write(f'- {source} → {target} · {_inline(relation)} {_refs(edge.get("source_ids"), numbered)}\n')
            out.write('\n')
        dependencies = inventory.get('dependencies') or snapshot.get('dependencies')
        if dependencies:
            out.write('## Dependencies\n\n')
            for item in _records(dependencies):
                title = item.get('name') or item.get('id') or 'Dependency'
                fields = [item[key] for key in ('constraint', 'category', 'purpose') if item.get(key)]
                refs = _refs([*_strings(item.get('source_ids')), *_strings(item.get('purpose_source_ids'))], numbered)
                out.write(f'- **{_inline(title)}** {" · ".join(_inline(value) for value in fields)} {refs}\n')
        _coverage(out, snapshot.get('coverage'))
        _source_details(out, numbered, source_paths, path)


def _parameters(archive, project, snapshot, source_paths):
    path = 'implementation-choices.md'
    with _page(archive, path, 'Implementation Choices & Limits') as out:
        if not snapshot:
            out.write('No saved implementation findings.\n')
            return
        _meta(out, snapshot)
        numbered = _numbered(_sources(snapshot.get('sources'), project.id))
        for item in _records(snapshot.get('items')):
            if not _owned(item, project.id):
                continue
            title = item.get('title') or item.get('name') or 'Implementation finding'
            out.write(f'## {_inline(title)}\n\n')
            for key, label in (('finding_kind', 'Kind'), ('component', 'Component'), ('symbol', 'Symbol'),
                               ('path', 'Path'), ('line_start', 'First line'), ('line_end', 'Last line'),
                               ('default_value', 'Declared default'), ('default_kind', 'Default kind'),
                               ('condition', 'Condition'), ('units', 'Units')):
                _field(out, label, item.get(key))
            for key, label in (('behavior', 'Behavior'), ('purpose', 'Purpose'), ('rationale', 'Rationale'),
                               ('trade_off', 'Trade-off')):
                if item.get(key):
                    basis = item.get(f'{key}_basis')
                    _field(out, f'{label} ({_inline(basis)})' if isinstance(basis, str) else label, item[key])
            for key, title in (('source_ids', 'Declaration'), ('usage_source_ids', 'Usage'),
                               ('explanation_source_ids', 'Explanation')):
                if refs := _refs(item.get(key), numbered):
                    out.write(f'- **{title} sources** · {refs}\n')
            out.write('\n')
        _coverage(out, snapshot.get('coverage'))
        _source_details(out, numbered, source_paths, path)


def _todo(archive, db, project, source_paths):
    with _page(archive, 'todos.md', 'TODOs') as out:
        tasks = db.scalars(select(Task).where(Task.project_id == project.id)
                           .order_by(Task.pinned.desc(), Task.id).execution_options(yield_per=1))
        found = False
        for task in tasks:
            found = True
            out.write(f'## {_inline(task.title)}\n\n')
            _field(out, 'Status', task.status)
            _field(out, 'Priority', task.priority)
            _field(out, 'Updated', task.updated_at)
            path = source_paths.get('sessions', {}).get(task.session_id)
            if link := _local_link('Related Codex session', path):
                out.write('- ' + link + '\n')
            _quote(out, task.description)
        if not found:
            out.write('No saved TODOs.\n')


def _note_rows(db, project_id):
    return db.execute(select(Note.id, Note.title, Note.kind, Note.reference, Note.updated_at)
                      .where(Note.project_id == project_id).order_by(Note.id))


def _note_body(out, db, note_id):
    # Export one bounded UTF-8 buffer at a time. Indented Markdown code
    # preserves original line breaks and displays imported markup as text.
    connection = db.connection().connection.driver_connection
    with connection.blobopen('notes', 'content', note_id, readonly=True) as blob:
        if not len(blob):
            return
        out.write('**Content**\n\n')
        decoder = io.IncrementalNewlineDecoder(codecs.getincrementaldecoder('utf-8')('backslashreplace'), True)
        at_line_start = True
        while True:
            raw = blob.read(65536)
            chunk = decoder.decode(raw, final=not raw)
            if chunk:
                if at_line_start:
                    out.write('    ')
                at_line_start = chunk.endswith('\n')
                body = chunk[:-1] if at_line_start else chunk
                out.write(body.replace('\n', '\n    '))
                if at_line_start:
                    out.write('\n')
            if not raw:
                break
        out.write('\n\n' if at_line_start else '\n\n\n')


def _excerpt_source(reference, project_id, source_paths):
    prefix = '#history?'
    if not isinstance(reference, str) or not reference.startswith(prefix):
        return None, None
    try:
        params = parse_qs(reference[len(prefix):], keep_blank_values=True, strict_parsing=True, errors='strict',
                          max_num_fields=3)
    except ValueError:
        return None, None
    if params.get('project') != [str(project_id)] or any(len(values) != 1 for values in params.values()):
        return None, None
    if params.keys() in ({'project', 'note'}, {'project', 'note', 'q'}):
        note_id = params['note'][0]
        if re.fullmatch(r'[1-9][0-9]{0,18}', note_id) and int(note_id) in source_paths['_saved_notes']:
            path = source_paths.get('notes', {}).get(int(note_id))
            link = _local_link('Source transcript', path, 'notes.md')
            return link or _link(f'Source note {note_id}', f'#note-{note_id}'), None
    if params.keys() == {'project', 'session', 'turn'}:
        session_id = params['session'][0]
        turn_id = params['turn'][0]
        if (re.fullmatch(r'[1-9][0-9]{0,18}', session_id) and int(session_id) in source_paths['_saved_sessions']
                and turn_id.strip() and not re.search(r'[\x00-\x1f\x7f]', turn_id)):
            path = source_paths.get('sessions', {}).get(int(session_id))
            return _local_link('Source Codex session', path, 'notes.md'), turn_id
    return None, None


def _notes(archive, db, project, source_paths):
    with _page(archive, 'notes.md', 'Notes') as out:
        found = False
        for note in _note_rows(db, project.id):
            found = True
            out.write(f'## Note {note.id}\n\n**{_inline(note.title)}**\n\n')
            _field(out, 'Kind', note.kind)
            _field(out, 'Updated', note.updated_at)
            if links := _source_links({'note_id': note.id, 'reference': note.reference}, source_paths, 'notes.md'):
                # A source already has its own exported transcript or original file.
                if note.id in source_paths.get('notes', {}) or note.id in source_paths.get('uploads', {}):
                    out.write(f'- {links}\n')
                elif url := _external_url(note.reference):
                    out.write('- ' + _link('Original source', url) + '\n')
            excerpt_link, turn_id = _excerpt_source(note.reference, project.id, source_paths)
            if excerpt_link:
                out.write(f'- {excerpt_link}\n')
            _field(out, 'Source turn', turn_id)
            out.write('\n')
            if note.id not in source_paths.get('notes', {}):
                _note_body(out, db, note.id)
        if not found:
            out.write('No saved notes.\n')


def _source_index(archive, db, project, source_paths):
    page = 'sources/index.md'
    with _page(archive, page, 'Sources') as out:
        out.write('## Imported conversations and files\n\n')
        found = False
        for note in _note_rows(db, project.id):
            path = source_paths.get('notes', {}).get(note.id) or source_paths.get('uploads', {}).get(note.id)
            if not (link := _local_link(note.title, path, page)):
                continue
            found = True
            if url := _external_url(note.reference):
                link += ' · ' + _link('Original source', url)
            out.write('- ' + link + '\n')
            for copy in _records(source_paths.get('copies', {}).get(note.id)):
                if copy_link := _local_link(copy.get('filename') or 'Source copy', copy.get('path'), page):
                    sha = f' · SHA-256 {_inline(copy["content_hash"])}' if copy.get('content_hash') else ''
                    out.write(f'  - {copy_link}{sha}\n')
        if not found:
            out.write('No imported source artifacts.\n')
        out.write('\n## Saved Codex sessions\n\n')
        found = False
        rows = db.execute(select(CodexSession.id, CodexSession.title).where(CodexSession.project_id == project.id)
                          .order_by(CodexSession.id))
        for session in rows:
            path = source_paths.get('sessions', {}).get(session.id)
            if link := _local_link(session.title or f'Session {session.id}', path, page):
                found = True
                out.write('- ' + link + '\n')
        if not found:
            out.write('No saved Codex transcripts.\n')


def write_project_markdown(archive, db, project, saved, source_paths):
    """Write readable views into an existing project ZIP read snapshot."""
    source_paths = {
        **source_paths,
        '_saved_notes': set(db.scalars(select(Note.id).where(Note.project_id == project.id))),
        '_saved_sessions': set(db.scalars(select(CodexSession.id).where(CodexSession.project_id == project.id))),
    }
    saved = {name: _mapping(result) for name, result in saved.items() if _owned(_mapping(result), project.id)}
    with _page(archive, 'index.md', project.name) as out:
        _field(out, 'Project', project.id)
        _field(out, 'Project status', project.status)
        _quote(out, project.description)
        out.write('## Project views\n\n')
        for label, path in _PAGES:
            out.write(f'- {_link(label, path)}\n')
        out.write(f'\n{_link("Project records (JSON)", "project.json")}\n')
    _overview(archive, project, _mapping(saved.get('brief')), source_paths)
    _knowledge(archive, project, _mapping(saved.get('knowledge')), source_paths)
    _qa(archive, project, _mapping(saved.get('qa')), source_paths)
    _scheme(archive, project, _mapping(saved.get('scheme')), source_paths)
    _parameters(archive, project, _mapping(saved.get('parameters')), source_paths)
    _todo(archive, db, project, source_paths)
    _notes(archive, db, project, source_paths)
    _source_index(archive, db, project, source_paths)
