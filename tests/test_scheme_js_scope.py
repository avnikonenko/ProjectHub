from backend.app.integrations.codex.scheme_evidence import _mask_js
from backend.app.integrations.codex.scheme_js_scope import focused_call_owner, focused_ui_declarations


def _owners(source, names):
    owner = focused_call_owner(source)
    return {name: owner(source.index(name + '(')) for name in names}


def test_calls_are_attributed_to_containing_scopes():
    source = """function status(){ return readStatus(); }
const flushQueue = async() => { persistBatch(); };
registerHandler(flushQueue);"""
    assert _owners(source, ('status', 'readStatus', 'persistBatch', 'registerHandler')) == {
        'status': None, 'readStatus': 'status', 'persistBatch': 'flushQueue', 'registerHandler': 'module',
    }


def test_typed_functions_and_nested_callbacks():
    source = """export async function outer<T>(state: T): Promise<T> {
  const inner = async <R>(job: R): Promise<R> => {
    if (hasWork(job)) { return startWork(job); }
  };
  queue.schedule(function () { ignoredAnonymous(); });
  queue.schedule(() => { ignoredArrow(); });
  queue.schedule(() => ignoredExpression());
  return finish(state);
}"""
    assert _owners(source, ('hasWork', 'startWork', 'ignoredAnonymous', 'ignoredArrow',
                            'ignoredExpression', 'finish')) == {
        'hasWork': 'outer.inner', 'startWork': 'outer.inner', 'ignoredAnonymous': None, 'ignoredArrow': None,
        'ignoredExpression': None, 'finish': 'outer',
    }


def test_method_head_and_unsupported_bodies_do_not_acquire_previous_owner():
    source = """function makeWorker() {
  return {runTask(task = loadTask()) { send(task); }};
}
function knownBefore() { ready(); }
const unfinished = () => { maybeSend();"""
    assert _owners(source, ('runTask', 'loadTask', 'send', 'ready', 'maybeSend')) == {
        'runTask': None, 'loadTask': None, 'send': None, 'ready': 'knownBefore', 'maybeSend': None,
    }


def test_expression_bodied_arrows_require_an_explicit_boundary():
    source = """const parse = (input = defaultInput()) => validate(input);
const wrapper = () => () => doLater();
const ambiguous = (input) =>
  evaluate(input)
globalHandler();
const incomplete = input => transform(input;"""
    assert _owners(source, ('defaultInput', 'validate', 'doLater', 'evaluate', 'globalHandler', 'transform')) == {
        'defaultInput': None, 'validate': 'parse', 'doLater': None, 'evaluate': None,
        'globalHandler': None, 'transform': None,
    }


def test_unclassified_blocks_do_not_supply_module_call_evidence():
    source = """interface Worker { runTask(): void; }
const worker = { run() { runImplementation(); } };
configureWorker(worker);"""
    assert _owners(source, ('runTask', 'runImplementation', 'configureWorker')) == {
        'runTask': None, 'runImplementation': None, 'configureWorker': 'module',
    }


def test_nested_functions_with_same_leaf_have_distinct_owners():
    source = """function read() { function run() { readWork(); } }
function write() { function run() { writeWork(); } }
function module() { initialize(); }"""
    assert _owners(source, ('readWork', 'writeWork', 'initialize')) == {
        'readWork': 'read.run', 'writeWork': 'write.run', 'initialize': 'module',
    }


def test_ui_declarations_show_named_click_handler_and_request_fields():
    source = '''function Status() {
  async function cancel() {
    return await post(`${base}/generation/cancel`, { kind, started_at: started });
  }
  return <button disabled={cancelling} onClick={() => void cancel()}>Cancel</button>;
}'''
    masked = _mask_js(source)
    assert focused_ui_declarations(source, masked, focused_call_owner(masked)) == [
        (5, 'UI click binding Status <button> onClick invokes cancel()'),
        (3, 'Client request syntax Status.cancel calls post for ${base}/generation/cancel'
            ' with payload {kind, started_at: started}'),
    ]


def test_ui_declarations_skip_external_targets_comments_and_raw_payload_values():
    source = '''function Status() {
  // post('/comment', { ignored });
  const sample = "<button onClick={sample}>";
  post('/action?token=private', { ignored });
  post('https://example.org/private', { ignored });
  post(`${base}/action/${user}`, { ignored });
  post('/action', { token: 'private' });
  return <button onClick={handleClick}>Go</button>;
}'''
    masked = _mask_js(source)
    assert focused_ui_declarations(source, masked, focused_call_owner(masked)) == [
        (8, 'UI click binding Status <button> onClick references handleClick'),
        (7, 'Client request syntax Status calls post for /action'),
    ]
