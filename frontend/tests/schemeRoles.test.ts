import assert from 'node:assert/strict';
import test from 'node:test';
import { schemeNodeRoles } from '../src/schemeRoles.ts';

const nodes = (...ids: string[]) => ids.map(id => ({ id }));
const edge = (source: string, target: string) => ({ source, target });

test('classifies starts, intermediate steps and ends independently of input order', () => {
  assert.deepEqual(schemeNodeRoles(nodes('end', 'middle', 'start'), [edge('start', 'middle'), edge('middle', 'end')]),
    new Map([['end', 'end'], ['middle', 'step'], ['start', 'start']]));
});

test('supports multiple inputs and outputs in branching workflows', () => {
  const roles = schemeNodeRoles(nodes('a', 'b', 'join', 'x', 'y'),
    [edge('a', 'join'), edge('b', 'join'), edge('join', 'x'), edge('join', 'y')]);
  assert.equal(roles.get('a'), 'start');
  assert.equal(roles.get('b'), 'start');
  assert.equal(roles.get('join'), 'step');
  assert.equal(roles.get('x'), 'end');
  assert.equal(roles.get('y'), 'end');
});

test('isolates, cycles and self loops remain ordinary steps', () => {
  assert.deepEqual([...schemeNodeRoles(nodes('a', 'b', 'alone', 'loop'),
    [edge('a', 'b'), edge('b', 'a'), edge('loop', 'loop')]).values()],
    ['step', 'step', 'step', 'step']);
});

test('ignores dangling edges and leaves input records unchanged', () => {
  const input = nodes('a', 'b');
  const edges = [edge('missing', 'a'), edge('b', 'missing')];
  const before = structuredClone({ input, edges });
  assert.deepEqual([...schemeNodeRoles(input, edges)], [['a', 'step'], ['b', 'step']]);
  assert.deepEqual({ input, edges }, before);
  assert.equal(schemeNodeRoles([], edges).size, 0);
});
