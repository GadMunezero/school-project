"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowLeft, GitBranch, Trash2 } from "lucide-react";

import { api } from "@/lib/api";
import { formatDateTime, formatInteger, formatMoney, formatPercent, humanise } from "@/lib/format";
import { queryKeys } from "@/lib/queries";
import { useCurrency } from "@/lib/session";
import type { EngineStrategyInfo, Strategy, StrategyDetail, StrategyVersion } from "@/lib/types";
import { Card, CardHeader, MetricCard } from "@/components/ui/card";
import { ErrorState, MetricsSkeleton, Skeleton } from "@/components/ui/feedback";
import { ConfirmDialog, Modal } from "@/components/ui/overlay";
import { Badge, Button, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/shell/page-header";
import { ParameterFields, parameterDefaults } from "@/components/strategies/parameter-fields";

export default function StrategyDetailPage() {
  const params = useParams<{ strategyId: string }>();
  const strategyId = params.strategyId;
  const router = useRouter();
  const queryClient = useQueryClient();
  const toast = useToast();
  const currency = useCurrency();

  const [editing, setEditing] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const detail = useQuery({
    queryKey: queryKeys.strategy(strategyId),
    queryFn: () => api.get<StrategyDetail>(`/api/v1/strategies/${strategyId}`),
  });

  const remove = useMutation({
    mutationFn: () => api.delete(`/api/v1/strategies/${strategyId}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["strategies"] });
      toast.success("Strategy deleted", "Trades tagged with it keep their history.");
      router.push("/strategies");
    },
    onError: (error) => toast.fromError(error, "Could not delete the strategy"),
  });

  if (detail.isError) {
    return (
      <>
        <PageHeader title="Strategy" />
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      </>
    );
  }

  if (detail.isLoading || !detail.data) {
    return (
      <>
        <PageHeader title="Strategy" />
        <div className="space-y-4">
          <MetricsSkeleton count={3} />
          <Skeleton className="h-64 rounded" />
        </div>
      </>
    );
  }

  const { strategy, versions } = detail.data;
  const isEngine = strategy.kind === "builtin";

  return (
    <>
      <PageHeader
        title={strategy.name}
        description={
          isEngine
            ? `Engine model: ${strategy.engine_key} · ${humanise(strategy.status)}`
            : `Journal strategy · ${humanise(strategy.status)}`
        }
        action={
          <>
            <Link href="/strategies">
              <Button variant="ghost" icon={<ArrowLeft className="h-3.5 w-3.5" />}>
                Back
              </Button>
            </Link>
            <Link href={`/journal?strategy_id=${strategy.id}`}>
              <Button variant="outline">View its trades</Button>
            </Link>
            {isEngine ? (
              <Button
                variant="outline"
                icon={<GitBranch className="h-3.5 w-3.5" />}
                onClick={() => setPublishing(true)}
              >
                New version
              </Button>
            ) : null}
            <Button variant="outline" onClick={() => setEditing(true)}>
              Edit
            </Button>
            <Button
              variant="ghost"
              icon={<Trash2 className="h-3.5 w-3.5" />}
              onClick={() => setConfirmDelete(true)}
            >
              Delete
            </Button>
          </>
        }
      />

      <section className="mb-4 grid gap-3 sm:grid-cols-3">
        <MetricCard
          label="Trades"
          value={formatInteger(strategy.trade_count)}
          hint="Closed trades tagged with this strategy"
        />
        <MetricCard
          label="Net P&L"
          tone="pnl"
          raw={strategy.net_pnl}
          value={formatMoney(strategy.net_pnl, currency, { signed: true })}
        />
        <MetricCard label="Win rate" value={formatPercent(strategy.win_rate)} />
      </section>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Playbook" description="What this strategy is, in your own words." />
          <p className="whitespace-pre-wrap text-sm text-muted">
            {strategy.description?.trim() ? strategy.description : "No description written yet."}
          </p>

          {Object.keys(strategy.playbook ?? {}).length > 0 ? (
            <dl className="mt-4 space-y-2 border-t border-line pt-4">
              {Object.entries(strategy.playbook).map(([key, value]) => (
                <div key={key} className="flex items-baseline justify-between gap-4">
                  <dt className="text-xs text-muted">{humanise(key)}</dt>
                  <dd className="text-right text-sm text-ink">{String(value)}</dd>
                </div>
              ))}
            </dl>
          ) : null}
        </Card>

        <Card>
          <CardHeader title="Details" />
          <dl className="space-y-2">
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-xs text-muted">Kind</dt>
              <dd>
                <Badge tone={isEngine ? "accent" : "neutral"}>
                  {isEngine ? "Engine" : "Journal"}
                </Badge>
              </dd>
            </div>
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-xs text-muted">Status</dt>
              <dd className="text-sm text-ink">{humanise(strategy.status)}</dd>
            </div>
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-xs text-muted">Created</dt>
              <dd className="text-sm text-ink">{formatDateTime(strategy.created_at)}</dd>
            </div>
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-xs text-muted">Versions</dt>
              <dd className="tnum text-sm text-ink">{formatInteger(versions.length)}</dd>
            </div>
          </dl>
        </Card>
      </div>

      {isEngine ? (
        <Card className="mt-4">
          <CardHeader
            title="Parameter versions"
            description="Each version is an immutable set of parameters. Backtests record which one they ran, so a result always maps back to an exact configuration."
          />
          {versions.length === 0 ? (
            <p className="py-6 text-center text-xs text-faint">
              No versions published yet. Publish one to run a backtest against it.
            </p>
          ) : (
            <ul className="divide-y divide-line">
              {versions.map((version) => (
                <VersionRow
                  key={version.id}
                  version={version}
                  isCurrent={version.id === strategy.current_version_id}
                />
              ))}
            </ul>
          )}
        </Card>
      ) : null}

      <EditModal strategy={strategy} open={editing} onClose={() => setEditing(false)} />

      <PublishModal
        strategy={strategy}
        open={publishing}
        onClose={() => setPublishing(false)}
        latest={versions[0]}
      />

      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => remove.mutate()}
        loading={remove.isPending}
        destructive
        confirmLabel="Delete strategy"
        title="Delete this strategy?"
        message="Trades tagged with it keep every number they have — they simply lose the tag. Backtests that ran against it are not deleted."
      />
    </>
  );
}

function VersionRow({ version, isCurrent }: { version: StrategyVersion; isCurrent: boolean }) {
  const entries = Object.entries(version.parameters ?? {});
  return (
    <li className="py-3">
      <div className="flex items-center justify-between gap-3">
        <span className="flex items-center gap-2">
          <span className="tnum text-sm font-semibold text-ink">v{version.version}</span>
          {isCurrent ? <Badge tone="accent">Current</Badge> : null}
          {version.is_published ? null : <Badge tone="neutral">Draft</Badge>}
        </span>
        <span className="text-2xs text-faint">{formatDateTime(version.created_at)}</span>
      </div>
      {version.notes ? <p className="mt-1 text-xs text-muted">{version.notes}</p> : null}
      {entries.length > 0 ? (
        <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
          {entries.map(([key, value]) => (
            <div key={key} className="flex items-baseline gap-1.5">
              <dt className="text-2xs text-faint">{humanise(key)}</dt>
              <dd className="tnum text-xs font-medium text-ink">{String(value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </li>
  );
}

function EditModal({
  strategy,
  open,
  onClose,
}: {
  strategy: Strategy;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState({
    name: strategy.name,
    description: strategy.description ?? "",
    color: strategy.color ?? "#115e4f",
    status: strategy.status,
  });

  const save = useMutation({
    mutationFn: () =>
      api.patch<Strategy>(`/api/v1/strategies/${strategy.id}`, {
        name: form.name.trim(),
        description: form.description.trim() || null,
        color: form.color,
        status: form.status,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["strategies"] });
      onClose();
    },
    onError: (error) => toast.fromError(error, "Could not save the strategy"),
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Edit strategy"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={save.isPending}
            disabled={form.name.trim() === ""}
            onClick={() => save.mutate()}
          >
            Save
          </Button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name" htmlFor="se-name" className="sm:col-span-2" required>
          <Input
            id="se-name"
            value={form.name}
            onChange={(event) => setForm((f) => ({ ...f, name: event.target.value }))}
          />
        </Field>
        <Field label="Status" htmlFor="se-status">
          <Select
            id="se-status"
            value={form.status}
            onChange={(event) => setForm((f) => ({ ...f, status: event.target.value }))}
          >
            <option value="active">Active</option>
            <option value="paused">Paused</option>
            <option value="retired">Retired</option>
          </Select>
        </Field>
        <Field label="Colour" htmlFor="se-color">
          <Input
            id="se-color"
            type="color"
            className="h-9 w-full p-1"
            value={form.color}
            onChange={(event) => setForm((f) => ({ ...f, color: event.target.value }))}
          />
        </Field>
        <Field label="Description" htmlFor="se-desc" className="sm:col-span-2">
          <Textarea
            id="se-desc"
            rows={5}
            value={form.description}
            onChange={(event) => setForm((f) => ({ ...f, description: event.target.value }))}
          />
        </Field>
      </div>
    </Modal>
  );
}

function PublishModal({
  strategy,
  open,
  onClose,
  latest,
}: {
  strategy: Strategy;
  open: boolean;
  onClose: () => void;
  latest?: StrategyVersion;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [values, setValues] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState("");

  const engines = useQuery({
    queryKey: queryKeys.engineStrategies,
    queryFn: () => api.get<EngineStrategyInfo[]>("/api/v1/strategies/engine"),
    enabled: open,
  });

  const spec = engines.data?.find((engine) => engine.key === strategy.engine_key);

  // Start from the previous version's values where they exist, so publishing a tweak means
  // changing one number rather than retyping the whole set.
  useEffect(() => {
    if (!spec) return;
    const defaults = parameterDefaults(spec.parameters);
    const previous = latest?.parameters ?? {};
    for (const [key, value] of Object.entries(previous)) {
      if (key in defaults) defaults[key] = String(value);
    }
    setValues(defaults);
  }, [spec, latest]);

  const publish = useMutation({
    mutationFn: () =>
      api.post<StrategyVersion>(`/api/v1/strategies/${strategy.id}/versions`, {
        parameters: values,
        notes: notes.trim() || null,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["strategies"] });
      setNotes("");
      onClose();
    },
    onError: (error) => toast.fromError(error, "Could not publish the version"),
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Publish a parameter version"
      description="Versions are immutable. Backtests reference the version they ran, so their results stay reproducible."
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={publish.isPending} onClick={() => publish.mutate()}>
            Publish v{(latest?.version ?? 0) + 1}
          </Button>
        </>
      }
    >
      {engines.isLoading ? (
        <Skeleton className="h-32 rounded" />
      ) : !spec ? (
        <p className="text-sm text-muted">
          The engine no longer registers a model called{" "}
          <span className="font-mono text-xs">{strategy.engine_key}</span>, so its parameters cannot
          be shown.
        </p>
      ) : (
        <div className="space-y-4">
          <ParameterFields
            specs={spec.parameters}
            values={values}
            idPrefix="version"
            onChange={(name, value) => setValues((current) => ({ ...current, [name]: value }))}
          />
          <Field label="What changed?" htmlFor="v-notes">
            <Textarea
              id="v-notes"
              rows={3}
              value={notes}
              placeholder="Widened the stop after the March drawdown."
              onChange={(event) => setNotes(event.target.value)}
            />
          </Field>
        </div>
      )}
    </Modal>
  );
}
