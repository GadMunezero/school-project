"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Play } from "lucide-react";

import { api } from "@/lib/api";
import {
  formatDateTime,
  formatDuration,
  formatInteger,
  formatMoney,
  formatPercent,
  formatR,
  formatRatio,
  humanise,
  pnlClass,
} from "@/lib/format";
import { breakdownRows, metricString, rBuckets } from "@/lib/metrics";
import { queryKeys } from "@/lib/queries";
import type { Backtest, BacktestResult, BacktestRun, JobRecord, RunSubmission } from "@/lib/types";
import { cn } from "@/lib/utils";
import { BreakdownBars, Histogram } from "@/components/charts/bar-chart";
import { SeriesChart } from "@/components/charts/series-chart";
import { Card, CardHeader, MetricCard } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import { Badge, Button } from "@/components/ui/primitives";
import { Tabs } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/shell/page-header";

const TABS = [
  { id: "summary", label: "Summary" },
  { id: "trades", label: "Trades" },
  { id: "breakdowns", label: "Breakdowns" },
  { id: "reproducibility", label: "Reproducibility" },
];

export default function BacktestDetailPage() {
  const params = useParams<{ backtestId: string }>();
  const backtestId = params.backtestId;
  const queryClient = useQueryClient();
  const toast = useToast();

  const [tab, setTab] = useState("summary");
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);

  const backtest = useQuery({
    queryKey: queryKeys.backtest(backtestId),
    queryFn: () => api.get<Backtest>(`/api/v1/backtests/${backtestId}`),
  });

  const runs = useQuery({
    queryKey: queryKeys.backtestRuns(backtestId),
    queryFn: () => api.list<BacktestRun>(`/api/v1/backtests/${backtestId}/runs`, { page_size: 20 }),
  });

  const latestRunId = activeRunId ?? runs.data?.data[0]?.id ?? null;

  /**
   * Poll the job while it is queued or running. Polling stops the moment the job reaches a
   * terminal state, so a finished page makes no further requests.
   */
  const job = useQuery({
    queryKey: queryKeys.job(activeJobId ?? ""),
    queryFn: () => api.get<JobRecord>(`/api/v1/backtests/jobs/${activeJobId}`),
    enabled: Boolean(activeJobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 1500 : false;
    },
  });

  // Reacting to the job reaching a terminal state belongs in an effect: mutating state during
  // render would re-enter the render phase and fire the toast on every pass.
  const announcedJobRef = useRef<string | null>(null);
  const jobStatus = job.data?.status;
  useEffect(() => {
    if (!activeJobId || (jobStatus !== "completed" && jobStatus !== "failed")) return;
    if (announcedJobRef.current === activeJobId) return;

    announcedJobRef.current = activeJobId;
    setActiveJobId(null);
    void queryClient.invalidateQueries({ queryKey: queryKeys.backtestRuns(backtestId) });
    if (jobStatus === "completed") toast.success("Backtest finished.");
    else toast.error("Backtest failed", job.data?.error_message ?? undefined);
  }, [activeJobId, jobStatus, job.data?.error_message, backtestId, queryClient, toast]);

  const result = useQuery({
    queryKey: queryKeys.backtestRun(latestRunId ?? ""),
    queryFn: () => api.get<BacktestResult>(`/api/v1/backtests/runs/${latestRunId}`),
    enabled: Boolean(latestRunId),
  });

  const run = useMutation({
    mutationFn: () => api.post<RunSubmission>(`/api/v1/backtests/${backtestId}/run`),
    onSuccess: (submission) => {
      setActiveJobId(submission.job_id);
      setActiveRunId(submission.run_id);
      toast.info("Queued", "The run is executing on a worker; this page will update itself.");
      void queryClient.invalidateQueries({ queryKey: queryKeys.backtestRuns(backtestId) });
    },
    onError: (error) => toast.fromError(error, "Could not queue the backtest"),
  });

  if (backtest.isError) {
    return (
      <>
        <PageHeader title="Backtest" />
        <ErrorState error={backtest.error} onRetry={() => void backtest.refetch()} />
      </>
    );
  }

  const metrics = result.data?.metrics ?? {};
  const currency = backtest.data?.currency ?? "USD";
  const isRunning = job.data?.status === "queued" || job.data?.status === "running" || run.isPending;

  return (
    <>
      <PageHeader
        title={backtest.data?.name ?? "Backtest"}
        description={
          backtest.data
            ? `${backtest.data.timeframe} · ${backtest.data.start_date} → ${backtest.data.end_date}`
            : undefined
        }
        action={
          <>
            <Link href="/backtester">
              <Button variant="ghost" icon={<ArrowLeft className="h-3.5 w-3.5" />}>Back</Button>
            </Link>
            <Button
              variant="primary"
              icon={<Play className="h-3.5 w-3.5" />}
              loading={isRunning}
              onClick={() => run.mutate()}
            >
              {isRunning ? "Running…" : "Run backtest"}
            </Button>
          </>
        }
      />

      {job.data && (job.data.status === "queued" || job.data.status === "running") ? (
        <Card className="mb-4">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-sm font-medium">{humanise(job.data.status)}</p>
              <p className="text-xs text-muted">
                {job.data.progress_message ?? "Waiting for a worker to pick this up…"}
              </p>
            </div>
            <span className="tnum text-sm font-semibold">{job.data.progress_percent}%</span>
          </div>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-raised">
            <div
              className="h-full rounded-full bg-accent transition-all"
              style={{ width: `${Math.max(job.data.progress_percent, 3)}%` }}
            />
          </div>
        </Card>
      ) : null}

      {!latestRunId ? (
        <EmptyState
          title="This backtest has not been run yet"
          description="Running it queues a job. The simulation happens on a worker, so nothing here blocks while it executes."
          action={<Button variant="primary" onClick={() => run.mutate()} loading={run.isPending}>Run backtest</Button>}
        />
      ) : result.isLoading ? (
        <Skeleton className="h-96 rounded" />
      ) : result.isError ? (
        <ErrorState error={result.error} onRetry={() => void result.refetch()} />
      ) : result.data?.run.status === "failed" ? (
        <Card>
          <CardHeader title="This run failed" />
          <p className="text-sm text-loss">{result.data.run.error?.message ?? "No detail was recorded."}</p>
          <p className="mt-2 text-xs text-muted">
            Nothing was saved from this attempt. Adjust the configuration and run it again.
          </p>
        </Card>
      ) : (
        <>
          <Tabs items={TABS} active={tab} onChange={setTab} className="mb-4" />

          {tab === "summary" ? (
            <div className="space-y-4">
              <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <MetricCard
                  label="Net profit"
                  tone="pnl"
                  raw={metricString(metrics.net_profit)}
                  value={formatMoney(metricString(metrics.net_profit), currency, { signed: true })}
                  hint={`${formatPercent(metricString(metrics.total_return_percent), { signed: true })} return`}
                />
                <MetricCard label="Trades" value={formatInteger(Number(metrics.total_trades ?? 0))} hint={`${formatPercent(metricString(metrics.win_rate))} win rate`} />
                <MetricCard label="Profit factor" value={formatRatio(metricString(metrics.profit_factor))} hint={metrics.profit_factor === null ? "Undefined — no losses" : undefined} />
                <MetricCard label="Max drawdown" value={formatPercent(metricString(metrics.max_drawdown_percent))} hint={formatMoney(metricString(metrics.max_drawdown), currency)} />
              </section>

              <Card>
                <CardHeader title="Equity curve" description="Sampled per bar, downsampled for storage on long runs." />
                <SeriesChart
                  height={300}
                  baseline={metricString(metrics.initial_capital)}
                  points={(result.data?.equity_curve ?? []).map((point) => ({ timestamp: point.timestamp, value: point.equity }))}
                />
              </Card>

              <div className="grid gap-4 lg:grid-cols-2">
                <Card>
                  <CardHeader title="Statistics" />
                  <dl className="space-y-2.5">
                    <StatRow label="Expectancy" value={formatMoney(metricString(metrics.expectancy), currency, { signed: true })} />
                    <StatRow label="Average winner" value={formatMoney(metricString(metrics.average_winner), currency)} />
                    <StatRow label="Average loser" value={formatMoney(metricString(metrics.average_loser), currency)} />
                    <StatRow label="Payoff ratio" value={formatRatio(metricString(metrics.payoff_ratio))} />
                    <StatRow label="Sharpe" value={formatRatio(metricString(metrics.sharpe_ratio))} />
                    <StatRow label="Sortino" value={formatRatio(metricString(metrics.sortino_ratio))} />
                    <StatRow label="CAGR" value={formatPercent(metricString(metrics.cagr_percent))} />
                    <StatRow label="Exposure" value={formatPercent(metricString(metrics.exposure_percent))} />
                    <StatRow label="Max consecutive losses" value={formatInteger(Number(metrics.max_consecutive_losses ?? 0))} />
                    <StatRow label="Total costs" value={formatMoney(metricString(metrics.total_commission), currency)} />
                  </dl>
                </Card>

                <Card>
                  <CardHeader title="Drawdown episodes" description="Peak → trough → recovery." />
                  {(result.data?.drawdowns.length ?? 0) === 0 ? (
                    <p className="py-6 text-center text-xs text-faint">No drawdowns recorded.</p>
                  ) : (
                    <ul className="divide-y divide-line text-xs">
                      {result.data?.drawdowns.slice(0, 8).map((episode, index) => (
                        <li key={index} className="flex items-center justify-between py-2">
                          <span className="text-muted">{episode.started_at.slice(0, 10)}</span>
                          <span className="tnum text-loss">{formatPercent(episode.depth_percent)}</span>
                          <span className="text-faint">
                            {episode.recovered_at ? formatDuration(episode.recovery_seconds) : "not recovered"}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </Card>
              </div>

              {(result.data?.run.warnings.items?.length ?? 0) > 0 ? (
                <Card>
                  <CardHeader title="Engine notes" description="Why signals were rejected or adjusted." />
                  <ul className="list-inside list-disc space-y-1 text-xs text-muted">
                    {result.data?.run.warnings.items?.map((warning, index) => <li key={index}>{warning}</li>)}
                  </ul>
                </Card>
              ) : null}
            </div>
          ) : null}

          {tab === "trades" ? (
            <Card padded={false}>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-raised text-2xs uppercase tracking-wide text-faint">
                    <tr>
                      <th className="px-3 py-2 text-left font-semibold">#</th>
                      <th className="px-3 py-2 text-left font-semibold">Direction</th>
                      <th className="px-3 py-2 text-left font-semibold">Entry</th>
                      <th className="px-3 py-2 text-left font-semibold">Exit</th>
                      <th className="px-3 py-2 text-right font-semibold">Qty</th>
                      <th className="px-3 py-2 text-right font-semibold">Net P&L</th>
                      <th className="px-3 py-2 text-right font-semibold">R</th>
                      <th className="px-3 py-2 text-left font-semibold">Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.data?.trades.map((trade) => (
                      <tr key={trade.sequence} className="border-t border-line">
                        <td className="px-3 py-2 text-xs text-faint">{trade.sequence}</td>
                        <td className="px-3 py-2">
                          <Badge tone={trade.direction === "long" ? "info" : "warn"}>{trade.direction}</Badge>
                        </td>
                        <td className="px-3 py-2 text-xs text-muted">
                          {formatDateTime(trade.entry_timestamp)} @ {trade.entry_price}
                        </td>
                        <td className="px-3 py-2 text-xs text-muted">
                          {trade.exit_timestamp ? `${formatDateTime(trade.exit_timestamp)} @ ${trade.exit_price}` : "—"}
                        </td>
                        <td className="tnum px-3 py-2 text-right text-xs">{trade.quantity}</td>
                        <td className={cn("tnum px-3 py-2 text-right font-medium", pnlClass(trade.net_pnl))}>
                          {formatMoney(trade.net_pnl, currency, { signed: true })}
                        </td>
                        <td className={cn("tnum px-3 py-2 text-right text-xs", pnlClass(trade.r_multiple))}>
                          {formatR(trade.r_multiple)}
                        </td>
                        <td className="px-3 py-2 text-xs text-faint">{humanise(trade.exit_reason)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          ) : null}

          {tab === "breakdowns" ? (
            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader title="R distribution" />
                <Histogram buckets={rBuckets(result.data?.breakdowns.r_distribution)} />
              </Card>
              <Card>
                <CardHeader title="By exit reason" />
                <BreakdownBars
                  currency={currency}
                  data={breakdownRows(result.data?.breakdowns.by_exit_reason).map((row) => ({
                    label: humanise(row.label),
                    value: row.net_pnl,
                    meta: `${row.trades} trades`,
                  }))}
                />
              </Card>
              <Card>
                <CardHeader title="By weekday" />
                <BreakdownBars
                  currency={currency}
                  data={breakdownRows(result.data?.breakdowns.by_weekday).map((row) => ({
                    label: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][Number(row.label)] ?? row.label,
                    value: row.net_pnl,
                    meta: `${row.trades} trades`,
                  }))}
                />
              </Card>
              <Card>
                <CardHeader title="By month" />
                <BreakdownBars
                  currency={currency}
                  maxRows={24}
                  data={breakdownRows(result.data?.breakdowns.by_month).map((row) => ({
                    label: row.label,
                    value: row.net_pnl,
                    meta: `${row.trades} trades`,
                  }))}
                />
              </Card>
            </div>
          ) : null}

          {tab === "reproducibility" ? (
            <Card>
              <CardHeader
                title="Reproducibility record"
                description="Everything needed to reproduce this run byte-for-byte."
              />
              <dl className="grid gap-3 sm:grid-cols-2">
                <StatRow label="Engine version" value={result.data?.run.engine_version ?? "—"} />
                <StatRow label="Bars processed" value={formatInteger(result.data?.run.bars_processed ?? 0)} />
                <StatRow label="Duration" value={result.data?.run.duration_ms ? `${result.data.run.duration_ms} ms` : "—"} />
                <StatRow label="Finished" value={formatDateTime(result.data?.run.finished_at)} />
              </dl>
              <p className="mt-4 text-xs font-medium text-muted">Input digest</p>
              <p className="mt-1 break-all rounded bg-raised p-2 font-mono text-2xs text-ink">
                {result.data?.run.input_digest ?? "—"}
              </p>
              <p className="mt-1 text-2xs text-faint">
                A second run with the same digest consumed identical inputs and must produce identical output.
              </p>
              <p className="mt-4 text-xs font-medium text-muted">Data</p>
              <pre className="mt-1 overflow-x-auto rounded bg-raised p-3 font-mono text-2xs text-ink">
                {JSON.stringify(result.data?.run.data_snapshot, null, 2)}
              </pre>
            </Card>
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
