"use client";

import { formatMoney, pnlClass, signOf, toChartNumber, type DecimalString } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface BarDatum {
  label: string;
  value: DecimalString;
  /** Optional secondary line under the label (trade count, win rate). */
  meta?: string;
}

/**
 * Horizontal bar chart for breakdowns (by symbol, setup, weekday, hour).
 *
 * Plain SVG-free CSS bars: the data is a short ranked list, and a charting library would add
 * weight without adding meaning. Bars are scaled against the largest **absolute** value so gains
 * and losses stay visually comparable, and every row also shows its exact figure — the bar is a
 * reading aid, not the source of truth.
 */
export function BreakdownBars({
  data,
  currency = "USD",
  emptyMessage = "No data yet.",
  className,
  maxRows = 12,
}: {
  data: BarDatum[];
  currency?: string;
  emptyMessage?: string;
  className?: string;
  maxRows?: number;
}) {
  if (data.length === 0) {
    return <p className={cn("py-6 text-center text-xs text-faint", className)}>{emptyMessage}</p>;
  }

  const rows = data.slice(0, maxRows);
  const largest = Math.max(...rows.map((row) => Math.abs(toChartNumber(row.value))), 1);

  return (
    <ul className={cn("space-y-2", className)}>
      {rows.map((row) => {
        const magnitude = Math.abs(toChartNumber(row.value));
        const width = Math.max((magnitude / largest) * 100, 1.5);
        const sign = signOf(row.value);

        return (
          <li key={row.label} className="grid grid-cols-[minmax(0,7rem)_1fr_auto] items-center gap-3">
            <div className="min-w-0">
              <p className="truncate text-xs font-medium text-ink" title={row.label}>
                {row.label}
              </p>
              {row.meta ? <p className="truncate text-2xs text-faint">{row.meta}</p> : null}
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-raised" aria-hidden>
              <div
                className={cn(
                  "h-full rounded-full",
                  sign > 0 ? "bg-profit" : sign < 0 ? "bg-loss" : "bg-faint",
                )}
                style={{ width: `${width}%` }}
              />
            </div>
            <span className={cn("tnum text-xs font-medium", pnlClass(row.value))}>
              {formatMoney(row.value, currency)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** Vertical histogram, used for the R-multiple distribution. */
export function Histogram({
  buckets,
  className,
}: {
  buckets: { label: string; count: number; lower: string | null }[];
  className?: string;
}) {
  const total = buckets.reduce((sum, bucket) => sum + bucket.count, 0);
  if (total === 0) {
    return <p className={cn("py-6 text-center text-xs text-faint", className)}>No trades with a defined R multiple.</p>;
  }

  const tallest = Math.max(...buckets.map((bucket) => bucket.count), 1);

  return (
    <div className={cn("flex h-40 items-end gap-1", className)}>
      {buckets.map((bucket) => {
        // A bucket's lower bound below zero is a losing bucket.
        const losing = bucket.lower === null || Number(bucket.lower) < 0;
        const height = (bucket.count / tallest) * 100;
        return (
          <div key={bucket.label} className="flex flex-1 flex-col items-center justify-end gap-1">
            <span className="tnum text-2xs text-faint">{bucket.count || ""}</span>
            <div
              className={cn("w-full rounded-t", losing ? "bg-loss/70" : "bg-profit/70")}
              style={{ height: `${Math.max(height, bucket.count ? 3 : 0)}%` }}
              title={`${bucket.label}: ${bucket.count}`}
            />
            <span className="w-full truncate text-center text-2xs text-faint" title={bucket.label}>
              {bucket.label.replace("R to ", "–").replace("R", "")}
            </span>
          </div>
        );
      })}
    </div>
  );
}
