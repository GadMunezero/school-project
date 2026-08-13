"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import type {
  Backtest,
  CoverageRow,
  EngineStrategyInfo,
  Instrument,
  MarketDataSource,
  Strategy,
} from "@/lib/types";
import { Button, Field, Input, Select } from "@/components/ui/primitives";
import { Modal } from "@/components/ui/overlay";
import { useToast } from "@/components/ui/toast";
import { ParameterFields, parameterDefaults } from "@/components/strategies/parameter-fields";

/**
 * Backtest configuration.
 *
 * The parameter inputs are generated from the engine's declared schema (`/strategies/engine`),
 * including each parameter's min, max and step. The form and the server-side validation therefore
 * cannot drift: both read the same source.
 */
export function BacktestFormDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (backtest: Backtest) => void;
}) {
  const toast = useToast();

  const [name, setName] = useState("");
  const [strategyId, setStrategyId] = useState("");
  const [instrumentId, setInstrumentId] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [timeframe, setTimeframe] = useState("1d");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [capital, setCapital] = useState("100000");
  const [riskPercent, setRiskPercent] = useState("1");
  const [sizing, setSizing] = useState("percent_risk");
  const [execution, setExecution] = useState("next_bar_open");
  const [commissionRate, setCommissionRate] = useState("0.005");
  const [slippageTicks, setSlippageTicks] = useState("1");
  const [parameters, setParameters] = useState<Record<string, string>>({});

  const strategies = useQuery({
    queryKey: queryKeys.strategies(),
    queryFn: () => api.list<Strategy>("/api/v1/strategies", { page_size: 100 }),
    enabled: open,
  });
  const engine = useQuery({
    queryKey: queryKeys.engineStrategies,
    queryFn: () => api.get<EngineStrategyInfo[]>("/api/v1/strategies/engine"),
    enabled: open,
  });
  const instruments = useQuery({
    queryKey: queryKeys.instruments(),
    queryFn: () => api.list<Instrument>("/api/v1/instruments", { page_size: 200 }),
    enabled: open,
  });
  const sources = useQuery({
    queryKey: queryKeys.marketDataSources,
    queryFn: () => api.get<MarketDataSource[]>("/api/v1/market-data/sources"),
    enabled: open,
  });
  const coverage = useQuery({
    queryKey: queryKeys.coverage(instrumentId),
    queryFn: () => api.get<CoverageRow[]>(`/api/v1/market-data/coverage/${instrumentId}`),
    enabled: open && Boolean(instrumentId),
  });

  // Only strategies with executable engine logic can be backtested; journal-only ones cannot.
  const runnable = useMemo(
    () => (strategies.data?.data ?? []).filter((strategy) => strategy.kind === "builtin" && strategy.engine_key),
    [strategies.data],
  );

  const selectedStrategy = runnable.find((strategy) => strategy.id === strategyId);
  const spec = engine.data?.find((item) => item.key === selectedStrategy?.engine_key);

  // Reset parameters to the engine's declared defaults whenever the strategy changes.
  useEffect(() => {
    if (!spec) {
      setParameters({});
      return;
    }
    setParameters(parameterDefaults(spec.parameters));
  }, [spec]);

  useEffect(() => {
    if (!sourceId && sources.data?.[0]) setSourceId(sources.data[0].id);
  }, [sources.data, sourceId]);

  const availableSeries = coverage.data ?? [];
  const matching = availableSeries.find((row) => row.timeframe === timeframe);

  const create = useMutation({
    mutationFn: () =>
      api.post<Backtest>("/api/v1/backtests", {
        name: name.trim(),
        strategy_id: strategyId,
        instrument_id: instrumentId,
        market_data_source_id: sourceId || undefined,
        timeframe,
        start_date: startDate,
        end_date: endDate,
        initial_capital: capital,
        position_sizing: sizing,
        risk_percent: riskPercent,
        execution_model: execution,
        commission_config: commissionRate
          ? { model: "per_share", rate: commissionRate, minimum: "1" }
          : {},
        slippage_config: slippageTicks ? { model: "fixed_ticks", amount: slippageTicks } : {},
        parameters,
      }),
    onSuccess: (backtest) => {
      toast.success("Backtest configured.");
      onClose();
      onCreated(backtest);
    },
    onError: (error) => {
      if (error instanceof ApiError) {
        toast.error("Could not create the backtest", error.message);
        return;
      }
      toast.fromError(error);
    },
  });

  const ready = name && strategyId && instrumentId && startDate && endDate;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Configure a backtest"
      description="Every setting is stored with the run so the result stays reproducible."
      size="xl"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!ready} loading={create.isPending} onClick={() => create.mutate()}>
            Create
          </Button>
        </>
      }
    >
      {runnable.length === 0 ? (
        <p className="rounded border border-warn/30 bg-warn/5 p-3 text-sm text-muted">
          You have no strategies with executable logic. Create one on the Strategies page and choose
          a built-in engine — journal-only strategies classify trades but cannot be simulated.
        </p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name" htmlFor="bt-name" className="sm:col-span-2" required>
            <Input id="bt-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="EMA cross on NVLX, 2023" />
          </Field>

          <Field label="Strategy" htmlFor="bt-strategy" required>
            <Select id="bt-strategy" value={strategyId} onChange={(event) => setStrategyId(event.target.value)}>
              <option value="">Choose…</option>
              {runnable.map((strategy) => (
                <option key={strategy.id} value={strategy.id}>{strategy.name}</option>
              ))}
            </Select>
          </Field>

          <Field label="Instrument" htmlFor="bt-instrument" required>
            <Select id="bt-instrument" value={instrumentId} onChange={(event) => setInstrumentId(event.target.value)}>
              <option value="">Choose…</option>
              {(instruments.data?.data ?? []).map((instrument) => (
                <option key={instrument.id} value={instrument.id}>
                  {instrument.symbol} — {instrument.name ?? instrument.asset_type}
                </option>
              ))}
            </Select>
          </Field>

          <Field
            label="Timeframe"
            htmlFor="bt-timeframe"
            hint={
              instrumentId
                ? matching
                  ? `${matching.bar_count.toLocaleString()} bars available`
                  : "No candles loaded for this timeframe"
                : undefined
            }
          >
            <Select id="bt-timeframe" value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>
              {["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"].map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </Select>
          </Field>

          <Field label="Data source" htmlFor="bt-source">
            <Select id="bt-source" value={sourceId} onChange={(event) => setSourceId(event.target.value)}>
              {(sources.data ?? []).map((source) => (
                <option key={source.id} value={source.id}>
                  {source.name}{source.is_realtime ? "" : " (historical)"}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Start date" htmlFor="bt-start" required
            hint={matching?.first_bar_at ? `Data from ${matching.first_bar_at.slice(0, 10)}` : undefined}>
            <Input id="bt-start" type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
          </Field>
          <Field label="End date" htmlFor="bt-end" required
            hint={matching?.last_bar_at ? `Data to ${matching.last_bar_at.slice(0, 10)}` : undefined}>
            <Input id="bt-end" type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
          </Field>

          <Field label="Starting capital" htmlFor="bt-capital" required>
            <Input id="bt-capital" inputMode="decimal" value={capital} onChange={(event) => setCapital(event.target.value)} />
          </Field>

          <Field label="Position sizing" htmlFor="bt-sizing">
            <Select id="bt-sizing" value={sizing} onChange={(event) => setSizing(event.target.value)}>
              <option value="percent_risk">Percent risk</option>
              <option value="fixed_risk_amount">Fixed risk amount</option>
              <option value="percent_of_equity">Percent of equity</option>
              <option value="fixed_quantity">Fixed quantity</option>
              <option value="fixed_notional">Fixed notional</option>
            </Select>
          </Field>

          <Field
            label="Risk per trade (%)"
            htmlFor="bt-risk"
            hint="Risk-based sizing needs the strategy to set a stop."
          >
            <Input id="bt-risk" inputMode="decimal" value={riskPercent} onChange={(event) => setRiskPercent(event.target.value)} />
          </Field>

          <Field
            label="Execution model"
            htmlFor="bt-execution"
            hint={execution === "current_bar_close" ? "Optimistic: fills on the signal bar's close." : "Fills on the next bar's open."}
          >
            <Select id="bt-execution" value={execution} onChange={(event) => setExecution(event.target.value)}>
              <option value="next_bar_open">Next bar open</option>
              <option value="current_bar_close">Current bar close</option>
            </Select>
          </Field>

          <Field label="Commission per share" htmlFor="bt-commission">
            <Input id="bt-commission" inputMode="decimal" value={commissionRate} onChange={(event) => setCommissionRate(event.target.value)} />
          </Field>

          <Field label="Slippage (ticks)" htmlFor="bt-slippage" hint="Always applied against the trade.">
            <Input id="bt-slippage" inputMode="decimal" value={slippageTicks} onChange={(event) => setSlippageTicks(event.target.value)} />
          </Field>

          {spec ? (
            <div className="sm:col-span-2">
              <p className="mb-2 mt-2 text-xs font-semibold uppercase tracking-wide text-faint">
                {spec.name} parameters
              </p>
              <ParameterFields
                specs={spec.parameters}
                values={parameters}
                onChange={(name, value) =>
                  setParameters((current) => ({ ...current, [name]: value }))
                }
              />
            </div>
          ) : null}
        </div>
      )}
    </Modal>
  );
}
