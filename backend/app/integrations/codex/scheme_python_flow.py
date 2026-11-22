"""Summarize bounded Python control flow without forwarding source literals."""

import ast


_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = (*_FUNCTIONS, ast.ClassDef)
_CONTEXTS = (ast.With, ast.AsyncWith)
_MAX_ROWS = 32
_MAX_ARGUMENTS = 8


def _call_shape(call, shape):
    if not isinstance(call.func, (ast.Name, ast.Attribute)):
        return ''
    arguments = [shape(arg) for arg in call.args[:_MAX_ARGUMENTS]]
    arguments.extend(
        f'{keyword.arg}={shape(keyword.value)}' if keyword.arg else '**<expression>'
        for keyword in call.keywords[:_MAX_ARGUMENTS - len(arguments)]
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
