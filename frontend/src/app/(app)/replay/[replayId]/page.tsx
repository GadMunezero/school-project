"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, ChevronRight, FastForward, Flag, Trash2, XCircle } from "lucide-react";

import { api } from "@/lib/api";
import {
  formatDateTime,
  formatMoney,
  formatQuantity,
  formatR,
  humanise,
  pnlClass,
  signOf,
} from "@/lib/format";
import { queryKeys } from "@/lib/queries";
import type { ReplayState } from "@/lib/types";
import { cn } from "@/lib/utils";
import { CandleChart, type PriceLevel, type TradeMarker } from "@/components/charts/candle-chart";
import { SeriesChart } from "@/components/charts/series-chart";
import { Card, CardHeader } from "@/components/ui/card";
import { ErrorState, Skeleton } from "@/components/ui/feedback";
import { ConfirmDialog } from "@/components/ui/overlay";
import { Badge, Button, Field, Input, Select } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/shell/page-header";

const STEP_SIZES = [1, 5, 25];

export default function ReplayDetailPage() {
  const params = useParams<{ replayId: string }>();
  const replayId = params.replayId;
  const router = useRouter();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [confirmDelete, setConfirmDelete] = useState(false);

  const state = useQuery({
    queryKey: queryKeys.replay(replayId),
    queryFn: () => api.get<ReplayState>(`/api/v1/replay/${replayId}`),
  });

  /**
   * Every mutation returns the full new state, so we write it straight into the cache instead of
   * refetching. That keeps the chart from flickering back to the previous bar between the response
   * and a refetch — and, more importantly, means the client never holds a state the server did not
   * produce.
   */
  const applyState = useCallback(
    (next: ReplayState) => queryClient.setQueryData(queryKeys.replay(replayId), next),
    [queryClient, replayId],
  );

  const step = useMutation({
    mutationFn: (steps: number) =>
      api.post<ReplayState>(`/api/v1/replay/${replayId}/step`, { steps }),
    onSuccess: applyState,
    onError: (error) => toast.fromError(error, "Could not advance the replay"),
  });

  const placeOrder = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      api.post<ReplayState>(`/api/v1/replay/${replayId}/orders`, payload),
    onSuccess: (next) => {
      applyState(next);
      toast.success(
        "Order submitted",
        "It becomes eligible to fill on the next bar, exactly as in a backtest.",
      );
    },
    onError: (error) => toast.fromError(error, "Order rejected"),
  });

  const setProtection = useMutation({
    mutationFn: (payload: { stop_loss: string | null; take_profit: string | null }) =>
      api.post<ReplayState>(`/api/v1/replay/${replayId}/protection`, payload),
    onSuccess: applyState,
    onError: (error) => toast.fromError(error, "Could not update the stop and target"),
  });

  const closePosition = useMutation({
    mutationFn: () => api.action<ReplayState>(`/api/v1/replay/${replayId}/close`),
    onSuccess: applyState,
    onError: (error) => toast.fromError(error, "Could not close the position"),
  });

  const remove = useMutation({
    mutationFn: () => api.delete(`/api/v1/replay/${replayId}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.replays });
      router.push("/replay");
    },
    onError: (error) => toast.fromError(error, "Could not delete the session"),
  });

  const data = state.data;
  const finished = data?.is_finished ?? false;
  const busy = step.isPending || placeOrder.isPending || closePosition.isPending;

  // Keyboard stepping. The right-arrow is the primary control of this screen, so it gets a key.
  useEffect(() => {
    if (!data || finished) return;
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName)) return;
      if (event.key === "ArrowRight") {
        event.preventDefault();
        if (!step.isPending) step.mutate(1);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [data, finished, step]);

  const levels = useMemo<PriceLevel[]>(() => {
    if (!data?.position) return [];
    const list: PriceLevel[] = [
      { price: data.position.average_price, label: "Avg", tone: "entry" },
    ];
    if (data.position.stop_loss) list.push({ price: data.position.stop_loss, label: "Stop", tone: "stop" });
    if (data.position.take_profit) list.push({ price: data.position.take_profit, label: "Target", tone: "target" });
    return list;
  }, [data?.position]);

  const markers = useMemo<TradeMarker[]>(() => {
    if (!data) return [];
    const list: TradeMarker[] = [];
    for (const trade of data.closed_trades) {
      const long = trade.direction === "long";
      list.push({ time: trade.entry_timestamp, side: long ? "buy" : "sell", label: `#${trade.sequence}` });
      if (trade.exit_timestamp) {
        list.push({ time: trade.exit_timestamp, side: long ? "sell" : "buy", label: `#${trade.sequence}` });
      }
    }
    return list;
  }, [data]);

  if (state.isError) {
    return (
      <>
        <PageHeader title="Replay" />
        <ErrorState error={state.error} onRetry={() => void state.refetch()} />
      </>
    );
  }

  if (state.isLoading || !data) {
    return (
      <>
        <PageHeader title="Replay" />
        <div className="space-y-4">
          <Skeleton className="h-96 rounded" />
          <Skeleton className="h-40 rounded" />
        </div>
      </>
    );
  }

  const progress = ((data.cursor_index + 1) / Math.max(data.total_bars, 1)) * 100;

  return (
    <>
      <PageHeader
        title={data.name}
        description={`${data.instrument.symbol} · ${data.timeframe} · bar ${data.cursor_index + 1} of ${data.total_bars}`}
        action={
          <>
            <Link href="/replay">
              <Button variant="ghost" icon={<ArrowLeft className="h-3.5 w-3.5" />}>
                Back
              </Button>
            </Link>
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

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-4">
          <Card padded={false}>
            <div className="border-b border-line px-4 py-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <Button
                    variant="primary"
                    icon={<ChevronRight className="h-3.5 w-3.5" />}
                    disabled={finished || busy}
                    loading={step.isPending && step.variables === 1}
                    onClick={() => step.mutate(1)}
                  >
                    Next bar
                  </Button>
                  {STEP_SIZES.slice(1).map((size) => (
                    <Button
                      key={size}
                      variant="outline"
                      icon={<FastForward className="h-3.5 w-3.5" />}
                      disabled={finished || busy}
                      loading={step.isPending && step.variables === size}
                      onClick={() => step.mutate(size)}
                    >
                      +{size}
                    </Button>
                  ))}
                  {finished ? (
                    <Badge tone="neutral">
                      <Flag className="mr-1 h-3 w-3" aria-hidden />
                      Session finished
                    </Badge>
                  ) : (
                    <span className="text-2xs text-faint">or press →</span>
                  )}
                </div>
                <div className="tnum text-xs text-muted">
                  Close{" "}
                  <span className="font-medium text-ink">{data.current_bar?.close ?? "—"}</span>
                  <span className="mx-2 text-line">|</span>
                  {formatDateTime(data.current_bar?.time)}
                </div>
              </div>
              <div className="mt-3 h-1 overflow-hidden rounded-full bg-raised">
                <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${progress}%` }} />
              </div>
            </div>
            <div className="p-2">
              <CandleChart candles={data.visible_candles} levels={levels} markers={markers} height={420} />
            </div>
            <p className="border-t border-line px-4 py-2 text-2xs text-faint">
              The chart holds only the {data.visible_candles.length} bars the server has revealed.
              Future candles are not sent to your browser, so they cannot be inspected.
            </p>
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Equity" description="Marked at each bar close." />
              <SeriesChart
                height={180}
                baseline={data.initial_capital}
                points={data.equity_curve.map((point) => ({
                  timestamp: point.time,
                  value: point.equity,
                }))}
              />
            </Card>

            <Card>
              <CardHeader title="Closed trades" description={`${data.closed_trades.length} so far`} />
              {data.closed_trades.length === 0 ? (
                <p className="py-6 text-center text-xs text-faint">No trades closed yet.</p>
              ) : (
                <ul className="divide-y divide-line">
                  {[...data.closed_trades].reverse().map((trade) => (
                    <li key={trade.sequence} className="flex items-center justify-between gap-3 py-2">
                      <span className="text-xs text-muted">
                        #{trade.sequence} · {humanise(trade.direction)} {formatQuantity(trade.quantity)}
                        {trade.exit_reason ? ` · ${humanise(trade.exit_reason)}` : ""}
                      </span>
                      <span className="flex items-center gap-3">
                        <span className="tnum text-2xs text-faint">{formatR(trade.r_multiple)}</span>
                        <span className={cn("tnum text-sm font-medium", pnlClass(trade.net_pnl))}>
                          {formatMoney(trade.net_pnl, data.currency, { signed: true })}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        </div>

        <aside className="space-y-4">
          <Card>
            <CardHeader title="Account" />
            <dl className="space-y-2">
              <Row label="Equity" value={formatMoney(data.equity, data.currency)} />
              <Row label="Cash" value={formatMoney(data.cash, data.currency)} />
              <Row
                label="Realised"
                value={formatMoney(data.realized_pnl, data.currency, { signed: true })}
                tone={data.realized_pnl}
              />
              <Row
                label="Unrealised"
                value={formatMoney(data.unrealized_pnl, data.currency, { signed: true })}
                tone={data.unrealized_pnl}
              />
            </dl>
          </Card>

          <PositionPanel
            state={data}
            disabled={busy || finished}
            onClose={() => closePosition.mutate()}
            closing={closePosition.isPending}
            onProtection={(payload) => setProtection.mutate(payload)}
            savingProtection={setProtection.isPending}
          />

          <OrderTicket
            state={data}
            disabled={busy || finished}
            pending={placeOrder.isPending}
            onSubmit={(payload) => placeOrder.mutate(payload)}
          />

          {data.working_orders.length > 0 ? (
            <Card>
              <CardHeader title="Working orders" description="Eligible from the next bar." />
              <ul className="divide-y divide-line">
                {data.working_orders.map((order) => (
                  <li key={order.id} className="py-2 text-xs">
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-ink">
                        {humanise(order.side)} {formatQuantity(order.quantity)}
                      </span>
                      <Badge tone="neutral">{humanise(order.order_type)}</Badge>
                    </div>
                    <p className="mt-0.5 tnum text-2xs text-faint">
                      {order.limit_price ? `limit ${order.limit_price}` : null}
                      {order.limit_price && order.stop_price ? " · " : null}
                      {order.stop_price ? `stop ${order.stop_price}` : null}
                      {!order.limit_price && !order.stop_price ? humanise(order.intent) : null}
                    </p>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}
        </aside>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => remove.mutate()}
        loading={remove.isPending}
        destructive
        confirmLabel="Delete session"
        title="Delete this replay session?"
        message="The session and everything you traded inside it are removed. Your journal is not affected."
      />
    </>
  );
}

function Row({ label, value, tone }: { label: string; value: string; tone?: string | null }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className={cn("tnum text-sm font-semibold", tone === undefined ? "text-ink" : pnlClass(tone))}>
        {value}
      </dd>
    </div>
  );
}

function PositionPanel({
  state,
  disabled,
  onClose,
  closing,
  onProtection,
  savingProtection,
}: {
  state: ReplayState;
  disabled: boolean;
  onClose: () => void;
  closing: boolean;
  onProtection: (payload: { stop_loss: string | null; take_profit: string | null }) => void;
  savingProtection: boolean;
}) {
  const position = state.position;
  const [stop, setStop] = useState("");
  const [target, setTarget] = useState("");

  // Re-seed the inputs whenever the server's protection levels change (a fill can move them).
  useEffect(() => {
    setStop(position?.stop_loss ?? "");
    setTarget(position?.take_profit ?? "");
  }, [position?.stop_loss, position?.take_profit]);

  if (!position) {
    return (
      <Card>
        <CardHeader title="Position" />
        <p className="py-4 text-center text-xs text-faint">Flat. Place an order to open one.</p>
      </Card>
    );
  }

  const long = position.direction === "long";

  return (
    <Card>
      <CardHeader
        title="Position"
        action={
          <Badge tone={long ? "profit" : "loss"}>{long ? "Long" : "Short"}</Badge>
        }
      />
      <dl className="space-y-2">
        <Row label="Quantity" value={formatQuantity(position.quantity)} />
        <Row label="Average price" value={position.average_price ?? "—"} />
        <Row
          label="Open P&L"
          value={formatMoney(position.unrealized_pnl, state.currency, { signed: true })}
          tone={position.unrealized_pnl}
        />
      </dl>

      <div className="mt-3 grid grid-cols-2 gap-2 border-t border-line pt-3">
        <Field label="Stop" htmlFor="rp-stop">
          <Input
            id="rp-stop"
            inputMode="decimal"
            value={stop}
            placeholder="none"
            onChange={(event) => setStop(event.target.value)}
          />
        </Field>
        <Field label="Target" htmlFor="rp-target">
          <Input
            id="rp-target"
            inputMode="decimal"
            value={target}
            placeholder="none"
            onChange={(event) => setTarget(event.target.value)}
          />
        </Field>
      </div>
      <p className="mt-1 text-2xs text-faint">
        Protective orders are checked against the next bar&apos;s range. A gap through your stop
        fills at that bar&apos;s open, not at the stop price.
      </p>

      <div className="mt-3 flex gap-2">
        <Button
          variant="outline"
          className="flex-1 justify-center"
          disabled={disabled}
          loading={savingProtection}
          onClick={() =>
            onProtection({
              stop_loss: stop.trim() === "" ? null : stop.trim(),
              take_profit: target.trim() === "" ? null : target.trim(),
            })
          }
        >
          Update
        </Button>
        <Button
          variant="danger"
          className="flex-1 justify-center"
          icon={<XCircle className="h-3.5 w-3.5" />}
          disabled={disabled}
          loading={closing}
          onClick={onClose}
        >
          Close
        </Button>
      </div>
    </Card>
  );
}

function OrderTicket({
  state,
  disabled,
  pending,
  onSubmit,
}: {
  state: ReplayState;
  disabled: boolean;
  pending: boolean;
  onSubmit: (payload: Record<string, unknown>) => void;
}) {
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [orderType, setOrderType] = useState("market");
  const [quantity, setQuantity] = useState("1");
  const [limitPrice, setLimitPrice] = useState("");
  const [stopPrice, setStopPrice] = useState("");

  const needsLimit = orderType === "limit" || orderType === "stop_limit";
  const needsStop = orderType === "stop" || orderType === "stop_limit";
  const incomplete =
    signOf(quantity) <= 0 ||
    (needsLimit && limitPrice.trim() === "") ||
    (needsStop && stopPrice.trim() === "");

  return (
    <Card>
      <CardHeader title="Order ticket" description={state.instrument.symbol} />

      <div className="grid grid-cols-2 gap-2">
        <Button
          variant={side === "buy" ? "primary" : "outline"}
          className="justify-center"
          onClick={() => setSide("buy")}
        >
          Buy
        </Button>
        <Button
          variant={side === "sell" ? "danger" : "outline"}
          className="justify-center"
          onClick={() => setSide("sell")}
        >
          Sell
        </Button>
      </div>

      <div className="mt-3 space-y-3">
        <Field label="Quantity" htmlFor="ro-qty" required>
          <Input
            id="ro-qty"
            inputMode="decimal"
            value={quantity}
            onChange={(event) => setQuantity(event.target.value)}
          />
        </Field>
        <Field label="Type" htmlFor="ro-type">
          <Select id="ro-type" value={orderType} onChange={(event) => setOrderType(event.target.value)}>
            <option value="market">Market</option>
            <option value="limit">Limit</option>
            <option value="stop">Stop</option>
            <option value="stop_limit">Stop limit</option>
          </Select>
        </Field>
        {needsLimit ? (
          <Field label="Limit price" htmlFor="ro-limit" required>
            <Input
              id="ro-limit"
              inputMode="decimal"
              value={limitPrice}
              onChange={(event) => setLimitPrice(event.target.value)}
            />
          </Field>
        ) : null}
        {needsStop ? (
          <Field label="Stop price" htmlFor="ro-stop" required>
            <Input
              id="ro-stop"
              inputMode="decimal"
              value={stopPrice}
              onChange={(event) => setStopPrice(event.target.value)}
            />
          </Field>
        ) : null}
      </div>

      <Button
        variant="primary"
        size="lg"
        className="mt-3 w-full justify-center"
        disabled={disabled || incomplete}
        loading={pending}
        onClick={() =>
          onSubmit({
            side,
            quantity: quantity.trim(),
            order_type: orderType,
            limit_price: needsLimit ? limitPrice.trim() : null,
            stop_price: needsStop ? stopPrice.trim() : null,
          })
        }
      >
        Submit {side === "buy" ? "buy" : "sell"} order
      </Button>
      <p className="mt-2 text-2xs text-faint">
        Orders never fill on the bar that created them. Advance the replay to see the result.
      </p>
    </Card>
  );
}
