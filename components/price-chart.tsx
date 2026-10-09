"use client";

import { useMemo, useState } from "react";
import { money, dateLabel, finite } from "@/lib/format";
import type { PricePoint } from "@/lib/types";

const RANGES = [
  { label: "1M", days: 31 },
  { label: "6M", days: 183 },
  { label: "1Y", days: 365 },
  { label: "5Y", days: 1826 },
] as const;

export function PriceChart({ points, currency, ticker }: { points: PricePoint[]; currency: string; ticker: string }) {
  const [range, setRange] = useState<(typeof RANGES)[number]["label"]>("1Y");
  const visible = useMemo(() => {
    const selected = RANGES.find((item) => item.label === range) ?? RANGES[2];
    const latestDate = points.length ? new Date(points[points.length - 1].date).getTime() : 0;
    const cutoff = latestDate - selected.days * 86_400_000;
    const filtered = points.filter((point) => new Date(point.date).getTime() >= cutoff);
    return filtered.length > 1 ? filtered : points.slice(-Math.min(points.length, 2));
  }, [points, range]);
  const values = visible.map((point) => point.close).filter(finite);
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 0;
  const width = 720;
  const height = 208;
  const inset = { x: 5, y: 10 };
  const spread = max - min || Math.max(max * 0.02, 1);
  const coords = visible.map((point, index) => ({
    point,
    x: inset.x + (index / Math.max(visible.length - 1, 1)) * (width - inset.x * 2),
    y: height - inset.y - ((point.close - min) / spread) * (height - inset.y * 2),
  }));
  const path = coords.map(({ x, y }, index) => `${index ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  const first = values[0];
  const last = values[values.length - 1];
  const move = first && last ? (last - first) / Math.abs(first) : null;

  return (
    <section className="chart-panel" aria-labelledby="price-chart-heading">
      <div className="panel-heading chart-heading">
        <div>
          <h2 id="price-chart-heading">Share price</h2>
          <p>Daily closing prices · Yahoo Finance · provider data may be delayed</p>
        </div>
        <div className="range-control" role="group" aria-label="Chart period">
          {RANGES.map((item) => <button key={item.label} type="button" aria-pressed={range === item.label} onClick={() => setRange(item.label)}>{item.label}</button>)}
        </div>
      </div>
      {coords.length > 1 ? (
        <>
          <div className="chart-plot">
            <div className="chart-axis-labels"><span>{money(max, currency)}</span><span>{money(min, currency)}</span></div>
            <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="chart-svg-title chart-svg-desc" preserveAspectRatio="none">
              <title id="chart-svg-title">{ticker} share price history</title>
              <desc id="chart-svg-desc">Daily adjusted closing prices from {dateLabel(visible[0]?.date)} through {dateLabel(visible.at(-1)?.date)}. Values shown in {currency}.</desc>
              <line x1="0" x2={width} y1={height * 0.25} y2={height * 0.25} className="chart-gridline" />
              <line x1="0" x2={width} y1={height * 0.5} y2={height * 0.5} className="chart-gridline" />
              <line x1="0" x2={width} y1={height * 0.75} y2={height * 0.75} className="chart-gridline" />
              <path d={path} className={`chart-line ${move !== null && move < 0 ? "is-down" : ""}`} vectorEffect="non-scaling-stroke" />
            </svg>
          </div>
          <div className="chart-foot"><span>{dateLabel(visible[0]?.date)}</span><span>{dateLabel(visible.at(-1)?.date)}</span></div>
          <p className="chart-summary">{visible.length} observations · {first && last ? `${move! >= 0 ? "Increased" : "Decreased"} ${Math.abs(move! * 100).toFixed(1)}% over this displayed period` : "Change unavailable"}</p>
        </>
      ) : <div className="empty-inline">Price history is unavailable from Yahoo Finance.</div>}
    </section>
  );
}
