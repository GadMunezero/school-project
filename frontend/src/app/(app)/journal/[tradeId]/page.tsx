"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import { ArrowLeft, ImagePlus, Save, Trash2 } from "lucide-react";

import { api } from "@/lib/api";
import {
  formatDateTime,
  formatDuration,
  formatMoney,
  formatPercent,
  formatPrice,
  formatQuantity,
  formatR,
  formatRatio,
  humanise,
  signOf,
} from "@/lib/format";
import { queryKeys } from "@/lib/queries";
import type { CandleResponse, Setup, Strategy, Tag, TradeDetail } from "@/lib/types";
import { cn } from "@/lib/utils";
import { CandleChart, type PriceLevel, type TradeMarker } from "@/components/charts/candle-chart";
import { Card, CardHeader, MetricCard } from "@/components/ui/card";
import { ErrorState, Skeleton } from "@/components/ui/feedback";
import { Badge, Button, Field, Select, Textarea } from "@/components/ui/primitives";
import { ConfirmDialog } from "@/components/ui/overlay";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/shell/page-header";

export default function TradeDetailPage() {
  const params = useParams<{ tradeId: string }>();
  const tradeId = params.tradeId;
  const router = useRouter();
  const queryClient = useQueryClient();
  const toast = useToast();

  const [confirmDelete, setConfirmDelete] = useState(false);
  const [notes, setNotes] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const detail = useQuery({
    queryKey: queryKeys.trade(tradeId),
    queryFn: () => api.get<TradeDetail>(`/api/v1/trades/${tradeId}`),
  });

  const strategies = useQuery({
    queryKey: queryKeys.strategies(),
    queryFn: () => api.list<Strategy>("/api/v1/strategies", { page_size: 100 }),
  });
  const setups = useQuery({ queryKey: queryKeys.setups, queryFn: () => api.get<Setup[]>("/api/v1/setups") });
  const tags = useQuery({ queryKey: queryKeys.tags, queryFn: () => api.get<Tag[]>("/api/v1/tags") });

  const trade = detail.data?.trade;

  // Candles covering the holding period, padded either side so the trade has context.
  const candleQuery = useQuery({
    queryKey: queryKeys.candles({ trade: tradeId }),
    enabled: Boolean(trade?.instrument_id),
    queryFn: () => {
      const entry = new Date(trade!.entry_timestamp).getTime();
      const exit = trade!.exit_timestamp ? new Date(trade!.exit_timestamp).getTime() : Date.now();
      const padding = Math.max((exit - entry) * 1.5, 1000 * 60 * 60 * 24 * 20);
      return api.get<CandleResponse>("/api/v1/market-data/candles", {
        instrument_id: trade!.instrument_id!,
        timeframe: "1d",
        start: new Date(entry - padding).toISOString(),
        end: new Date(exit + padding).toISOString(),
        limit: 400,
      });
    },
    retry: false,
  });

  const update = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      api.patch(`/api/v1/trades/${tradeId}`, payload),
    onSuccess: () => {
      toast.success("Trade updated.");
      setNotes(null);
      void queryClient.invalidateQueries({ queryKey: queryKeys.trade(tradeId) });
      void queryClient.invalidateQueries({ queryKey: ["trades"] });
    },
    onError: (error) => toast.fromError(error, "Could not update the trade"),
  });

  const remove = useMutation({
    mutationFn: () => api.delete(`/api/v1/trades/${tradeId}`),
    onSuccess: () => {
      toast.success("Trade deleted.");
      void queryClient.invalidateQueries({ queryKey: ["trades"] });
      router.push("/journal");
    },
    onError: (error) => toast.fromError(error, "Could not delete the trade"),
  });

  const uploadScreenshot = useMutation({
    mutationFn: (file: File) => {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("trade_id", tradeId);
      formData.append("phase", "review");
      return api.upload<{ id: string; url: string }>("/api/v1/files/screenshots", formData);
    },
    onSuccess: () => {
      toast.success("Screenshot attached.");
      void queryClient.invalidateQueries({ queryKey: queryKeys.trade(tradeId) });
    },
    onError: (error) => toast.fromError(error, "Could not upload the screenshot"),
  });

  const levels = useMemo<PriceLevel[]>(() => {
    if (!trade) return [];
    const result: PriceLevel[] = [{ price: trade.entry_price, label: "Entry", tone: "entry" }];
    if (trade.exit_price) result.push({ price: trade.exit_price, label: "Exit", tone: "exit" });
    if (trade.initial_stop_loss ?? trade.stop_loss)
      result.push({ price: trade.initial_stop_loss ?? trade.stop_loss, label: "Stop", tone: "stop" });
    if (trade.take_profit) result.push({ price: trade.take_profit, label: "Target", tone: "target" });
    return result;
  }, [trade]);

  const markers = useMemo<TradeMarker[]>(
    () =>
      (detail.data?.orders ?? [])
        .filter((order) => order.filled_at)
        .map((order) => ({
          time: order.filled_at!,
          side: order.side,
          label: `${order.side === "buy" ? "B" : "S"} ${formatQuantity(order.filled_quantity)}`,
        })),
    [detail.data?.orders],
  );

  if (detail.isError) {
    return (
      <>
        <PageHeader title="Trade" />
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      </>
    );
  }

  if (detail.isLoading || !trade) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-24 rounded" />
        <Skeleton className="h-80 rounded" />
      </div>
    );
  }

  const closed = trade.status === "closed";

  return (
    <>
      <PageHeader
        title={`${trade.symbol} ${humanise(trade.direction)}`}
        description={`${formatDateTime(trade.entry_timestamp)}${trade.exit_timestamp ? ` → ${formatDateTime(trade.exit_timestamp)}` : " · still open"}`}
        action={
          <>
            <Link href="/journal">
              <Button variant="ghost" icon={<ArrowLeft className="h-3.5 w-3.5" />}>
                Back
              </Button>
            </Link>
            <Button
              variant="danger"
              icon={<Trash2 className="h-3.5 w-3.5" />}
              onClick={() => setConfirmDelete(true)}
            >
              Delete
            </Button>
          </>
        }
      />

      <div className="space-y-4">
        <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <MetricCard
            label="Net P&L"
            tone="pnl"
            raw={trade.net_pnl}
            value={closed ? formatMoney(trade.net_pnl, trade.currency, { signed: true }) : "Open"}
            hint={closed ? `${formatPercent(trade.return_percentage, { signed: true })} on cost` : undefined}
          />
          <MetricCard label="R multiple" value={formatR(trade.r_multiple)} hint={trade.risk_amount ? `Risked ${formatMoney(trade.risk_amount, trade.currency)}` : "No stop recorded"} />
          <MetricCard
            label="Quantity"
            value={formatQuantity(trade.quantity)}
            hint={
              signOf(trade.remaining_quantity) > 0
                ? `${formatQuantity(trade.remaining_quantity)} still open`
                : "Fully closed"
            }
          />
          <MetricCard
            label="Commission"
            value={formatMoney(trade.commission, trade.currency)}
            hint={`Fees ${formatMoney(trade.fees, trade.currency)}`}
          />
          <MetricCard label="Held" value={formatDuration(trade.holding_seconds)} hint={trade.session ? humanise(trade.session) : undefined} />
        </section>

        <Card>
          <CardHeader
            title="Price"
            description={
              candleQuery.data?.source
                ? `${candleQuery.data.source.name}${candleQuery.data.source.is_realtime ? "" : " · historical data"}`
                : undefined
            }
          />
          {candleQuery.isLoading ? (
            <Skeleton className="h-[380px] rounded" />
          ) : (
            <CandleChart
              candles={candleQuery.data?.candles ?? []}
              levels={levels}
              markers={markers}
              emptyMessage={
                trade.instrument_id
                  ? "No candles are loaded for this instrument and period."
                  : "This trade is not linked to an instrument in the catalogue, so no chart is available."
              }
            />
          )}
        </Card>

        <section className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader title="Executions" description="The fills this trade was built from." />
            {detail.data?.orders.length === 0 ? (
              <p className="py-6 text-center text-xs text-faint">
                Recorded manually — no individual fills were captured.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-line text-2xs uppercase tracking-wide text-faint">
                      <th className="py-2 text-left font-semibold">Time</th>
                      <th className="py-2 text-left font-semibold">Side</th>
                      <th className="py-2 text-left font-semibold">Effect</th>
                      <th className="py-2 text-right font-semibold">Qty</th>
                      <th className="py-2 text-right font-semibold">Price</th>
                      <th className="py-2 text-right font-semibold">Commission</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.data?.orders.map((order) => (
                      <tr key={order.id} className="border-b border-line last:border-0">
                        <td className="py-2 text-xs text-muted">{formatDateTime(order.filled_at ?? order.placed_at)}</td>
                        <td className="py-2">
                          <Badge tone={order.side === "buy" ? "profit" : "loss"}>{order.side}</Badge>
                        </td>
                        <td className="py-2 text-xs text-muted">
                          {order.is_entry === null ? "—" : order.is_entry ? "Open" : "Close"}
                        </td>
                        <td className="tnum py-2 text-right text-xs">{formatQuantity(order.filled_quantity)}</td>
                        <td className="tnum py-2 text-right text-xs">{formatPrice(order.average_fill_price)}</td>
                        <td className="tnum py-2 text-right text-xs text-muted">
                          {formatMoney(order.commission, trade.currency)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card>
            <CardHeader title="Execution quality" />
            <dl className="space-y-3">
              <Row label="Entry (avg)" value={formatPrice(trade.entry_price)} />
              <Row label="Exit (avg)" value={formatPrice(trade.exit_price)} />
              <Row label="Initial stop" value={formatPrice(trade.initial_stop_loss ?? trade.stop_loss)} />
              <Row label="Target" value={formatPrice(trade.take_profit)} />
              <Row
                label="Planned R:R"
                value={formatRatio(detail.data?.planned_reward_risk)}
              />
              <Row
                label="MFE"
                value={formatMoney(trade.mfe_amount, trade.currency)}
                hint={trade.mfe_amount === null ? "No covering candles" : undefined}
              />
              <Row label="MAE" value={formatMoney(trade.mae_amount, trade.currency)} />
              <Row
                label="Capture"
                value={formatRatio(detail.data?.efficiency)}
                hint="Net P&L ÷ MFE — 1.00 means you exited at the high-water mark"
              />
            </dl>
          </Card>
        </section>

        <section className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader
              title="Notes"
              action={
                notes !== null ? (
                  <Button
                    size="sm"
                    variant="primary"
                    icon={<Save className="h-3.5 w-3.5" />}
                    loading={update.isPending}
                    onClick={() => update.mutate({ notes })}
                  >
                    Save
                  </Button>
                ) : null
              }
            />
            <Textarea
              rows={6}
              value={notes ?? trade.notes ?? ""}
              placeholder="What was the thesis? What would you do differently?"
              onChange={(event) => setNotes(event.target.value)}
            />

            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <Field label="Strategy" htmlFor="strategy">
                <Select
                  id="strategy"
                  value={trade.strategy_id ?? ""}
                  onChange={(event) => update.mutate({ strategy_id: event.target.value || null })}
                >
                  <option value="">None</option>
                  {(strategies.data?.data ?? []).map((strategy) => (
                    <option key={strategy.id} value={strategy.id}>
                      {strategy.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Setup" htmlFor="setup">
                <Select
                  id="setup"
                  value={trade.setup_id ?? ""}
                  onChange={(event) => update.mutate({ setup_id: event.target.value || null })}
                >
                  <option value="">None</option>
                  {(setups.data ?? []).map((setup) => (
                    <option key={setup.id} value={setup.id}>
                      {setup.name}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>

            <div className="mt-4">
              <p className="mb-1.5 text-xs font-medium text-muted">Tags</p>
              <div className="flex flex-wrap gap-1.5">
                {(tags.data ?? []).map((tag) => {
                  const applied = trade.tags.some((item) => item.id === tag.id);
                  return (
                    <button
                      key={tag.id}
                      type="button"
                      onClick={() =>
                        update.mutate({
                          tag_ids: applied
                            ? trade.tags.filter((item) => item.id !== tag.id).map((item) => item.id)
                            : [...trade.tags.map((item) => item.id), tag.id],
                        })
                      }
                      className={cn(
                        "rounded px-2 py-1 text-2xs font-medium transition-colors",
                        applied ? "bg-accent text-accent-ink" : "bg-raised text-muted hover:text-ink",
                      )}
                    >
                      {tag.name}
                    </button>
                  );
                })}
              </div>
            </div>
          </Card>

          <Card>
            <CardHeader
              title="Screenshots"
              action={
                <>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/png,image/jpeg,image/webp,image/gif"
                    className="hidden"
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      if (file) uploadScreenshot.mutate(file);
                      event.target.value = "";
                    }}
                  />
                  <Button
                    size="sm"
                    variant="outline"
                    icon={<ImagePlus className="h-3.5 w-3.5" />}
                    loading={uploadScreenshot.isPending}
                    onClick={() => fileInputRef.current?.click()}
                  >
                    Attach
                  </Button>
                </>
              }
            />
            {detail.data?.screenshots.length === 0 ? (
              <p className="py-8 text-center text-xs text-faint">
                No screenshots yet. Attach a chart to remember what you saw.
              </p>
            ) : (
              <div className="grid grid-cols-2 gap-2">
                {detail.data?.screenshots.map((screenshot) =>
                  screenshot.url ? (
                    <a
                      key={screenshot.id}
                      href={screenshot.url}
                      target="_blank"
                      rel="noreferrer"
                      className="group block overflow-hidden rounded border border-line"
                    >
                      <img
                        src={screenshot.url}
                        alt={screenshot.caption ?? `${trade.symbol} chart`}
                        className="aspect-video w-full object-cover transition-transform group-hover:scale-[1.02]"
                      />
                    </a>
                  ) : (
                    <div
                      key={screenshot.id}
                      className="flex aspect-video items-center justify-center rounded border border-dashed border-line text-2xs text-faint"
                    >
                      Image unavailable
                    </div>
                  ),
                )}
              </div>
            )}
          </Card>
        </section>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => remove.mutate()}
        title="Delete this trade?"
        message="It will be removed from your journal and excluded from every analytic. This cannot be undone from the interface."
        confirmLabel="Delete trade"
        destructive
        loading={remove.isPending}
      />
    </>
  );
}

function Row({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="text-right">
        <span className="tnum text-sm text-ink">{value}</span>
        {hint ? <span className="block text-2xs text-faint">{hint}</span> : null}
      </dd>
    </div>
  );
}
