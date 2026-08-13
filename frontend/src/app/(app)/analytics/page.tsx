"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { BarChart3 } from "lucide-react";

import { api, type QueryParams } from "@/lib/api";
import {
  formatDuration,
  formatInteger,
  formatMoney,
  formatPercent,
  formatR,
  formatRatio,
} from "@/lib/format";
import { breakdownRows, metricString, monthlyReturns, rBuckets } from "@/lib/metrics";
import { queryKeys } from "@/lib/queries";
import { useCurrency } from "@/lib/session";
import type { Account, AnalyticsResult, BreakdownRow, Setup, Strategy } from "@/lib/types";
import { BreakdownBars, Histogram } from "@/components/charts/bar-chart";
import { SeriesChart } from "@/components/charts/series-chart";
import { Card, CardHeader, MetricCard } from "@/components/ui/card";
import { EmptyState, ErrorState, MetricsSkeleton, Skeleton } from "@/components/ui/feedback";
import { Field, Select } from "@/components/ui/primitives";
import { Tabs } from "@/components/ui/tabs";
import { PageHeader } from "@/components/shell/page-header";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "distribution", label: "Distribution" },
  { id: "breakdowns", label: "Breakdowns" },
  { id: "timing", label: "Timing" },
];

export default function AnalyticsPage() {
  const currency = useCurrency();
  const [tab, setTab] = useState("overview");
  const [filters, setFilters] = useState({ account_id: "", strategy_id: "", setup_id: "", date_from: "", date_to: "" });

  const accounts = useQuery({
    queryKey: queryKeys.accounts(),
    queryFn: () => api.list<Account>("/api/v1/accounts", { page_size: 100 }),
  });
  const strategies = useQuery({
    queryKey: queryKeys.strategies(),
    queryFn: () => api.list<Strategy>("/api/v1/strategies", { page_size: 100 }),
  });
  const setups = useQuery({ queryKey: queryKeys.setups, queryFn: () => api.get<Setup[]>("/api/v1/setups") });

  const params = useMemo<QueryParams>(() => {
    const query: QueryParams = {};
    for (const [key, value] of Object.entries(filters)) if (value) query[key] = value;
    return query;
  }, [filters]);

  const analytics = useQuery({
    queryKey: queryKeys.analytics(params),
    queryFn: () => api.get<AnalyticsResult>("/api/v1/analytics/overview", params),
  });

  const metrics = analytics.data?.metrics ?? {};
  const tradeCount = Number(metrics.total_trades ?? 0);
  const breakdowns = analytics.data?.breakdowns ?? {};

  const toBar = (row: BreakdownRow) => ({
    label: row.label,
    value: row.net_pnl,
    meta: `${row.trades} trades · ${formatPercent(row.win_rate)} win`,
  });

  return (
    <>
      <PageHeader
        title="Analytics"
        description="One filterable engine — every chart answers the same question set."
      />

      <div className="mb-4 grid gap-3 rounded border border-line bg-surface p-4 sm:grid-cols-2 lg:grid-cols-5">
        <Field label="Account" htmlFor="a-account">
          <Select
            id="a-account"
            value={filters.account_id}
            onChange={(event) => setFilters((current) => ({ ...current, account_id: event.target.value }))}
          >
            <option value="">All accounts</option>
            {(accounts.data?.data ?? []).map((account) => (
              <option key={account.id} value={account.id}>{account.name}</option>
            ))}
          </Select>
        </Field>
        <Field label="Strategy" htmlFor="a-strategy">
          <Select
            id="a-strategy"
            value={filters.strategy_id}
            onChange={(event) => setFilters((current) => ({ ...current, strategy_id: event.target.value }))}
          >
            <option value="">All strategies</option>
            {(strategies.data?.data ?? []).map((strategy) => (
              <option key={strategy.id} value={strategy.id}>{strategy.name}</option>
            ))}
          </Select>
        </Field>
        <Field label="Setup" htmlFor="a-setup">
          <Select
            id="a-setup"
            value={filters.setup_id}
            onChange={(event) => setFilters((current) => ({ ...current, setup_id: event.target.value }))}
          >
            <option value="">All setups</option>
            {(setups.data ?? []).map((setup) => (
              <option key={setup.id} value={setup.id}>{setup.name}</option>
            ))}
          </Select>
        </Field>
        <Field label="From" htmlFor="a-from">
          <input
            id="a-from"
            type="date"
            className="h-9 w-full rounded border border-line bg-surface px-3 text-sm"
            value={filters.date_from}
            onChange={(event) => setFilters((current) => ({ ...current, date_from: event.target.value }))}
          />
        </Field>
        <Field label="To" htmlFor="a-to">
          <input
            id="a-to"
            type="date"
            className="h-9 w-full rounded border border-line bg-surface px-3 text-sm"
            value={filters.date_to}
            onChange={(event) => setFilters((current) => ({ ...current, date_to: event.target.value }))}
          />
        </Field>
      </div>

      {analytics.isError ? (
        <ErrorState error={analytics.error} onRetry={() => void analytics.refetch()} />
      ) : analytics.isLoading ? (
        <div className="space-y-4">
          <MetricsSkeleton />
          <Skeleton className="h-72 rounded" />
        </div>
      ) : tradeCount === 0 ? (
        <EmptyState
          icon={<BarChart3 className="h-7 w-7" />}
          title="Nothing to analyse yet"
          description="Analytics are computed from closed trades. Record or import some, then come back."
        />
      ) : (
        <>
          <Tabs items={TABS} active={tab} onChange={setTab} className="mb-4" />

          {tab === "overview" ? (
            <div className="space-y-4">
              <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <MetricCard label="Net profit" tone="pnl" raw={metricString(metrics.net_profit)} value={formatMoney(metricString(metrics.net_profit), currency, { signed: true })} />
                <MetricCard label="Trades" value={formatInteger(tradeCount)} hint={`${formatInteger(Number(metrics.winning_trades ?? 0))}W / ${formatInteger(Number(metrics.losing_trades ?? 0))}L`} />
                <MetricCard label="Expectancy" tone="pnl" raw={metricString(metrics.expectancy)} value={formatMoney(metricString(metrics.expectancy), currency, { signed: true })} hint="Per trade" />
                <MetricCard label="Profit factor" value={formatRatio(metricString(metrics.profit_factor))} hint={metrics.profit_factor === null ? "Undefined — no losses" : undefined} />
                <MetricCard label="Average winner" tone="pnl" raw={metricString(metrics.average_winner)} value={formatMoney(metricString(metrics.average_winner), currency)} />
                <MetricCard label="Average loser" tone="pnl" raw={metricString(metrics.average_loser)} value={formatMoney(metricString(metrics.average_loser), currency)} />
                <MetricCard label="Largest win" tone="pnl" raw={metricString(metrics.largest_winner)} value={formatMoney(metricString(metrics.largest_winner), currency)} />
                <MetricCard label="Largest loss" tone="pnl" raw={metricString(metrics.largest_loser)} value={formatMoney(metricString(metrics.largest_loser), currency)} />
              </section>

              <Card>
                <CardHeader title="Equity" description="Cumulative realised P&L across the filtered set." />
                <SeriesChart
                  height={300}
                  points={(analytics.data?.equity_curve ?? []).map((point) => ({ timestamp: point.timestamp, value: point.equity }))}
                  baseline={analytics.data?.equity_curve[0]?.equity}
                />
              </Card>

              <div className="grid gap-4 lg:grid-cols-2">
                <Card>
                  <CardHeader title="Drawdown" />
                  <SeriesChart
                    tone="loss"
                    height={220}
                    points={(analytics.data?.drawdown_curve ?? []).map((point) => ({ timestamp: point.timestamp, value: point.drawdown }))}
                  />
                </Card>
                <Card>
                  <CardHeader title="Risk-adjusted" description="Definitions in docs/FINANCIALS.md" />
                  <dl className="space-y-3">
                    <StatRow label="Sharpe ratio" value={formatRatio(metricString(metrics.sharpe_ratio))} />
                    <StatRow label="Sortino ratio" value={formatRatio(metricString(metrics.sortino_ratio))} />
                    <StatRow label="Calmar ratio" value={formatRatio(metricString(metrics.calmar_ratio))} />
                    <StatRow label="CAGR" value={formatPercent(metricString(metrics.cagr_percent))} />
                    <StatRow label="Max drawdown" value={formatPercent(metricString(metrics.max_drawdown_percent))} />
                    <StatRow label="Average R" value={formatR(metricString(metrics.average_r))} />
                    <StatRow label="Median R" value={formatR(metricString(metrics.median_r))} />
                  </dl>
                </Card>
              </div>
            </div>
          ) : null}

          {tab === "distribution" ? (
            <div className="space-y-4">
              <Card>
                <CardHeader title="R-multiple distribution" description="Trades without a defined stop are excluded." />
                <Histogram buckets={rBuckets(breakdowns.r_distribution)} />
              </Card>
              <Card>
                <CardHeader title="Monthly returns" />
                <BreakdownBars
                  currency={currency}
                  maxRows={24}
                  data={monthlyReturns(breakdowns.monthly_returns).map((row) => ({
                    label: row.period,
                    value: row.net_change,
                    meta: formatPercent(row.return_percent, { signed: true }),
                  }))}
                />
              </Card>
            </div>
          ) : null}

          {tab === "breakdowns" ? (
            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader title="By symbol" />
                <BreakdownBars currency={currency} data={breakdownRows(breakdowns.by_symbol).map(toBar)} />
              </Card>
              <Card>
                <CardHeader title="By strategy" />
                <BreakdownBars currency={currency} data={breakdownRows(breakdowns.by_strategy).map(toBar)} />
              </Card>
              <Card>
                <CardHeader title="By setup" />
                <BreakdownBars currency={currency} data={breakdownRows(breakdowns.by_setup).map(toBar)} />
              </Card>
              <Card>
                <CardHeader title="By tag" description="A trade with several tags counts toward each." />
                <BreakdownBars currency={currency} data={breakdownRows(breakdowns.by_tag).map(toBar)} />
              </Card>
              <Card>
                <CardHeader title="By account" />
                <BreakdownBars currency={currency} data={breakdownRows(breakdowns.by_account).map(toBar)} />
              </Card>
              <Card>
                <CardHeader title="Long vs short" />
                <BreakdownBars currency={currency} data={breakdownRows(breakdowns.by_direction).map(toBar)} />
              </Card>
            </div>
          ) : null}

          {tab === "timing" ? (
            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader title="By weekday" description="Evaluated in the account's timezone." />
                <BreakdownBars
                  currency={currency}
                  data={breakdownRows(breakdowns.by_weekday).map((row) => ({ ...toBar(row), label: WEEKDAYS[Number(row.label)] ?? row.label }))}
                />
              </Card>
              <Card>
                <CardHeader title="By hour" />
                <BreakdownBars
                  currency={currency}
                  maxRows={24}
                  data={breakdownRows(breakdowns.by_hour).map((row) => ({ ...toBar(row), label: `${row.label.padStart(2, "0")}:00` }))}
                />
              </Card>
              <Card>
                <CardHeader title="By session" />
                <BreakdownBars currency={currency} data={breakdownRows(breakdowns.by_session).map(toBar)} />
              </Card>
              <Card>
                <CardHeader title="Holding time" />
                <dl className="space-y-3">
                  <StatRow label="Average hold" value={formatDuration(metrics.average_holding_seconds === null ? null : Number(metrics.average_holding_seconds))} />
                  <StatRow label="Median hold" value={formatDuration(metrics.median_holding_seconds === null ? null : Number(metrics.median_holding_seconds))} />
                  <StatRow label="Exposure" value={formatPercent(metricString(metrics.exposure_percent))} />
                </dl>
              </Card>
            </div>
          ) : null}
        </>
      )}
    </>
  );
}

function StatRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="tnum text-sm font-medium text-ink">{value}</dd>
    </div>
  );
}
