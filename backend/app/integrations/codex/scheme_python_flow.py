"""Summarize bounded Python control flow without forwarding source literals."""

import ast
from collections import deque
import heapq


_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = (*_FUNCTIONS, ast.ClassDef)
_CONTEXTS = (ast.With, ast.AsyncWith)
_MAX_ROWS = 32
_MAX_ARGUMENTS = 8
_TRACE_DEPTH = 3
_TRACE_ROUTINES = 24
_TRACE_NODES = 10000
_TRACE_ROUTINE_ROWS = 8
_TRACE_ROUTINE_CALLS = {
    'ValueError', 'all', 'any', 'append', 'add_argument', 'add_argument_group', 'clear', 'close',
    'debug', 'dict', 'enumerate', 'error', 'extend', 'format', 'get', 'getcwd', 'info', 'items', 'join', 'keys', 'len', 'list', 'open',
    'print', 'range', 'setdefault', 'strip', 'sum', 'tuple', 'update', 'values', 'warning', 'zip',
}
_TRACE_PRIORITY_TERMS = ('backend', 'checkpoint', 'dispatch', 'dock', 'generation', 'mutat',
                         'population', 'program', 'restore', 'resume', 'select')


def _call_shape(call, shape, *, max_arguments=_MAX_ARGUMENTS):
    if not isinstance(call.func, (ast.Name, ast.Attribute)):
        return ''
    arguments = [shape(arg) for arg in call.args[:max_arguments]]
    arguments.extend(
        f'{keyword.arg}={shape(keyword.value)}' if keyword.arg else '**<expression>'
        for keyword in call.keywords[:max_arguments - len(arguments)]
    )
    if len(arguments) < len(call.args) + len(call.keywords):
        arguments.append('…')
    return f'{shape(call.func)}({", ".join(arguments)})'


def _statement_shape(node, shape):
    if isinstance(node, ast.If) and node.body:
        action = node.body[0]
        if isinstance(action, ast.Raise):
            error = action.exc.func if isinstance(action.exc, ast.Call) else action.exc
            ending = f'raise {shape(error)}' if error is not None else 'raise'
        elif isinstance(action, ast.Return):
            ending = 'return'
        else:
            return '', None
        return f'if {shape(node.test)} -> {ending}', node.test

    if not isinstance(node, (ast.Expr, ast.Return, ast.Assign, ast.AnnAssign)):
        return '', None
    value = node.value
    awaited = isinstance(value, ast.Await)
    value = value.value if awaited else value
    if isinstance(value, ast.Call):
        expression = _call_shape(value, shape)
    elif (isinstance(node, ast.Assign) and isinstance(value, ast.Tuple)
          and 1 < len(value.elts) <= _MAX_ARGUMENTS
          and all(isinstance(item, (ast.Name, ast.Attribute)) for item in value.elts)):
        expression = '(' + ', '.join(shape(item) for item in value.elts) + ')'
    else:
        return '', None
    if not expression:
        return '', None
    if awaited:
        expression = 'await ' + expression
    if isinstance(node, ast.Return):
        return 'return ' + expression, value
    target = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, 'target', None)
    if target is not None:
        return f'{shape(target)} = {expression}', value
    return expression, value


def _call_scores(call, parameters, focus):
    if not isinstance(call, ast.Call):
        return 0, 0
    callback = any(isinstance(arg, ast.Attribute) and arg.attr == 'commit'
                   for arg in (*call.args, *(keyword.value for keyword in call.keywords)))
    # Commit-shaped callbacks are relevant to save questions.
    commit_score = focus('save commit') if callback else 0
    parameter_score = focus(call.func.id) if isinstance(call.func, ast.Name) and call.func.id in parameters else 0
    return commit_score, parameter_score


def python_flow_declarations(parsed, focus, *, predicate_shape):
    """Read selected statements, preserving lexical owner, context and line."""
    results = []
    pending = [(parsed, '', None, ())]
    while pending:
        node, owner, parameters, contexts = pending.pop()
        if isinstance(node, _SCOPES):
            qualified = f'{owner}.{node.name}' if owner else node.name
            names = None
            if isinstance(node, _FUNCTIONS):
                args = node.args
                names = {arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
            pending.extend((child, qualified, names, ()) for child in reversed(node.body))
            continue
        if parameters is not None and isinstance(node, _CONTEXTS):
            active = tuple(predicate_shape(item.context_expr) for item in node.items[:2])
            pending.extend((child, owner, parameters, (*contexts, *active)[-2:])
                           for child in reversed(node.body))
            continue
        if parameters is not None:
            statement, expression = _statement_shape(node, predicate_shape)
            if statement:
                owner_score = focus(owner)
                is_guard = isinstance(node, ast.If)
                commit_score, parameter_call_score = _call_scores(expression, parameters, focus) if not is_guard else (0, 0)
                detail_score = max(focus(statement), commit_score)
                if owner_score or detail_score:
                    # Parameter-dependent guards and tuple keys show what a
                    # focused function scopes or checks before its handoff.
                    parameter_scope = (is_guard or isinstance(expression, ast.Tuple)) and any(
                        isinstance(part, ast.Name) and part.id in parameters for part in ast.walk(expression))
                    score = owner_score + detail_score + 2 * (owner_score * parameter_scope + parameter_call_score)
                    context = f' under with {", ".join(contexts)}' if contexts else ''
                    label = f'Python flow {owner}{context}: {statement}'
                    results.append(((-score, -detail_score, not is_guard, node.lineno), label))
        pending.extend((child, owner, parameters, contexts)
                       for child in reversed(list(ast.iter_child_nodes(node)))
                       if not isinstance(child, ast.expr))

    return [(key[-1], statement) for key, statement in sorted(results)[:_MAX_ROWS]]


def _main_guard(node):
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    test = node.test
    return (len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)
            and any(isinstance(left, ast.Name) and left.id == '__name__'
                    and isinstance(right, ast.Constant) and right.value == '__main__'
                    for left, right in ((test.left, test.comparators[0]), (test.comparators[0], test.left))))


def _trace_body(statements):
    """Traverse only this body's syntax with its two closest conditional guards."""
    pending = [(node, ()) for node in reversed(statements)]
    for _ in range(_TRACE_NODES):
        if not pending:
            break
        node, guards = pending.pop()
        if isinstance(node, (*_SCOPES, ast.Lambda)):
            continue
        yield node, guards
        if isinstance(node, ast.If):
            for truth, items in ((False, node.orelse), (True, node.body)):
                if isinstance(node.test, ast.Constant) and type(node.test.value) is bool and node.test.value != truth:
                    continue
                pending.extend((child, (*guards, (node.test, truth))[-2:]) for child in reversed(items))
            pending.append((node.test, guards))
        else:
            pending.extend((child, guards) for child in reversed(list(ast.iter_child_nodes(node))))


def _trace_receiver_types(body, classes, owner):
    assignments, stores = {}, set()
    for node, _ in body:
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if node.id in stores:
                assignments.pop(node.id, None)
            stores.add(node.id)
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else getattr(node, 'target', None)
        value = node.value
        if (isinstance(target, ast.Name) and target.id not in stores and isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name) and value.func.id in classes):
            assignments[target.id] = (value.func.id, node.lineno)
    # A method may use only its own class's receiver. Other receiver types
    # require one syntactically unambiguous constructor assignment.
    if '.' in owner:
        cls = owner.split('.')[0]
        for name in ('self', 'cls'):
            if name not in stores:
                assignments[name] = (cls, 0)
    return assignments, stores


def _trace_assignment_shapes(body, shape):
    assignments = {}
    for node, _ in body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        value = node.value
        attribute = ''
        if isinstance(value, ast.Attribute):
            attribute = f'.{value.attr}'
            value = value.value
        if isinstance(value, ast.Call):
            assignments[id(value)] = (f'{shape(target)} = ', attribute)
    return assignments


def _trace_rank(leaf, condition, local):
    name = leaf.casefold()
    score = sum(term in name for term in _TRACE_PRIORITY_TERMS) * 4
    score += sum(term in condition for term in _TRACE_PRIORITY_TERMS)
    score += 12 if name in {'main', 'run'} or local and name == 'execute' else 0
    score += 2 if local else 0
    if name.startswith(('log_', 'get_', 'mols_from_')) and 'checkpoint' not in name:
        score -= 4
    return -score


def _trace_group_rank(owner, priority):
    leaf = owner.rsplit('.', 1)[-1].casefold()
    return (_trace_rank(leaf, '', True), priority)


def python_entrypoint_trace(parsed, entrypoints=(), *, predicate_shape):
    """Trace direct static call syntax from declared or main-guard entrypoints."""
    classes = {node.name for node in parsed.body if isinstance(node, ast.ClassDef)}
    definitions = {node.name: node for node in parsed.body if isinstance(node, _FUNCTIONS)}
    for node in parsed.body:
        if isinstance(node, ast.ClassDef):
            definitions.update((f'{node.name}.{method.name}', method)
                               for method in node.body if isinstance(method, _FUNCTIONS))
    guarded = [call.func.id for node in parsed.body if _main_guard(node)
               for call, _ in _trace_body(node.body)
               if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)]
    roots = list(dict.fromkeys(name for name in (*entrypoints, *guarded) if name in definitions))
    pending = [(-100, 0, name) for name in roots[:_TRACE_ROUTINES]]
    heapq.heapify(pending)
    visited, called_names, groups = set(), set(), []
    while pending and len(visited) < _TRACE_ROUTINES:
        _, depth, owner = heapq.heappop(pending)
        if owner in visited:
            continue
        visited.add(owner)
        body = list(_trace_body(definitions[owner].body))
        receivers, shadows = _trace_receiver_types(body, classes, owner)
        arguments = definitions[owner].args
        shadows.update(arg.arg for arg in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs,
                                           arguments.vararg, arguments.kwarg) if arg)
        assignments = _trace_assignment_shapes(body, predicate_shape)
        candidates = []
        for node, guards in body:
            if not isinstance(node, ast.Call):
                continue
            shape = _call_shape(node, predicate_shape, max_arguments=2)
            if isinstance(node.func, ast.Name):
                root, leaf = node.func.id, node.func.id
                target = None if root in shadows else root + '.__init__' if root in classes else root
            elif isinstance(node.func, ast.Attribute):
                leaf, source = node.func.attr, node.func.value
                while isinstance(source, ast.Attribute):
                    source = source.value
                if not isinstance(source, ast.Name):
                    continue
                root = source.id
                receiver, line = receivers.get(root, (None, 0))
                target = f'{receiver}.{leaf}' if receiver and source is node.func.value and line < node.lineno else None
            else:
                continue
            if root not in shadows:
                called_names.add(root)
            if not shape or leaf in _TRACE_ROUTINE_CALLS:
                continue
            condition = ' under ' + ' and '.join(
                ('' if truth else 'else of ') + f'if {predicate_shape(guard)}' for guard, truth in guards
            ) if guards else ''
            prefix, suffix = assignments.get(id(node), ('', ''))
            label = f'Python entrypoint flow {owner}{condition}: {prefix}{shape}{suffix}'
            priority = _trace_rank(leaf, condition.casefold(), target in definitions)
            rank = (priority, not guards, node.lineno, node.col_offset)
            candidates.append((rank, node.lineno, label, target))
        candidates.sort()
        rows = list(dict.fromkeys((number, label) for _, number, label, _ in candidates))
        if rows:
            groups.append((depth, _trace_group_rank(owner, candidates[0][0][0]), deque(rows[:_TRACE_ROUTINE_ROWS])))
        if depth < _TRACE_DEPTH:
            targets = {}
            for (priority, *_), _, _, target in candidates:
                if target in definitions and target not in visited:
                    targets.setdefault(target, min(_trace_group_rank(target, priority)))
            for target, priority in sorted(targets.items(), key=lambda pair: pair[1])[:_TRACE_ROUTINES]:
                heapq.heappush(pending, (priority + (depth + 1) * 2, depth + 1, target))
    rows = []
    # Show several handoffs from each entrypoint before distributing the
    # remainder across callees, including deeper pipeline steps.
    for depth, _, group in groups:
        if depth != 0:
            continue
        for _ in range(min(4, len(group), _MAX_ROWS - len(rows))):
            rows.append(group.popleft())
    groups = [group for _, _, group in sorted(groups, key=lambda group: (group[1], group[0])) if group]
    while groups and len(rows) < _MAX_ROWS:
        for group in groups:
            if len(rows) == _MAX_ROWS:
                break
            rows.append(group.popleft())
        groups = [group for group in groups if group]
    return rows, called_names
