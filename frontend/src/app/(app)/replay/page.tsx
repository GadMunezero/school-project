"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { PlayCircle, Plus } from "lucide-react";

import { ApiError, api } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { queryKeys } from "@/lib/queries";
import type { CoverageRow, Instrument, MarketDataSource, ReplayState, ReplaySummary } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton, UpgradeNotice } from "@/components/ui/feedback";
import { Badge, Button, Field, Input, Select } from "@/components/ui/primitives";
import { Modal } from "@/components/ui/overlay";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/shell/page-header";

export default function ReplayListPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [creating, setCreating] = useState(false);

  const sessions = useQuery({
    queryKey: queryKeys.replays,
    queryFn: () => api.get<ReplaySummary[]>("/api/v1/replay"),
  });

  const instruments = useQuery({
    queryKey: queryKeys.instruments(),
    queryFn: () => api.list<Instrument>("/api/v1/instruments", { page_size: 200 }),
    enabled: creating,
  });
  const sources = useQuery({
    queryKey: queryKeys.marketDataSources,
    queryFn: () => api.get<MarketDataSource[]>("/api/v1/market-data/sources"),
    enabled: creating,
  });

  const [form, setForm] = useState({
    name: "",
    instrument_id: "",
    timeframe: "1d",
    start_at: "",
    end_at: "",
    initial_capital: "100000",
  });

  const coverage = useQuery({
    queryKey: queryKeys.coverage(form.instrument_id),
    queryFn: () => api.get<CoverageRow[]>(`/api/v1/market-data/coverage/${form.instrument_id}`),
    enabled: creating && Boolean(form.instrument_id),
  });

  const create = useMutation({
    mutationFn: () =>
      api.post<ReplayState>("/api/v1/replay", {
        name: form.name.trim(),
        instrument_id: form.instrument_id,
        market_data_source_id: sources.data?.[0]?.id,
        timeframe: form.timeframe,
        start_at: new Date(form.start_at).toISOString(),
        end_at: new Date(form.end_at).toISOString(),
        initial_capital: form.initial_capital,
        warmup_bars: 60,
      }),
    onSuccess: (state) => {
      setCreating(false);
      void queryClient.invalidateQueries({ queryKey: queryKeys.replays });
      router.push(`/replay/${state.id}`);
    },
    onError: (error) => toast.fromError(error, "Could not start the replay"),
  });

  if (sessions.isError && sessions.error instanceof ApiError && sessions.error.isEntitlement) {
    return (
      <>
        <PageHeader title="Replay" />
        <UpgradeNotice error={sessions.error} />
      </>
    );
  }

  const matching = (coverage.data ?? []).find((row) => row.timeframe === form.timeframe);

  return (
    <>
      <PageHeader
        title="Replay"
        description="Step through real historical candles bar by bar, with the backtester's execution rules."
        action={
          <Button variant="primary" icon={<Plus className="h-3.5 w-3.5" />} onClick={() => setCreating(true)}>
            New session
          </Button>
        }
      />

      {sessions.isError ? (
        <ErrorState error={sessions.error} onRetry={() => void sessions.refetch()} />
      ) : sessions.isLoading ? (
        <Skeleton className="h-32 rounded" />
      ) : (sessions.data?.length ?? 0) === 0 ? (
        <EmptyState
          icon={<PlayCircle className="h-7 w-7" />}
          title="No replay sessions"
          description="Pick an instrument and a period. The server reveals one candle at a time — you cannot see ahead, and orders fill exactly as they would in a backtest."
          action={<Button variant="primary" onClick={() => setCreating(true)}>Start a session</Button>}
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {sessions.data?.map((session) => (
            <Link key={session.id} href={`/replay/${session.id}`}>
              <Card className="h-full transition-colors hover:border-accent/50">
                <div className="flex items-start justify-between gap-2">
                  <h3 className="text-sm font-semibold">{session.name}</h3>
                  <Badge tone={session.is_finished ? "neutral" : "accent"}>
                    {session.is_finished ? "Finished" : "In progress"}
                  </Badge>
                </div>
                <p className="mt-1 text-xs text-muted">
                  Bar {session.cursor_index + 1} of {session.total_bars} · {session.timeframe}
                </p>
                <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-raised">
                  <div
                    className="h-full rounded-full bg-accent"
                    style={{ width: `${((session.cursor_index + 1) / Math.max(session.total_bars, 1)) * 100}%` }}
                  />
                </div>
                <p className="mt-2 text-2xs text-faint">
                  {session.last_interacted_at ? `Last used ${formatRelative(session.last_interacted_at)}` : "Not started"}
                </p>
              </Card>
            </Link>
          ))}
        </div>
      )}

      <Modal
        open={creating}
        onClose={() => setCreating(false)}
        title="Start a replay session"
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreating(false)}>Cancel</Button>
            <Button
              variant="primary"
              loading={create.isPending}
              disabled={!form.name || !form.instrument_id || !form.start_at || !form.end_at}
              onClick={() => create.mutate()}
            >
              Start
            </Button>
          </>
        }
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name" htmlFor="r-name" className="sm:col-span-2" required>
            <Input id="r-name" value={form.name} onChange={(event) => setForm((f) => ({ ...f, name: event.target.value }))} placeholder="March 2023 practice" />
          </Field>
          <Field label="Instrument" htmlFor="r-instrument" required>
            <Select id="r-instrument" value={form.instrument_id} onChange={(event) => setForm((f) => ({ ...f, instrument_id: event.target.value }))}>
              <option value="">Choose…</option>
              {(instruments.data?.data ?? []).map((instrument) => (
                <option key={instrument.id} value={instrument.id}>{instrument.symbol}</option>
              ))}
            </Select>
          </Field>
          <Field
            label="Timeframe"
            htmlFor="r-timeframe"
            hint={matching ? `${matching.bar_count.toLocaleString()} bars available` : form.instrument_id ? "No candles at this timeframe" : undefined}
          >
            <Select id="r-timeframe" value={form.timeframe} onChange={(event) => setForm((f) => ({ ...f, timeframe: event.target.value }))}>
              {["5m", "15m", "1h", "4h", "1d"].map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </Select>
          </Field>
          <Field label="From" htmlFor="r-start" required
            hint={matching?.first_bar_at ? `Data from ${matching.first_bar_at.slice(0, 10)}` : undefined}>
            <Input id="r-start" type="date" value={form.start_at} onChange={(event) => setForm((f) => ({ ...f, start_at: event.target.value }))} />
          </Field>
          <Field label="To" htmlFor="r-end" required
            hint={matching?.last_bar_at ? `Data to ${matching.last_bar_at.slice(0, 10)}` : undefined}>
            <Input id="r-end" type="date" value={form.end_at} onChange={(event) => setForm((f) => ({ ...f, end_at: event.target.value }))} />
          </Field>
          <Field label="Starting capital" htmlFor="r-capital" className="sm:col-span-2">
            <Input id="r-capital" inputMode="decimal" value={form.initial_capital} onChange={(event) => setForm((f) => ({ ...f, initial_capital: event.target.value }))} />
          </Field>
        </div>
      </Modal>
    </>
  );
}
