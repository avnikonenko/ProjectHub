from backend.app.integrations.codex.scheme_js_scope import focused_call_owner


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
