import { useId, type ReactNode } from 'react';

type Cited = { source_ids: string[] };
export type MapNode = Cited & { id: string; label: string };
export type MapEdge = Cited & { from: string; to: string; label: string; inferred: boolean };
export type ProjectMapData = { nodes: MapNode[]; edges: MapEdge[] };

function labelLines(label: string) {
  const chars = Array.from(label);
  if (chars.length <= 22) return [label];
  const split = chars.slice(0, 22).lastIndexOf(' ');
  const index = split > 7 ? split : 22;
  const remaining = chars.slice(index).join('').trim();
  return [chars.slice(0, index).join(''), Array.from(remaining).length > 22
    ? Array.from(remaining).slice(0, 21).join('') + '…'
    : remaining];
}

function MapDrawing({ nodes, edges }: ProjectMapData) {
  const markerId = useId();
  const cols = Math.min(nodes.length, 3);
  const width = 196;
  const height = 58;
  const inset = 44;
  const columnGap = 62;
  const rowGap = 72;
  const rows = Math.ceil(nodes.length / cols);
  const positions = new Map(nodes.map((node, index) => [node.id, {
    x: inset + (index % cols) * (width + columnGap),
    y: inset + Math.floor(index / cols) * (height + rowGap),
  }]));
  const svgWidth = inset * 2 + cols * width + (cols - 1) * columnGap;
  const svgHeight = inset * 2 + rows * height + (rows - 1) * rowGap;
  return (
    <div className="project-map-drawing" tabIndex={0} role="region" aria-label="Scrollable project map">
      <svg
        width={svgWidth}
        height={svgHeight}
        viewBox={`0 0 ${svgWidth} ${svgHeight}`}
        role="img"
        aria-label="Component diagram. See the connections list for details and sources."
      >
        <defs>
          <marker id={markerId} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#51665b" />
          </marker>
        </defs>
        {edges.map((edge, index) => {
          const from = positions.get(edge.from);
          const to = positions.get(edge.to);
          if (!from || !to) return null;
          const offset = (index % 7) * 3;
          const a = from.x + width / 2 + offset;
          const b = to.x + width / 2 + offset;
          let path: string;
          if (from === to) {
            const x = from.x + width + 19 + offset;
            path = `M ${from.x + width} ${from.y + 20} H ${x} V ${from.y + 40} H ${from.x + width}`;
          } else if (from.y === to.y) {
            const y = from.y + height + 12 + offset;
            path = `M ${a} ${from.y + height} V ${y} H ${b} V ${to.y + height}`;
          } else {
            const down = from.y < to.y;
            const start = from.y + (down ? height : 0);
            const end = to.y + (down ? 0 : height);
            const y = start + (down ? 24 : -24) + offset;
            const lane = to.x - 12 - offset;
            const approach = end + (down ? -12 : 12);
            path = `M ${a} ${start} V ${y} H ${lane} V ${approach} H ${b} V ${end}`;
          }
          return (
            <path key={index} d={path} fill="none" stroke="#51665b" strokeWidth="1.5"
              strokeDasharray={edge.inferred ? '6 4' : undefined} markerEnd={`url(#${markerId})`} />
          );
        })}
        {nodes.map((node) => {
          const { x, y } = positions.get(node.id)!;
          const lines = labelLines(node.label);
          return (
            <g key={node.id}>
              <title>{node.label}</title>
              <rect x={x} y={y} width={width} height={height} rx={7} fill="#f5faf7" stroke="#7a9485" />
              {lines.map((line, index) => (
                <text key={index} x={x + width / 2}
                  y={y + height / 2 + (index - (lines.length - 1) / 2) * 18}
                  textAnchor="middle" dominantBaseline="middle" fontSize="13" fill="#24312a">
                  {line}
                </text>
              ))}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function ProjectMap({
  map,
  references,
}: {
  map: ProjectMapData;
  references: (ids: string[]) => ReactNode;
}) {
  const labels = new Map(map.nodes.map(({ id, label }) => [id, label]));
  const edges = map.edges.filter(({ from, to }) => labels.has(from) && labels.has(to));
  return (
    <>
      <MapDrawing nodes={map.nodes} edges={edges} />
      {edges.some((edge) => edge.inferred) && <p>Dashed arrows are AI inferences.</p>}
      <h3>Components</h3>
      <ul className="brief-points">
        {map.nodes.map((node) => (
          <li key={node.id}>{node.label}{references(node.source_ids)}</li>
        ))}
      </ul>
      <h3>Connections</h3>
      {edges.length ? (
        <ul className="brief-points">
          {edges.map((edge, index) => (
            <li key={index}>
              <strong>{labels.get(edge.from)} → {labels.get(edge.to)}</strong>
              {edge.label ? `: ${edge.label}` : ''}
              {edge.inferred && ' (inferred)'}
              {references(edge.source_ids)}
            </li>
          ))}
        </ul>
      ) : <p>No connections identified in the sources.</p>}
    </>
  );
}
