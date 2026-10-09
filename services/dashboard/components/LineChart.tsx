// Small dependency-free SVG line chart with optional horizontal levels and point marks.

type Levels = Partial<Record<"entry" | "stop" | "target", number | null | undefined>>;

export function LineChart({
  values,
  width = 640,
  height = 160,
  levels,
  baseline,
  marks,
  labels = true,
}: {
  values: number[];
  width?: number;
  height?: number;
  levels?: Levels;
  baseline?: number | null;
  marks?: { index: number; kind: "entry" | "exit" }[];
  labels?: boolean;
}) {
  if (!values.length) {
    return (
      <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="no data">
        <text x={12} y={24} className="muted-text">
          no data yet
        </text>
      </svg>
    );
  }
  const lv = Object.entries(levels || {}).filter(([, v]) => v != null) as [string, number][];
  const pool = [...values, ...lv.map(([, v]) => v), ...(baseline != null ? [baseline] : [])];
  let lo = Math.min(...pool);
  let hi = Math.max(...pool);
  if (hi - lo < 1e-9) {
    hi += 1;
    lo -= 1;
  }
  const [pl, pr, pt, pb] = labels ? [8, 70, 10, 14] : [2, 2, 2, 2];
  const iw = width - pl - pr;
  const ih = height - pt - pb;
  const n = Math.max(values.length - 1, 1);
  const x = (i: number) => pl + (iw * i) / n;
  const y = (v: number) => pt + ih * (1 - (v - lo) / (hi - lo));
  const pts = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const up = values[values.length - 1] >= (baseline ?? values[0]);
  const fmt = (v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
  return (
    <svg className="chart" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img">
      {baseline != null && <line x1={pl} x2={width - pr} y1={y(baseline)} y2={y(baseline)} className="lvl lvl-base" />}
      {lv.map(([name, v]) => (
        <g key={name}>
          <line x1={pl} x2={width - pr} y1={y(v)} y2={y(v)} className={`lvl lvl-${name}`} />
          {labels && (
            <text x={width - pr + 4} y={y(v) + 4} className={`lvl-label lvl-${name}`}>
              {name} {fmt(v)}
            </text>
          )}
        </g>
      ))}
      <polyline points={pts} className={`series ${up ? "up" : "down"}`} fill="none" />
      {marks?.map((m) =>
        m.index >= 0 && m.index < values.length ? (
          <circle key={m.kind} cx={x(m.index)} cy={y(values[m.index])} r={3.5} className={`mark m-${m.kind}`} />
        ) : null,
      )}
      {labels && (
        <>
          <text x={width - pr + 4} y={pt + 8} className="axis">
            {fmt(hi)}
          </text>
          <text x={width - pr + 4} y={height - pb} className="axis">
            {fmt(lo)}
          </text>
        </>
      )}
    </svg>
  );
}
