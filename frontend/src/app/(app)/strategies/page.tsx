"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Lightbulb, Plus } from "lucide-react";

import { api } from "@/lib/api";
import { formatInteger, formatMoney, formatPercent, humanise, pnlClass } from "@/lib/format";
import { queryKeys } from "@/lib/queries";
import { useCurrency } from "@/lib/session";
import type { EngineStrategyInfo, Strategy } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Card } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import { Modal } from "@/components/ui/overlay";
import { Badge, Button, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/shell/page-header";

export default function StrategiesPage() {
  const currency = useCurrency();
  const [creating, setCreating] = useState(false);

  const strategies = useQuery({
    queryKey: queryKeys.strategies(),
    queryFn: () => api.list<Strategy>("/api/v1/strategies", { page_size: 100 }),
  });

  const rows = strategies.data?.data ?? [];

  return (
    <>
      <PageHeader
        title="Strategies"
        description="Name what you actually trade. Tag your trades with a strategy and the journal reports each one separately."
        action={
          <Button
            variant="primary"
            icon={<Plus className="h-3.5 w-3.5" />}
            onClick={() => setCreating(true)}
          >
            New strategy
          </Button>
        }
      />

      {strategies.isError ? (
        <ErrorState error={strategies.error} onRetry={() => void strategies.refetch()} />
      ) : strategies.isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((key) => (
            <Skeleton key={key} className="h-36 rounded" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<Lightbulb className="h-7 w-7" />}
          title="No strategies defined"
          description="A strategy is how you group trades that share an idea. Two kinds exist: a plain journal strategy you tag trades with, and one backed by a built-in engine model you can also backtest."
          action={
            <Button variant="primary" onClick={() => setCreating(true)}>
              Define a strategy
            </Button>
          }
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {rows.map((strategy) => (
            <Link key={strategy.id} href={`/strategies/${strategy.id}`}>
              <Card className="h-full transition-colors hover:border-accent/50">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <span
                      aria-hidden
                      className="h-2.5 w-2.5 shrink-0 rounded-full"
                      style={{ backgroundColor: strategy.color ?? "rgb(var(--faint))" }}
                    />
                    <h2 className="truncate text-sm font-semibold text-ink">{strategy.name}</h2>
                  </div>
                  <Badge tone={strategy.kind === "builtin" ? "accent" : "neutral"}>
                    {strategy.kind === "builtin" ? "Engine" : "Journal"}
                  </Badge>
                </div>

                <p className="mt-1 line-clamp-2 text-xs text-muted">
                  {strategy.description ?? "No description."}
                </p>

                <dl className="mt-4 grid grid-cols-3 gap-2 border-t border-line pt-3 text-xs">
                  <div>
                    <dt className="text-2xs text-faint">Trades</dt>
                    <dd className="tnum font-medium text-ink">
                      {formatInteger(strategy.trade_count)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-2xs text-faint">Net P&amp;L</dt>
                    <dd className={cn("tnum font-medium", pnlClass(strategy.net_pnl))}>
                      {formatMoney(strategy.net_pnl, currency, { signed: true, places: 0 })}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-2xs text-faint">Win rate</dt>
                    <dd className="tnum font-medium text-ink">{formatPercent(strategy.win_rate)}</dd>
                  </div>
                </dl>

                {strategy.status !== "active" ? (
                  <p className="mt-2 text-2xs text-faint">{humanise(strategy.status)}</p>
                ) : null}
              </Card>
            </Link>
          ))}
        </div>
      )}

      <CreateModal open={creating} onClose={() => setCreating(false)} />
    </>
  );
}

function CreateModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState({
    name: "",
    description: "",
    kind: "journal_only",
    engine_key: "",
    color: "#115e4f",
  });

  const engines = useQuery({
    queryKey: queryKeys.engineStrategies,
    queryFn: () => api.get<EngineStrategyInfo[]>("/api/v1/strategies/engine"),
    enabled: open,
  });

  const create = useMutation({
    mutationFn: () =>
      api.post<Strategy>("/api/v1/strategies", {
        name: form.name.trim(),
        description: form.description.trim() || null,
        kind: form.kind,
        // Only an engine-backed strategy carries a key, and it must be one the engine registered.
        engine_key: form.kind === "builtin" ? form.engine_key : null,
        color: form.color,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["strategies"] });
      setForm((current) => ({ ...current, name: "", description: "" }));
      onClose();
    },
    onError: (error) => toast.fromError(error, "Could not create the strategy"),
  });

  const selectedEngine = engines.data?.find((engine) => engine.key === form.engine_key);
  const incomplete = form.name.trim() === "" || (form.kind === "builtin" && !form.engine_key);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New strategy"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={create.isPending}
            disabled={incomplete}
            onClick={() => create.mutate()}
          >
            Create
          </Button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name" htmlFor="s-name" className="sm:col-span-2" required>
          <Input
            id="s-name"
            value={form.name}
            placeholder="Opening range breakout"
            onChange={(event) => setForm((f) => ({ ...f, name: event.target.value }))}
          />
        </Field>

        <Field
          label="Kind"
          htmlFor="s-kind"
          hint="Engine strategies can also be backtested."
          className="sm:col-span-2"
        >
          <Select
            id="s-kind"
            value={form.kind}
            onChange={(event) => setForm((f) => ({ ...f, kind: event.target.value }))}
          >
            <option value="journal_only">Journal only — I trade this by hand</option>
            <option value="builtin">Engine model — backtestable</option>
          </Select>
        </Field>

        {form.kind === "builtin" ? (
          <Field
            label="Engine model"
            htmlFor="s-engine"
            hint={selectedEngine?.description ?? "Only models the backtester implements are listed."}
            className="sm:col-span-2"
            required
          >
            <Select
              id="s-engine"
              value={form.engine_key}
              onChange={(event) => setForm((f) => ({ ...f, engine_key: event.target.value }))}
            >
              <option value="">Choose a model…</option>
              {(engines.data ?? []).map((engine) => (
                <option key={engine.key} value={engine.key}>
                  {engine.name}
                </option>
              ))}
            </Select>
          </Field>
        ) : null}

        <Field label="Colour" htmlFor="s-color">
          <Input
            id="s-color"
            type="color"
            value={form.color}
            className="h-9 w-full p-1"
            onChange={(event) => setForm((f) => ({ ...f, color: event.target.value }))}
          />
        </Field>

        <Field label="Description" htmlFor="s-desc" className="sm:col-span-2">
          <Textarea
            id="s-desc"
            rows={3}
            value={form.description}
            placeholder="When do you take it, and when do you stand aside?"
            onChange={(event) => setForm((f) => ({ ...f, description: event.target.value }))}
          />
        </Field>
      </div>
    </Modal>
  );
}
