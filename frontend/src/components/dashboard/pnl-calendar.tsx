"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { api } from "@/lib/api";
import { formatInteger, formatMoney, formatPercent, formatPrice, pnlClass } from "@/lib/format";
import { queryKeys } from "@/lib/queries";
import type { QueryParams } from "@/lib/api";
import type { CalendarDay, CalendarDayDetail } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Drawer } from "@/components/ui/overlay";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/**
 * A trading day is a plain calendar label, not an instant.
 *
 * `new Date("2026-03-17")` parses as UTC midnight, which is the previous day anywhere west of
 * Greenwich — the calendar would render every cell one day early for users in the Americas. These
 * dates are only ever split into numbers and reassembled, never turned into a Date in local time.
 */
function parseDay(iso: string): { year: number; month: number; day: number } {
  const parts = iso.split("-").map(Number);
  return { year: parts[0] ?? 0, month: parts[1] ?? 1, day: parts[2] ?? 1 };
}

function isoOf(year: number, month: number, day: number): string {
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

/** Monday-based weekday index, computed in UTC so the host timezone cannot shift it. */
function weekdayIndex(year: number, month: number, day: number): number {
  return (new Date(Date.UTC(year, month - 1, day)).getUTCDay() + 6) % 7;
}

function daysInMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

export function PnlCalendar({
  days,
  currency,
  params,
}: {
  days: CalendarDay[];
  currency: string;
  params: QueryParams;
}) {
  const byDate = useMemo(() => new Map(days.map((d) => [d.date, d])), [days]);

  // Open on the most recent month that actually has trades, rather than today — a dashboard
  // filtered to last year should not open on an empty current month.
  const last = days.length > 0 ? days[days.length - 1] : undefined;
  const latest = last ? parseDay(last.date) : null;
  const now = new Date();
  const [cursor, setCursor] = useState(() =>
    latest
      ? { year: latest.year, month: latest.month }
      : { year: now.getFullYear(), month: now.getMonth() + 1 },
  );
  const [selected, setSelected] = useState<string | null>(null);

  const { year, month } = cursor;
  const total = daysInMonth(year, month);
  const leading = weekdayIndex(year, month, 1);

  const cells = useMemo(() => {
    const out: Array<{ iso: string; day: number; weekend: boolean } | null> = [];
    for (let i = 0; i < leading; i += 1) out.push(null);
    for (let day = 1; day <= total; day += 1) {
      const weekday = weekdayIndex(year, month, day);
      out.push({ iso: isoOf(year, month, day), day, weekend: weekday >= 5 });
    }
    return out;
  }, [year, month, total, leading]);

  // The month's own totals, summed from the cells on screen so the header cannot drift from them.
  const monthly = useMemo(() => {
    let net = 0;
    let trades = 0;
    let wins = 0;
    let green = 0;
    let red = 0;
    for (const cell of cells) {
      if (!cell) continue;
      const row = byDate.get(cell.iso);
      if (!row) continue;
      const value = Number(row.net_pnl ?? 0);
      net += value;
      trades += row.trades;
      wins += row.wins;
      if (value > 0) green += 1;
      else if (value < 0) red += 1;
    }
    return { net, trades, wins, green, red };
  }, [cells, byDate]);

  const largest = useMemo(() => {
    let max = 1;
    for (const cell of cells) {
      if (!cell) continue;
      const row = byDate.get(cell.iso);
      if (row) max = Math.max(max, Math.abs(Number(row.net_pnl ?? 0)));
    }
    return max;
  }, [cells, byDate]);

  const step = (delta: number) => {
    setCursor(({ year: y, month: m }) => {
      const next = m + delta;
      if (next < 1) return { year: y - 1, month: 12 };
      if (next > 12) return { year: y + 1, month: 1 };
      return { year: y, month: next };
    });
  };

  if (days.length === 0) {
    return <p className="py-6 text-center text-xs text-faint">No closed trades in this period.</p>;
  }

  return (
    <div>
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => step(-1)}
            aria-label="Previous month"
            className="rounded p-1 text-muted transition hover:bg-raised hover:text-fg"
          >
            <ChevronLeft className="h-4 w-4" aria-hidden />
          </button>
          <span className="min-w-[9rem] text-center text-sm font-medium">
            {MONTH_NAMES[month - 1]} {year}
          </span>
          <button
            type="button"
            onClick={() => step(1)}
            aria-label="Next month"
            className="rounded p-1 text-muted transition hover:bg-raised hover:text-fg"
          >
            <ChevronRight className="h-4 w-4" aria-hidden />
          </button>
        </div>
        <div className="text-right">
          <p
            className={cn(
              "text-sm font-semibold tabular-nums",
              monthly.net > 0 ? "text-profit" : monthly.net < 0 ? "text-loss" : "text-muted",
            )}
          >
            {formatMoney(String(monthly.net), currency, { signed: true })}
          </p>
          <p className="text-2xs text-faint">
            {monthly.green} green · {monthly.red} red · {formatInteger(monthly.trades)}{" "}
            trades
          </p>
        </div>
      </div>

      <div className="grid grid-cols-7 gap-1">
        {WEEKDAYS.map((label) => (
          <div key={label} className="pb-1 text-center text-2xs font-medium text-faint">
            {label}
          </div>
        ))}

        {cells.map((cell, index) => {
          if (!cell) return <div key={`pad-${index}`} aria-hidden />;
          const row = byDate.get(cell.iso);
          const value = row ? Number(row.net_pnl ?? 0) : 0;
          const traded = Boolean(row);
          const intensity = traded ? Math.min(Math.abs(value) / largest, 1) : 0;

          return (
            <button
              key={cell.iso}
              type="button"
              disabled={!traded}
              onClick={() => setSelected(cell.iso)}
              aria-label={
                traded
                  ? `${cell.iso}: ${formatMoney(row!.net_pnl, currency, { signed: true })} across ${row!.trades} trades`
                  : `${cell.iso}: no trades`
              }
              className={cn(
                "relative flex aspect-square flex-col justify-between rounded-md border p-1.5 text-left transition",
                traded
                  ? "border-line cursor-pointer hover:ring-2 hover:ring-accent/60"
                  : "border-line/40 cursor-default",
                !traded && cell.weekend && "bg-raised/30",
                selected === cell.iso && "ring-2 ring-accent",
              )}
              style={
                // The tokens hold bare RGB channels ("13 122 90") for Tailwind's
                // rgb(var(--profit) / <alpha-value>), so they have to be used that way here too —
                // color-mix(… var(--profit) …) is not a colour and the browser drops the whole
                // declaration, which is why the first version of this rendered flat white cells.
                traded && value !== 0
                  ? {
                      // Capped well below full strength: the P&L and the trade count sit on this
                      // tint, and at high alpha the green-on-green stops being readable.
                      backgroundColor: `rgb(var(--${value > 0 ? "profit" : "loss"}) / ${(
                        0.08 +
                        intensity * 0.24
                      ).toFixed(3)})`,
                    }
                  : undefined
              }
            >
              <span className={cn("text-2xs tabular-nums", traded ? "text-fg/70" : "text-faint")}>
                {cell.day}
              </span>
              {traded ? (
                <span className="min-w-0">
                  <span
                    className={cn(
                      "block truncate text-2xs font-semibold tabular-nums",
                      pnlClass(row!.net_pnl),
                    )}
                  >
                    {/* Whole units in a cell this small; the drawer shows the exact figure. */}
                    {formatMoney(row!.net_pnl, currency, { signed: true, places: 0 })}
                  </span>
                  <span className="block text-[10px] leading-tight text-muted">
                    {row!.trades} {row!.trades === 1 ? "trade" : "trades"}
                  </span>
                </span>
              ) : null}
            </button>
          );
        })}
      </div>

      <DayDrawer
        date={selected}
        currency={currency}
        params={params}
        onClose={() => setSelected(null)}
      />
    </div>
  );
}

function DayDrawer({
  date,
  currency,
  params,
  onClose,
}: {
  date: string | null;
  currency: string;
  params: QueryParams;
  onClose: () => void;
}) {
  const query = useQuery({
    queryKey: queryKeys.calendarDay(date ?? "", params),
    queryFn: () => api.get<CalendarDayDetail>(`/api/v1/analytics/calendar/${date}`, params),
    // The drill-down is fetched from the server rather than filtered from the cells in the
    // browser: a futures session opens at 18:00 the evening before, so selecting by calendar date
    // here would list a different set of trades than the cell was built from.
    enabled: Boolean(date),
  });

  const detail = query.data;

  return (
    <Drawer open={Boolean(date)} onClose={onClose} title={date ? `Trades on ${date}` : "Trades"}>
      {query.isLoading ? (
        <div className="space-y-2 p-4">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : !detail || detail.trades.length === 0 ? (
        <EmptyState title="No trades" description="Nothing closed on this trading day." />
      ) : (
        <div className="flex flex-col gap-4 p-4">
          <div className="grid grid-cols-2 gap-3">
            <Stat
              label="Net P&L"
              value={formatMoney(detail.summary.net_pnl, currency, { signed: true })}
              tone={pnlClass(detail.summary.net_pnl)}
            />
            <Stat label="Trades" value={formatInteger(detail.summary.trades)} />
            <Stat
              label="Win rate"
              value={
                detail.summary.win_rate === null ? "—" : formatPercent(detail.summary.win_rate)
              }
            />
            <Stat label="Costs" value={formatMoney(detail.summary.costs, currency)} />
          </div>

          <p className="text-2xs text-faint">
            Grouped by trading day in {detail.timezone}. A futures or FX session that opened the
            previous evening counts here.
          </p>

          <ul className="flex flex-col gap-2">
            {detail.trades.map((trade) => (
              <li key={trade.id} className="rounded-md border border-line bg-raised/40 p-2.5">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-sm font-medium">{trade.symbol}</span>
                  <span
                    className={cn("text-sm font-semibold tabular-nums", pnlClass(trade.net_pnl))}
                  >
                    {formatMoney(trade.net_pnl, currency, { signed: true })}
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-2xs text-muted">
                  <span className="uppercase">{trade.direction}</span>
                  <span className="tabular-nums">
                    {formatPrice(trade.entry_price)} → {formatPrice(trade.exit_price)}
                  </span>
                  {trade.r_multiple ? (
                    <span className="tabular-nums">{Number(trade.r_multiple).toFixed(2)}R</span>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Drawer>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-md border border-line bg-raised/40 p-2.5">
      <p className="text-2xs uppercase tracking-wide text-faint">{label}</p>
      <p className={cn("mt-0.5 text-sm font-semibold tabular-nums", tone)}>{value}</p>
    </div>
  );
}
