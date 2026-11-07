"""Read the text rendered on a public ChatGPT conversation snapshot."""
import json
import re
import time
from html.parser import HTMLParser
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

_SHARE_PATH = re.compile(
    r'/share/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?',
    re.IGNORECASE | re.ASCII,
)
_MAX_BYTES = 16 * 1024 * 1024
_TIMEOUT = 15
_STREAM_PREFIX = 'window.__reactRouterContext.streamController.enqueue('
# The generic response can contain only link preview metadata.
_BROWSER_AGENT = (
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
)


def canonical_url(value):
    url = urlsplit(value.strip())
    match = _SHARE_PATH.fullmatch(url.path)
    if url.scheme != 'https' or url.netloc.lower() != 'chatgpt.com' or not match:
        raise ValueError('Enter a public https://chatgpt.com/share/ conversation link.')
    return f'https://chatgpt.com/share/{match[1].lower()}'


class ShareReadError(Exception):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _Transcript(HTMLParser):
    _markers = {'data-user-message-copy': 'User', 'data-assistant-markdown-root': 'ChatGPT'}
    _void = set('area base br col embed hr img input link meta param source track wbr'.split())
    _blocks = set('blockquote div h1 h2 h3 h4 h5 h6 li p pre tr'.split())
    _ignored = {'script', 'style', 'svg', 'template', 'button'}

    def __init__(self):
        super().__init__()
        self.title = []
        self.in_title = False
        self.messages = []
        self.stack = []
        self.role = None
        self.parts = []
        self.hidden = 0
        self.pre = 0
        self.stream = []
        self.in_script = False

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.in_script = True
        if tag == 'title' and self.role is None:
            self.in_title = True
        if self.role is None:
            self.role = next((self._markers[key] for key, _ in attrs if key in self._markers), None)
            if self.role is None:
                return
        if tag not in self._void:
            self.stack.append(tag)
        if tag in self._ignored:
            self.hidden += 1
        if tag == 'pre':
            self.pre += 1
        if not self.hidden and (tag in self._blocks or tag in {'br', 'hr'}):
            self.parts.append('\n')
        if not self.hidden and tag in {'td', 'th'}:
            self.parts.append('\t')

    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_script = False
        if tag == 'title':
            self.in_title = False
        if not self.stack or self.stack[-1] != tag:
            return
        self.stack.pop()
        if tag == 'pre':
            self.pre -= 1
        if tag in self._ignored:
            self.hidden -= 1
        elif not self.hidden and tag in self._blocks:
            self.parts.append('\n')
        if not self.stack:
            body = re.sub(r'\n{3,}', '\n\n', ''.join(self.parts)).strip()
            if body:
                self.messages.append(f'{self.role}:\n{body}')
            self.parts.clear()
            self.role = None

    def handle_data(self, data):
        if self.in_script and not self.role and data.startswith(_STREAM_PREFIX):
            try:
                chunk, _ = json.JSONDecoder().raw_decode(data, len(_STREAM_PREFIX))
            except ValueError:
                return
            if isinstance(chunk, str):
                self.stream.append(chunk)
            return
        if self.in_title:
            self.title.append(data)
        if self.role and not self.hidden:
            if self.role == 'ChatGPT' and '\n' in data and not data.strip() and not self.pre:
                return
            self.parts.append(data)


def _stream_transcript(chunks):
    # The desktop page stores an indexed JSON table in its router stream.
    # Resolve only the shared conversation and public message fields.
    if not chunks:
        return '', []
    table = json.loads(''.join(chunks).partition('\n')[0])
    if not isinstance(table, list):
        return '', []

    def value(index):
        return table[index] if type(index) is int and 0 <= index < len(table) else None

    def record(item):
        if not isinstance(item, dict):
            return {}
        return {
            key: value(index)
            for ref, index in item.items()
            if ref.startswith('_') and ref[1:].isascii() and ref[1:].isdigit()
            and isinstance(key := value(int(ref[1:])), str)
        }

    loader = record(record(value(0)).get('loaderData'))
    route = next((record(item) for key, item in loader.items() if key.startswith('routes/share.')), {})
    conversation = record(record(route.get('serverResponse')).get('data'))
    nodes = conversation.get('linear_conversation')
    messages = []
    length = 0
    if isinstance(nodes, list):
        for index in nodes:
            message = record(record(value(index)).get('message'))
            role = record(message.get('author')).get('role')
            if role not in ('user', 'assistant') or message.get('recipient') != 'all':
                continue
            if record(message.get('metadata')).get('is_visually_hidden_from_conversation'):
                continue
            content = record(message.get('content'))
            parts = content.get('parts')
            if content.get('content_type') not in ('text', 'multimodal_text') or not isinstance(parts, list):
                continue
            strings = []
            for part in parts:
                text = value(part)
                if isinstance(text, str):
                    length += len(text) + 1
                    if length > _MAX_BYTES:
                        raise ShareReadError('The shared conversation is too large to import.')
                    if text.strip():
                        strings.append(text)
            if strings:
                label = 'User' if role == 'user' else 'ChatGPT'
                messages.append(label + ':\n' + '\n'.join(strings))
    title = conversation.get('title')
    return title if isinstance(title, str) else '', messages


def parse_transcript(html):
    parser = _Transcript()
    parser.feed(html)
    parser.close()
    title = ''.join(parser.title)
    messages = parser.messages if not parser.role else []
    if not messages:
        try:
            stream_title, messages = _stream_transcript(parser.stream)
            title = stream_title or title
        except (ValueError, RecursionError):
            pass
    if not messages:
        raise ShareReadError(
            'No readable transcript was found. Check that the share opens without signing in.'
        )
    title = title.strip().removeprefix('ChatGPT - ').strip()
    return title[:500] or 'Shared ChatGPT conversation', '\n\n'.join(messages)


def read_transcript(url):
    request = Request(canonical_url(url), headers={
        'User-Agent': _BROWSER_AGENT,
        'Accept': 'text/html',
        'Accept-Language': 'en-US,en;q=0.9',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
    })
    deadline = time.monotonic() + _TIMEOUT
    try:
        with build_opener(_NoRedirect()).open(request, timeout=_TIMEOUT) as response:
            if response.headers.get_content_type() != 'text/html':
                raise ShareReadError('ChatGPT did not return a conversation page.')
            body = bytearray()
            while chunk := response.read1(65536):
                body.extend(chunk)
                if len(body) > _MAX_BYTES:
                    raise ShareReadError('The shared conversation is too large to import.')
                if time.monotonic() > deadline:
                    raise ShareReadError('ChatGPT took too long to return the shared conversation.')
    except HTTPError as exc:
        exc.close()
        raise ShareReadError(
            f'ChatGPT returned HTTP {exc.code}. Check that the share opens without signing in.'
        ) from exc
    except (URLError, OSError, HTTPException) as exc:
        raise ShareReadError('Could not connect to ChatGPT to read the shared conversation.') from exc
    return parse_transcript(body.decode('utf-8', errors='replace'))
