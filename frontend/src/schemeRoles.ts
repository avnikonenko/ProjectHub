export type SchemeNodeRole = 'start' | 'end' | 'step';

/** Classify graph endpoints by connectivity, never by node order or labels. */
export function schemeNodeRoles(
  nodes: { id: string }[],
  edges: { source: string; target: string }[],
): Map<string, SchemeNodeRole> {
  const ids = new Set(nodes.map(node => node.id));
  const incoming = new Set<string>();
  const outgoing = new Set<string>();
  for (const edge of edges) {
    if (!ids.has(edge.source) || !ids.has(edge.target)) continue;
    outgoing.add(edge.source);
    incoming.add(edge.target);
  }
  return new Map([...ids].map(id => [id,
    !incoming.has(id) && outgoing.has(id) ? 'start'
      : incoming.has(id) && !outgoing.has(id) ? 'end' : 'step',
  ]));
}
