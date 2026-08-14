"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { BarChart3, ExternalLink } from "lucide-react";
import Link from "next/link";

import { api } from "@/lib/api";
import {
  formatDate,
  formatInteger,
  formatPercent,
  formatPrice,
  humanise,
  type DecimalString,
} from "@/lib/format";
import { queryKeys } from "@/lib/queries";
import type {
  CandleResponse,
  Instrument,
  ReportCondition,
  ReportRun,
  ReportSession,
  ReportSpec,
  Timeframe,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { CandleChart, type PriceLevel } from "@/components/charts/candle-chart";
import { Card, CardHeader } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import { Badge, Button, Field, Input, Select } from "@/components/ui/primitives";
import { PageHeader } from "@/components/shell/page-header";

/** Outcome vocabulary is shared across reports, so its presentation is defined once. */
const OUTCOMES: Record<string, { label: string; tone: "profit" | "loss" | "warn" | "info" | "neutral" }> = {
  broke_up_only: { label: "Broke up only", tone: "profit" },
  broke_down_only: { label: "Broke down only", tone: "loss" },
  broke_both: { label: "Broke both sides", tone: "warn" },
  stayed_inside: { label: "Stayed inside", tone: "neutral" },
  filled: { label: "Filled", tone: "profit" },
  unfilled: { label: "Not filled", tone: "loss" },
  no_setup: { label: "No setup", tone: "neutral" },
};

const TIMEFRAMES: Timeframe[] = ["15m", "1h", "4h", "1d"];

const ZONES = [
  "America/New_York",
  "America/Chicago",
  "Europe/London",
  "Asia/Tokyo",
  "UTC",
];

export default function ReportsPage() {
  const [reportKey, setReportKey] = useState("initial_balance");
  const [instrumentId, setInstrumentId] = useState("");
  const [timeframe, setTimeframe] = useState<Timeframe>("1h");
  const [zone, setZone] = useState("America/New_York");
  const [minutes, setMinutes] = useState("60");
  const [selected, setSelected] = useState<string | null>(null);

  const specs = useQuery({
    queryKey: queryKeys.reports,
    queryFn: () => api.get<ReportSpec[]>("/api/v1/reports"),
  });

  const instruments = useQuery({
    queryKey: queryKeys.instruments(),
    queryFn: () => api.list<Instrument>("/api/v1/instruments", { page_size: 100 }),
  });

  const resolvedInstrument = instrumentId || instruments.data?.data[0]?.id || "";
  const spec = specs.data?.find((item) => item.key === reportKey);
  const usesMinutes = spec?.parameters.some((p) => p.name === "minutes") ?? false;

  const params = useMemo(
    () => ({
      instrument_id: resolvedInstrument,
      timeframe,
      session_timezone: zone,
      ...(usesMinutes ? { minutes: Number(minutes) || 60 } : {}),
    }),
    [resolvedInstrument, timeframe, zone, usesMinutes, minutes],
  );

  const report = useQuery({
    queryKey: queryKeys.report(reportKey, params),
    queryFn: () => api.get<ReportRun>(`/api/v1/reports/${reportKey}`, params),
    enabled: Boolean(resolvedInstrument),
  });

  const run = report.data;
  const session = run?.sessions.find((s) => s.session_date === selected) ?? null;

  return (
    <>
      <PageHeader
        title="Reports"
        description="A statistic you can check. Every percentage lists the sessions behind it — open any one and see the day for yourself."
      />

      <Card className="mb-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <Field label="Report" htmlFor="r-key" className="lg:col-span-2">
            <Select
              id="r-key"
              value={reportKey}
              onChange={(event) => {
                setReportKey(event.target.value);
                setSelected(null);
              }}
            >
              {(specs.data ?? []).map((item) => (
                <option key={item.key} value={item.key}>
                  {item.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Instrument" htmlFor="r-instrument">
            <Select
              id="r-instrument"
              value={resolvedInstrument}
              onChange={(event) => {
                setInstrumentId(event.target.value);
                setSelected(null);
              }}
            >
              {(instruments.data?.data ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.symbol}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Candles" htmlFor="r-timeframe">
            <Select
              id="r-timeframe"
              value={timeframe}
              onChange={(event) => setTimeframe(event.target.value as Timeframe)}
            >
              {TIMEFRAMES.map((tf) => (
                <option key={tf} value={tf}>
                  {tf}
                </option>
              ))}
            </Select>
          </Field>
          <Field
            label="Session day in"
            htmlFor="r-zone"
            hint="Decides where a trading day starts."
          >
            <Select id="r-zone" value={zone} onChange={(event) => setZone(event.target.value)}>
              {ZONES.map((z) => (
                <option key={z} value={z}>
                  {z}
                </option>
              ))}
            </Select>
          </Field>
          {usesMinutes ? (
            <Field label="Range length (min)" htmlFor="r-minutes">
              <Input
                id="r-minutes"
                inputMode="numeric"
                value={minutes}
                onChange={(event) => setMinutes(event.target.value)}
              />
            </Field>
          ) : null}
        </div>
        {spec ? <p className="mt-3 text-xs text-muted">{spec.description}</p> : null}
      </Card>

      {report.isError ? (
        <ErrorState error={report.error} onRetry={() => void report.refetch()} />
      ) : report.isLoading || !run ? (
        <Skeleton className="h-72 rounded" />
      ) : run.sample_size === 0 ? (
        <EmptyState
          icon={<BarChart3 className="h-7 w-7" />}
          title="This setup never occurred in the data"
          description={`${run.total_sessions} sessions were examined and none of them qualified. That is a real answer, not an empty one — there is no rate to quote.`}
        />
      ) : (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
          <div className="space-y-4">
            <Card>
              <CardHeader title={run.question} />
              <p className="tnum text-4xl font-semibold tracking-tight text-ink">
                {formatPercent(run.hit_rate)}
              </p>
              <p className="mt-1 text-xs text-muted">
                {run.headline_outcomes.map((o) => OUTCOMES[o]?.label ?? humanise(o)).join(" or ")} ·{" "}
                {formatInteger(run.sample_size)} qualifying session
                {run.sample_size === 1 ? "" : "s"} of {formatInteger(run.total_sessions)}
              </p>

              {!run.sufficient_sample ? (
                <p className="mt-3 rounded border border-warn/30 bg-warn/5 p-2.5 text-xs text-muted">
                  Only {run.sample_size} sessions qualified. Below {run.minimum_sample} this is a
                  description of what happened, not evidence of what tends to happen.
                </p>
              ) : null}

              <div className="mt-4 space-y-1.5 border-t border-line pt-3">
                {Object.entries(run.buckets)
                  .sort((a, b) => b[1] - a[1])
                  .map(([outcome, count]) => {
                    const meta = OUTCOMES[outcome] ?? { label: humanise(outcome), tone: "neutral" };
                    const share = (count / Math.max(run.total_sessions, 1)) * 100;
                    return (
                      <div key={outcome} className="flex items-center gap-3 text-xs">
                        <span className="w-32 shrink-0 text-muted">{meta.label}</span>
                        <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-raised">
                          <span
                            className={cn(
                              "block h-full rounded-full",
                              meta.tone === "profit" && "bg-profit",
                              meta.tone === "loss" && "bg-loss",
                              meta.tone === "warn" && "bg-warn",
                              meta.tone === "info" && "bg-info",
                              meta.tone === "neutral" && "bg-faint",
                            )}
                            style={{ width: `${share}%` }}
                          />
                        </span>
                        <span className="tnum w-12 shrink-0 text-right font-medium">{count}</span>
                      </div>
                    );
                  })}
              </div>

              <p className="mt-3 text-2xs text-faint">
                {run.instrument.symbol} · {run.timeframe} candles · sessions bounded by{" "}
                {run.session_timezone} · {run.source.name}
              </p>
            </Card>

            <Card padded={false}>
              <div className="border-b border-line p-4">
                <h2 className="text-sm font-semibold text-ink">Every session counted</h2>
                <p className="mt-0.5 text-xs text-muted">
                  Select a day to plot it with the levels this report used.
                </p>
              </div>
              <div className="max-h-[26rem] overflow-y-auto">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-surface">
                    <tr className="border-b border-line">
                      <th className="px-4 py-2 text-left font-medium text-faint">Session</th>
                      <th className="px-3 py-2 text-left font-medium text-faint">Outcome</th>
                      <th className="px-3 py-2 text-right font-medium text-faint">Levels</th>
                    </tr>
                  </thead>
                  <tbody>
                    {run.sessions.map((row) => {
                      const meta = OUTCOMES[row.outcome] ?? {
                        label: humanise(row.outcome),
                        tone: "neutral" as const,
                      };
                      return (
                        <tr
                          key={row.session_date}
                          onClick={() => setSelected(row.session_date)}
                          aria-selected={selected === row.session_date}
                          className={cn(
                            "cursor-pointer border-b border-line/60 transition-colors hover:bg-raised",
                            selected === row.session_date && "bg-accent/8",
                          )}
                        >
                          <td className="tnum px-4 py-2">{formatDate(`${row.session_date}T00:00:00Z`)}</td>
                          <td className="px-3 py-2">
                            <Badge tone={meta.tone}>{meta.label}</Badge>
                          </td>
                          <td className="tnum px-3 py-2 text-right text-muted">
                            {row.levels.map((level) => formatPrice(level.price)).join(" / ") || "—"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Card>

            <ConditionalBreakdown
              conditions={run.conditions}
              overall={run.hit_rate}
              minimumSample={run.minimum_sample}
              onPick={(dates) => setSelected(dates[0] ?? null)}
            />
          </div>

          <SessionInspector
            session={session ?? run.sessions.find((s) => s.outcome !== "no_setup") ?? null}
            instrumentId={run.instrument.id}
            symbol={run.instrument.symbol}
            timeframe={run.timeframe}
          />
        </div>
      )}
    </>
  );
}

/**
 * The verification half.
 *
 * Fetches the actual candles for the chosen session's window and draws the report's levels on
 * them. This is the whole point of the feature: the statistic and the evidence sit side by side,
 * and a number you doubt is one click from the day it came from.
 */
function SessionInspector({
  session,
  instrumentId,
  symbol,
  timeframe,
}: {
  session: ReportSession | null;
  instrumentId: string;
  symbol: string;
  timeframe: Timeframe;
}) {
  const candles = useQuery({
    queryKey: queryKeys.candles({
      instrument_id: instrumentId,
      timeframe,
      start: session?.window_start,
      end: session?.window_end,
    }),
    // The endpoint returns { instrument, source, candles } — not a bare array.
    queryFn: () =>
      api.get<CandleResponse>("/api/v1/market-data/candles", {
        instrument_id: instrumentId,
        timeframe,
        start: session!.window_start,
        end: session!.window_end,
      }),
    enabled: Boolean(session),
  });

  if (!session) {
    return (
      <Card>
        <CardHeader title="Verify a session" />
        <p className="py-10 text-center text-xs text-faint">
          Select a session to plot it.
        </p>
      </Card>
    );
  }

  const levels: PriceLevel[] = session.levels.map((level) => ({
    price: level.price,
    label: level.label,
    tone: level.key.includes("high") || level.key.includes("open") ? "target" : "stop",
  }));

  return (
    <Card className="xl:sticky xl:top-4">
      <CardHeader
        title={formatDate(`${session.session_date}T00:00:00Z`)}
        description={`${symbol} · ${timeframe} candles`}
        action={
          <Link
            href={`/replay?instrument=${instrumentId}&start=${session.window_start}&timeframe=${timeframe}`}
          >
            <Button variant="outline" icon={<ExternalLink className="h-3.5 w-3.5" />}>
              Trade it in replay
            </Button>
          </Link>
        }
      />

      {candles.isError ? (
        <ErrorState error={candles.error} onRetry={() => void candles.refetch()} />
      ) : candles.isLoading ? (
        <Skeleton className="h-72 rounded" />
      ) : (
        <CandleChart candles={candles.data?.candles ?? []} levels={levels} height={300} />
      )}

      <dl className="mt-4 space-y-2 border-t border-line pt-3">
        {session.levels.map((level) => (
          <div key={level.key} className="flex items-baseline justify-between gap-3">
            <dt className="text-xs text-muted">{level.label}</dt>
            <dd className="tnum text-sm font-medium text-ink">{formatPrice(level.price)}</dd>
          </div>
        ))}
        {Object.entries(session.measures)
          .filter(([, value]) => value !== null && value !== undefined)
          .map(([key, value]) => (
            <div key={key} className="flex items-baseline justify-between gap-3">
              <dt className="text-xs text-muted">{humanise(key)}</dt>
              <dd className="tnum text-sm text-muted">{formatPrice(value)}</dd>
            </div>
          ))}
      </dl>

      <p className="mt-3 text-2xs text-faint">
        These are the exact levels the report measured against, and these are the candles it read.
        Nothing here is recomputed in the browser.
      </p>
    </Card>
  );
}

/**
 * The same rate, split by what was already knowable when each session opened.
 *
 * A flat headline describes the average day. These describe days you can recognise in advance —
 * which is the difference between a statistic that is interesting and one you can act on. Every
 * slice carries its own sample size, and thin slices are dimmed rather than hidden, because a
 * two-session slice showing 100% is the most misleading thing this page could render.
 */
function ConditionalBreakdown({
  conditions,
  overall,
  minimumSample,
  onPick,
}: {
  conditions: ReportCondition[];
  overall: DecimalString;
  minimumSample: number;
  onPick: (dates: string[]) => void;
}) {
  if (!conditions.length) return null;

  return (
    <Card>
      <CardHeader
        title="Does the context change it?"
        description={`Overall the rate is ${formatPercent(overall)}. Split by what was true before each session opened, it looks like this.`}
      />

      <div className="grid gap-5 sm:grid-cols-2">
        {conditions.map((condition) => (
          <div key={condition.key}>
            <p className="mb-2 text-2xs font-semibold uppercase tracking-wide text-faint">
              {condition.name}
            </p>
            <div className="space-y-1.5">
              {condition.values.map((value) => {
                const thin = value.sample_size < minimumSample;
                const rate = Number(value.hit_rate ?? 0);
                return (
                  <button
                    key={value.value}
                    type="button"
                    onClick={() => onPick(value.session_dates)}
                    disabled={value.session_dates.length === 0}
                    title={
                      thin
                        ? `Only ${value.sample_size} sessions — too few to lean on`
                        : `${value.sample_size} sessions`
                    }
                    className={cn(
                      "flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-xs transition-colors",
                      "hover:bg-raised disabled:cursor-not-allowed disabled:opacity-50",
                      thin && "opacity-60",
                    )}
                  >
                    <span className="w-28 shrink-0 truncate text-muted">{value.label}</span>
                    <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-raised">
                      <span
                        className={cn("block h-full rounded-full", thin ? "bg-faint" : "bg-accent")}
                        style={{ width: `${Math.min(Math.max(rate, 0), 100)}%` }}
                      />
                    </span>
                    <span className="tnum w-14 shrink-0 text-right font-medium">
                      {formatPercent(value.hit_rate)}
                    </span>
                    <span className="tnum w-8 shrink-0 text-right text-2xs text-faint">
                      {value.sample_size}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      <p className="mt-4 text-2xs text-faint">
        The trailing number is how many sessions each slice contains. Slices below {minimumSample}{" "}
        are dimmed — a high rate over three sessions is a coincidence with a percentage sign on it.
        Selecting a slice opens its first session below.
      </p>
    </Card>
  );
}
