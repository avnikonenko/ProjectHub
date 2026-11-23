"""Find bounded, function-local Python handoffs without executing repository code."""

import ast
from collections import defaultdict
from dataclasses import dataclass


_MAX_STEPS = 3000
_MAX_PATHS = 8
_MAX_ROWS = 12


@dataclass(frozen=True)
class _Binding:
    line: int
    value: str
    guard: str
    producer: str = ''


def _call_name(call, shape):
    return shape(call.func) if isinstance(call.func, (ast.Name, ast.Attribute)) else ''


def _direct_arguments(call):
    return tuple(dict.fromkeys(
        value.id for value in (*call.args, *(kw.value for kw in call.keywords if kw.arg))
        if isinstance(value, ast.Name)
    ))


def _may_terminate(statement):
    return any(isinstance(node, (ast.Return, ast.Raise)) for node in ast.walk(statement))


def local_handoff_rows(parsed, focus, *, shape):
    """Return ranked line-anchored bundles for explicit local relationships."""
    rows = []
    steps = _MAX_STEPS
    for function in (node for node in ast.walk(parsed) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
        if steps <= 0:
            break
        facts = defaultdict(dict)
        scores, visits = {}, defaultdict(int)
        arguments = function.args
        inputs = {arg.arg: _Binding(arg.lineno, 'parameter', '') for arg in
                  (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)}
        # Nested declarations, including a global in a branch, are conservatively excluded.
        if any(isinstance(node, (ast.Global, ast.Nonlocal)) for node in ast.walk(function)):
            continue
        stack = [(list(reversed(function.body)), inputs, {}, (), ())]
        created_paths = 1
        while stack and steps > 0:
            pending, bindings, previous, guards, path = stack.pop()
            while pending and steps > 0:
                statement = pending.pop()
                if isinstance(statement, tuple):
                    guards = statement
                    continue
                steps -= 1
                if isinstance(statement, ast.If):
                    if isinstance(statement.test, ast.Constant):
                        pending.extend(reversed(statement.body if statement.test.value else statement.orelse))
                        continue
                    if (len(guards) >= 3 or created_paths >= _MAX_PATHS
                            or any(isinstance(node, ast.NamedExpr) for node in ast.walk(statement.test))):
                        if _may_terminate(statement):
                            break
                        bindings.clear()
                        previous.clear()
                        path = ()
                        continue
                    test = shape(statement.test)
                    created_paths += 1
                    inverse = (shape(statement.test.operand)
                               if isinstance(statement.test, ast.UnaryOp) and isinstance(statement.test.op, ast.Not)
                               else f'not {test}')
                    opposite = f'{inverse} at L{statement.lineno}'
                    taken = f'{test} at L{statement.lineno}'
                    stack.append((pending + [guards, *reversed(statement.orelse)], bindings.copy(), previous.copy(),
                                  (*guards, opposite), (*path, opposite)))
                    pending.extend([guards, *reversed(statement.body)])
                    guards = (*guards, taken)
                    path = (*path, taken)
                    continue
                if isinstance(statement, ast.Raise):
                    break
                current_guard = ' and '.join(guards)
                target = None
                value = None
                if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                    target, value = statement.targets[0], statement.value
                elif isinstance(statement, ast.AnnAssign):
                    target, value = statement.target, statement.value
                    if value is None:
                        continue
                elif isinstance(statement, (ast.Expr, ast.Return)):
                    value = statement.value
                elif isinstance(statement, ast.Pass):
                    continue
                else:
                    if _may_terminate(statement):
                        break
                    bindings.clear()
                    previous.clear()
                    continue

                if value is not None and any(isinstance(node, ast.NamedExpr) for node in ast.walk(value)):
                    bindings.clear()
                    previous.clear()
                    if isinstance(statement, ast.Return):
                        break
                    continue
                value = value.value if isinstance(value, ast.Await) else value
                call = value if isinstance(value, ast.Call) else None
                if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Call):
                    call = value.value
                name = _call_name(call, shape) if call is not None else ''
                if name:
                    key = (call.lineno, call.col_offset, name)
                    visits[key] += 1
                    score = focus(name)
                    scores[key] = max(scores.get(key, 0), score)
                    guard_label = f' under {current_guard}' if current_guard else ''
                    for argument in _direct_arguments(call):
                        binding = bindings.get(argument)
                        if binding is None:
                            fact = f'Unresolved input {argument}{guard_label}'
                        elif binding.value == 'parameter':
                            continue
                        elif binding.producer:
                            source_guard = f' under {binding.guard}' if binding.guard else ''
                            fact = (f'Direct value transfer {binding.value} via {argument} from L{binding.line}'
                                    f'{source_guard}{guard_label if current_guard != binding.guard else ""}')
                            scores[key] = max(scores[key], focus(binding.producer))
                        else:
                            source_guard = f' under {binding.guard}' if binding.guard else ''
                            fact = (f'Input {argument} = {binding.value} at L{binding.line}'
                                    f'{source_guard}{guard_label if current_guard != binding.guard else ""}')
                        facts[key].setdefault(fact, []).append(path)
                    if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
                        receiver = call.func.value.id
                        if (binding := bindings.get(receiver)) is not None:
                            receiver_key = receiver, binding
                            if receiver_key in previous:
                                earlier, line, earlier_guard = previous[receiver_key]
                                guard = ' and '.join(dict.fromkeys(x for x in (earlier_guard, current_guard) if x))
                                if guard:
                                    facts[key].setdefault(
                                        f'Conditional sequence on {receiver}: {earlier} at L{line} before {name} under {guard}',
                                        []).append(path)
                                    scores[key] = max(scores[key], focus(earlier))
                            previous[receiver_key] = name, call.lineno, current_guard
                if isinstance(target, ast.Name):
                    rendered = shape(value)
                    if name:
                        rendered = name + ('().' + value.attr if isinstance(value, ast.Attribute) else '()')
                    elif isinstance(value, ast.Constant):
                        rendered = 'fixed literal (value omitted)'
                    elif isinstance(value, ast.Name):
                        rendered = f'{value.id} (alias unresolved)'
                    bindings[target.id] = _Binding(statement.lineno, rendered, current_guard, name)
                    previous = {key: earlier for key, earlier in previous.items() if key[0] != target.id}
                elif target is not None:
                    # Attribute writes and destructuring can change a receiver or an alias.
                    bindings.clear()
                    previous.clear()
                if isinstance(statement, ast.Return):
                    break
        if steps <= 0 or stack:
            # A truncated path set cannot establish all inputs at a join.
            break
        for key, details in facts.items():
            score = scores[key]
            if score and details:
                line, column, name = key
                parts = []
                for fact, paths in details.items():
                    if len(paths) < visits[key] and any(paths):
                        conditions = sorted({' and '.join(guards) for guards in paths if guards})
                        fact += ' [reaches on ' + ' or '.join(conditions) + ']'
                    elif paths:
                        common = [guard for guard in paths[0] if guard not in fact
                                  and all(guard in other for other in paths[1:])]
                        if common:
                            fact += ' [reaches under ' + ' and '.join(common) + ']'
                    parts.append(fact)
                supported = any(not fact.startswith('Unresolved') for fact in details)
                rows.append((not supported, -score, line, column, f'Local handoff into {name}: ' + ' | '.join(parts)))
    return [(line, description) for _, _, line, _, description in sorted(rows)[:_MAX_ROWS]]
