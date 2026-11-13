import { useEffect, useMemo, useRef, useState } from "react";

export interface Point { x: number; y: number | null }
export interface Series { label: string; color: string; points: Point[]; dashed?: boolean; thin?: boolean }
export interface Guide { at: number; label: string; tone?: "accent" | "muted" }

interface Props {
  series: Series[];
  xFormat: (x: number) => string;
  yFormat: (y: number) => string;
  height?: number;
  area?: boolean;
  logX?: boolean;
  hGuides?: Guide[];
  vGuides?: Guide[];
  xTicks?: number[];
  ariaLabel: string;
}

const M = { top: 14, right: 62, bottom: 26, left: 6 };

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

function niceTicks(lo: number, hi: number, count = 4): number[] {
  const span = hi - lo || Math.abs(hi) || 1;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const ticks = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi + step * 1e-9; t += step) ticks.push(+t.toPrecision(12));
  return ticks;
}

export default function LineChart({
  series, xFormat, yFormat, height = 300, area, logX, hGuides = [], vGuides = [], xTicks, ariaLabel,
}: Props) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);

  const lead = series[0]?.points ?? [];
  const geometry = useMemo(() => {
    const ys = series.flatMap((s) => s.points.map((p) => p.y)).filter((y): y is number => y !== null);
    ys.push(...hGuides.map((g) => g.at));
    if (!ys.length || !lead.length || width <= 0) return null;
    let lo = Math.min(...ys), hi = Math.max(...ys);
    const pad = (hi - lo || Math.abs(hi) || 1) * 0.08;
    lo -= pad; hi += pad;
    const tx = (x: number) => (logX ? Math.log10(Math.max(x, 1e-9)) : x);
    const x0 = tx(lead[0].x), x1 = tx(lead[lead.length - 1].x);
    const w = width - M.left - M.right, h = height - M.top - M.bottom;
    return {
      lo, hi, w, h,
      sx: (x: number) => M.left + ((tx(x) - x0) / (x1 - x0 || 1)) * w,
      sy: (y: number) => M.top + (1 - (y - lo) / (hi - lo)) * h,
    };
  }, [series, hGuides, lead, width, height, logX]);

  if (!geometry) return <div ref={ref} style={{ height }} className="chart" />;
  const { sx, sy, lo, hi, w, h } = geometry;

  const path = (points: Point[]) => {
    let d = "", pen = false;
    for (const p of points) {
      if (p.y === null) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`;
      pen = true;
    }
    return d;
  };

  const onMove = (event: React.PointerEvent) => {
    const box = event.currentTarget.getBoundingClientRect();
    const px = event.clientX - box.left;
    let best = 0, gap = Infinity;
    lead.forEach((p, i) => {
      const d = Math.abs(sx(p.x) - px);
      if (d < gap) { gap = d; best = i; }
    });
    setHover(best);
  };

  const ticks = niceTicks(lo, hi);
  const xs = xTicks ?? [0.02, 0.26, 0.5, 0.74, 0.98].map((f) => lead[Math.round(f * (lead.length - 1))].x);
  const at = hover === null ? null : lead[hover];
  const tipLeft = at ? sx(at.x) : 0;
  const first = lead.find((p) => p.y !== null), last = [...lead].reverse().find((p) => p.y !== null);

  return (
    <div ref={ref} className="chart" style={{ height }}>
      <svg width={width} height={height} role="img" aria-label={ariaLabel}
        onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid" x1={M.left} x2={M.left + w} y1={sy(t)} y2={sy(t)} />
            <text className="tick" x={M.left + w + 8} y={sy(t) + 4}>{yFormat(t)}</text>
          </g>
        ))}
        {xs.map((x, i) => (
          <text key={i} className="tick" x={sx(x)} y={height - 6}
            textAnchor={i === 0 ? "start" : i === xs.length - 1 ? "end" : "middle"}>{xFormat(x)}</text>
        ))}
        {hGuides.map((g) => (
          <g key={g.label}>
            <line className={`guide ${g.tone ?? "muted"}`} x1={M.left} x2={M.left + w} y1={sy(g.at)} y2={sy(g.at)} />
            <text className="guide-label" x={M.left + 4} y={sy(g.at) - 5}>{g.label}</text>
          </g>
        ))}
        {vGuides.map((g) => (
          <g key={g.label}>
            <line className={`guide ${g.tone ?? "accent"}`} x1={sx(g.at)} x2={sx(g.at)} y1={M.top} y2={M.top + h} />
            <text className="guide-label" y={M.top + 10} x={sx(g.at) + (sx(g.at) > M.left + w * 0.7 ? -6 : 6)}
              textAnchor={sx(g.at) > M.left + w * 0.7 ? "end" : "start"}>{g.label}</text>
          </g>
        ))}
        {area && first && last && (
          <path d={`${path(lead)}L${sx(last.x).toFixed(1)},${M.top + h}L${sx(first.x).toFixed(1)},${M.top + h}Z`}
            fill={series[0].color} opacity={0.12} />
        )}
        {[...series].reverse().map((s) => (
          <path key={s.label} d={path(s.points)} fill="none" stroke={s.color}
            strokeWidth={s.thin ? 1.25 : 2} strokeDasharray={s.dashed ? "4 4" : undefined}
            strokeLinejoin="round" strokeLinecap="round" />
        ))}
        {at && (
          <g pointerEvents="none">
            <line className="crosshair" x1={tipLeft} x2={tipLeft} y1={M.top} y2={M.top + h} />
            {series.map((s) => {
              const y = s.points[hover!]?.y;
              return y === null || y === undefined ? null : (
                <circle key={s.label} cx={tipLeft} cy={sy(y)} r={4.5} fill={s.color} className="dot" />
              );
            })}
          </g>
        )}
      </svg>
      {at && (
        <div className="tip" style={tipLeft > width / 2 ? { right: width - tipLeft + 12 } : { left: tipLeft + 12 }}>
          <div className="tip-x">{xFormat(at.x)}</div>
          {series.map((s) => {
            const y = s.points[hover!]?.y;
            return y === null || y === undefined ? null : (
              <div key={s.label} className="tip-row">
                <i style={{ background: s.color }} />
                <span>{s.label}</span>
                <b>{yFormat(y)}</b>
              </div>
            );
          })}
        </div>
      )}
      {series.length > 1 && (
        <div className="legend">
          {series.map((s) => (
            <span key={s.label}><i style={{ background: s.color }} className={s.dashed ? "dashed" : ""} />{s.label}</span>
          ))}
        </div>
      )}
    </div>
  );
}

/** Bars with a hover read-out. `value` may be negative; bars grow from zero. */
export function Bars({ items, format, height = 220, ariaLabel, color }: {
  items: { label: string; value: number; color?: string; tip?: string }[];
  format: (v: number) => string; height?: number; ariaLabel: string; color?: string;
}) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const top = 10, bottom = 8;
  const max = Math.max(0, ...items.map((i) => i.value)), min = Math.min(0, ...items.map((i) => i.value));
  const span = max - min || 1;
  const sy = (v: number) => top + ((max - v) / span) * (height - top - bottom);
  const slot = width / Math.max(items.length, 1);
  const gap = slot > 14 ? 2 : slot > 5 ? 1 : 0;
  const radius = Math.min(4, Math.max(0, (slot - gap) / 2));
  const picked = hover === null ? null : items[hover];

  return (
    <div ref={ref} className="chart" style={{ height }}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={ariaLabel} onPointerLeave={() => setHover(null)}>
          <line className="grid" x1={0} x2={width} y1={sy(0)} y2={sy(0)} />
          {items.map((item, i) => {
            const y = Math.min(sy(item.value), sy(0)), barH = Math.max(Math.abs(sy(item.value) - sy(0)), 1);
            return (
              <g key={i} onPointerEnter={() => setHover(i)}>
                <rect x={i * slot} y={0} width={slot} height={height} fill="transparent" />
                <rect x={i * slot + gap / 2} y={y} width={Math.max(slot - gap, 1)} height={barH}
                  rx={Math.min(radius, barH / 2)} fill={item.color ?? color ?? "var(--mark)"}
                  opacity={hover === null || hover === i ? 1 : 0.45} />
              </g>
            );
          })}
        </svg>
      )}
      {picked && (
        <div className="tip" style={hover! * slot > width / 2
          ? { right: width - hover! * slot + 8 } : { left: (hover! + 1) * slot + 8 }}>
          <div className="tip-x">{picked.tip ?? picked.label}</div>
          <div className="tip-row"><b>{format(picked.value)}</b></div>
        </div>
      )}
    </div>
  );
}
