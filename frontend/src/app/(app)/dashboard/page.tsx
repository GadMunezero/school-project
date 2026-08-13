"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { ArrowRight, BookOpen, TrendingUp } from "lucide-react";

import { api } from "@/lib/api";
import {
  formatDuration,
  formatInteger,
  formatMoney,
  formatPercent,
  formatR,
  formatRatio,
  humanise,
  pnlClass,
} from "@/lib/format";
import { queryKeys } from "@/lib/queries";
import { useCurrency } from "@/lib/session";
import type { BreakdownRow, CalendarDay, DashboardResult } from "@/lib/types";
import { cn } from "@/lib/utils";
import { BreakdownBars } from "@/components/charts/bar-chart";
import { SeriesChart } from "@/components/charts/series-chart";
import { Card, CardHeader, MetricCard } from "@/components/ui/card";
import { EmptyState, ErrorState, MetricsSkeleton, Skeleton } from "@/components/ui/feedback";
import { Button, Select } from "@/components/ui/primitives";
import { PageHeader } from "@/components/shell/page-header";
import { metricString, breakdownRows } from "@/lib/metrics";

const WINDOWS = [
  { value: 30, label: "Last 30 days" },
  { value: 90, label: "Last 90 days" },
  { value: 365, label: "Last 12 months" },
  { value: 3650, label: "All time" },
];

export default function DashboardPage() {
  const currency = useCurrency();
  const [days, setDays] = useState(90);

  const dashboard = useQuery({
    queryKey: queryKeys.dashboard({ days }),
    queryFn: () => api.get<DashboardResult>("/api/v1/analytics/dashboard", { days }),
  });

  if (dashboard.isError) {
    return (
      <>
        <PageHeader title="Dashboard" />
        <ErrorState error={dashboard.error} onRetry={() => void dashboard.refetch()} />
      </>
    );
  }

  const result = dashboard.data;
  const metrics = result?.metrics ?? {};
  const tradeCount = Number(metrics.total_trades ?? 0);

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Everything here is computed from your recorded trades."
        action={
          <Select
            aria-label="Time window"
            value={days}
            onChange={(event) => setDays(Number(event.target.value))}
            className="w-44"
          >
            {WINDOWS.map((window) => (
              <option key={window.value} value={window.value}>
                {window.label}
              </option>
            ))}
          </Select>
        }
      />

      {dashboard.isLoading ? (
        <div className="space-y-4">
          <MetricsSkeleton />
          <Skeleton className="h-72 rounded" />
        </div>
      ) : tradeCount === 0 ? (
        <EmptyState
          icon={<BookOpen className="h-7 w-7" />}
          title="No closed trades in this period"
          description="Record a trade or import your broker's execution history, and every figure on this page will be calculated from it."
          action={
            <div className="flex gap-2">
              <Link href="/journal">
                <Button variant="primary">Record a trade</Button>
              </Link>
              <Link href="/imports">
                <Button variant="outline">Import a CSV</Button>
              </Link>
            </div>
          }
        />
      ) : (
        <div className="space-y-4">
          <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <MetricCard
              label="Net P&L"
              tone="pnl"
              raw={metricString(metrics.net_profit)}
              value={formatMoney(metricString(metrics.net_profit), currency, { signed: true })}
              hint={`${formatPercent(metricString(metrics.total_return_percent), { signed: true })} return`}
            />
            <MetricCard
              label="Win rate"
              value={formatPercent(metricString(metrics.win_rate))}
              hint={`${formatInteger(Number(metrics.winning_trades ?? 0))} of ${formatInteger(tradeCount)} trades`}
            />
            <MetricCard
              label="Profit factor"
              value={formatRatio(metricString(metrics.profit_factor))}
              hint={
                metrics.profit_factor === null
                  ? "Undefined — no losing trades"
                  : "Gross profit ÷ gross loss"
              }
            />
            <MetricCard
              label="Expectancy"
              tone="pnl"
              raw={metricString(metrics.expectancy)}
              value={formatMoney(metricString(metrics.expectancy), currency, { signed: true })}
              hint="Average per trade"
            />
          </section>

          <section className="grid gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader
                title="Equity curve"
                description="Cumulative realised P&L, sampled at each closed trade."
              />
              <SeriesChart
                points={(result?.equity_curve ?? []).map((point) => ({
                  timestamp: point.timestamp,
                  value: point.equity,
                }))}
                baseline={result?.equity_curve[0]?.equity}
                height={280}
              />
            </Card>

            <Card>
              <CardHeader title="Risk" />
              <dl className="space-y-3">
                <Stat
                  label="Max drawdown"
                  value={formatPercent(metricString(metrics.max_drawdown_percent))}
                  sub={formatMoney(metricString(metrics.max_drawdown), currency)}
                />
                <Stat label="Average R" value={formatR(metricString(metrics.average_r))} sub={`${formatInteger(Number(metrics.trades_with_r ?? 0))} trades with a defined R`} />
                <Stat label="Payoff ratio" value={formatRatio(metricString(metrics.payoff_ratio))} sub="Avg win ÷ avg loss" />
                <Stat
                  label="Longest losing streak"
                  value={formatInteger(Number(metrics.max_consecutive_losses ?? 0))}
                  sub={`Best streak ${formatInteger(Number(metrics.max_consecutive_wins ?? 0))}`}
                />
                <Stat
                  label="Average hold"
                  value={formatDuration(
                    metrics.average_holding_seconds === null ? null : Number(metrics.average_holding_seconds),
                  )}
                />
              </dl>
            </Card>
          </section>

          <section className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Drawdown" description="Distance below the running equity peak." />
              <SeriesChart
                tone="loss"
                height={200}
                points={(result?.drawdown_curve ?? []).map((point) => ({
                  timestamp: point.timestamp,
                  value: point.drawdown,
                }))}
              />
            </Card>

            <Card>
              <CardHeader title="Daily P&L" description="Realised result per trading day." />
              <PnlCalendar days={(result?.breakdowns.calendar as CalendarDay[]) ?? []} currency={currency} />
            </Card>
          </section>

          <section className="grid gap-4 lg:grid-cols-3">
            <Card>
              <CardHeader title="By symbol" />
              <BreakdownBars
                currency={currency}
                data={breakdownRows(result?.breakdowns.by_symbol).map(toBar)}
              />
            </Card>
            <Card>
              <CardHeader title="By setup" />
              <BreakdownBars
                currency={currency}
                data={breakdownRows(result?.breakdowns.by_setup).map(toBar)}
                emptyMessage="Tag trades with a setup to see this."
              />
            </Card>
            <Card>
              <CardHeader title="By weekday" />
              <BreakdownBars
                currency={currency}
                data={breakdownRows(result?.breakdowns.by_weekday).map((row) => ({
                  ...toBar(row),
                  label: WEEKDAYS[Number(row.label)] ?? row.label,
                }))}
              />
            </Card>
          </section>

          <section className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader
                title="Open positions"
                description={`${result?.open_positions.count ?? 0} currently open`}
                action={
                  <Link href="/journal?status=open" className="text-xs text-accent hover:underline">
                    View all
                  </Link>
                }
              />
              {(result?.open_positions.trades.length ?? 0) === 0 ? (
                <p className="py-6 text-center text-xs text-faint">Nothing open right now.</p>
              ) : (
                <ul className="divide-y divide-line">
                  {result?.open_positions.trades.map((trade) => (
                    <li key={trade.id} className="flex items-center justify-between py-2">
                      <Link href={`/journal/${trade.id}`} className="text-sm font-medium hover:underline">
                        {trade.symbol}
                      </Link>
                      <span className="text-xs text-muted">
                        {humanise(trade.direction)} · {trade.quantity} @ {trade.entry_price}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            <Card>
              <CardHeader
                title="Recent trades"
                action={
                  <Link href="/journal" className="text-xs text-accent hover:underline">
                    Open journal
                  </Link>
                }
              />
              <ul className="divide-y divide-line">
                {result?.recent_trades.map((trade) => (
                  <li key={trade.id} className="flex items-center justify-between py-2">
                    <Link href={`/journal/${trade.id}`} className="text-sm font-medium hover:underline">
                      {trade.symbol}
                    </Link>
                    <span className="flex items-center gap-3">
                      <span className="text-xs text-faint">{formatR(trade.r_multiple)}</span>
                      <span className={cn("tnum text-sm font-medium", pnlClass(trade.net_pnl))}>
                        {formatMoney(trade.net_pnl, currency, { signed: true })}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          </section>

          {result?.truncated ? (
            <p className="rounded border border-warn/30 bg-warn/5 p-3 text-xs text-muted">
              This period contains more trades than a single analytics query returns. Narrow the
              window for exact figures.
            </p>
          ) : null}
        </div>
      )}
    </>
  );
}

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function toBar(row: BreakdownRow) {
  return {
    label: row.label,
    value: row.net_pnl,
    meta: `${row.trades} trade${row.trades === 1 ? "" : "s"} · ${formatPercent(row.win_rate)} win`,
  };
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="text-right">
        <span className="tnum text-sm font-semibold text-ink">{value}</span>
        {sub ? <span className="block text-2xs text-faint">{sub}</span> : null}
      </dd>
    </div>
  );
}

/** Calendar heatmap of daily P&L. Days with no trading are blank, not zero. */
function PnlCalendar({ days, currency }: { days: CalendarDay[]; currency: string }) {
  if (days.length === 0) {
    return <p className="py-6 text-center text-xs text-faint">No closed trades in this period.</p>;
  }

  const recent = days.slice(-70);
  const largest = Math.max(...recent.map((day) => Math.abs(Number(day.net_pnl ?? 0))), 1);

  return (
    <div>
      <div className="grid grid-cols-10 gap-1">
        {recent.map((day) => {
          const value = Number(day.net_pnl ?? 0);
          const intensity = Math.min(Math.abs(value) / largest, 1);
          return (
            <div
              key={day.date}
              title={`${day.date}: ${formatMoney(day.net_pnl, currency, { signed: true })} · ${day.trades} trades`}
              className={cn(
                "aspect-square rounded-sm border border-line/50",
                value > 0 ? "bg-profit" : value < 0 ? "bg-loss" : "bg-raised",
              )}
              style={value === 0 ? undefined : { opacity: 0.25 + intensity * 0.75 }}
            />
          );
        })}
      </div>
      <p className="mt-2 flex items-center gap-2 text-2xs text-faint">
        <TrendingUp className="h-3 w-3" aria-hidden />
        Last {recent.length} trading days
        <ArrowRight className="ml-auto h-3 w-3" aria-hidden />
      </p>
    </div>
  );
}
