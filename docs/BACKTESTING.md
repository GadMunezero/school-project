# Backtesting engine

`tradeloom.engine` is a standalone package. It imports nothing from the API, the database or the
service layer, which is what makes it fast to test and reusable by both the batch backtester and
the interactive replay session.

## Event flow

```
MarketDataEvent ─► Strategy ─► SignalEvent ─► RiskManager ─► OrderEvent
                                                          ─► BrokerSimulator ─► FillEvent
                                                          ─► Portfolio ─► PerformanceRecorder
```

A strategy never touches the broker. It emits signals; the risk manager decides whether the trade
is allowed and how big it is. That separation makes position sizing a portfolio decision instead
of something every strategy reinvents.

## The bar lifecycle — this ordering *is* the look-ahead guarantee

For each bar N the runner performs exactly these steps, in order:

1. **`broker.open_bar(bar, index)`** — orders that were already working (submitted on an earlier
   bar) are matched against bar N's OHLC. Protective stops and targets are evaluated here.
2. Position lifecycle callbacks fire for anything that changed.
3. **`strategy.on_bar(ctx)`** — the strategy sees bar N *complete* (O/H/L/C) and every earlier
   bar. It may raise signals.
4. **`strategy.risk_management(ctx)`** while a position is open (trail stops, time stops).
5. Signals drain through the risk manager into orders.
6. **`broker.close_bar(bar)`** — applies the execution model to orders submitted in step 3.
7. An equity sample is recorded.

An order is **inactive for the remainder of the bar that created it**. That single rule is what
prevents a signal from being filled by the very bar that produced it.

## Look-ahead prevention, structurally

Three independent mechanisms, none of which relies on a developer remembering a convention:

1. **Indicators are incremental.** They are fed one bar at a time and physically cannot reach a
   future value — it has not been pushed into them yet. Vectorised indicators computed over a
   whole array are the classic source of this bug, so the engine does not use them.
2. **The strategy receives a `BarWindow`**, a view truncated at the current index. Indexing past
   it raises `LookAheadError` rather than returning a future price. A look-ahead bug fails a test
   instead of inflating a result.
3. **Rolling extremes exclude the current bar** by default. Comparing a bar's close against a
   window that already contains its own high is how breakout systems end up either never
   triggering or triggering on information they could not have had.

## Execution models

| Model | Market order from a bar-N signal fills at |
| --- | --- |
| `next_bar_open` (default) | bar N+1's **open** |
| `current_bar_close` | bar N's **close** |

`next_bar_open` is the honest default: you cannot act on a close until it exists.
`current_bar_close` is available because some research workflows want it, and it is *labelled* as
the optimistic assumption it is.

## Fill rules

For a bar `(O, H, L, C)`:

| Order | Fills when | Fill price |
| --- | --- | --- |
| market | always | `O` (or `C` under `current_bar_close`) |
| buy stop @ P | `H ≥ P` | `max(P, O)` |
| sell stop @ P | `L ≤ P` | `min(P, O)` |
| buy limit @ P | `L ≤ P` | `min(P, O)` |
| sell limit @ P | `H ≥ P` | `max(P, O)` |
| stop-limit | stop triggers **and** the limit is reachable in the same bar | as limit |

**Gaps.** A bar that opens beyond a stop fills at the *open*, not at the stop — the market never
traded at the stop price, and filling there would be fiction. This is asserted by
`test_gap_through_the_stop_fills_at_the_open`.

Spread and slippage are applied on top, always adversely.

## Intrabar ambiguity

When a single bar's range spans both the stop and the target, the bar alone cannot say which came
first. `IntrabarPriority` makes the assumption explicit:

- `stop_first` (**default**) — pessimistic; a backtest never flatters itself.
- `target_first` — optimistic; use knowingly.
- `worst_case` — resolves as `stop_first` for a directional position.

## Timestamps

Four distinct timestamps are recorded for every order, which is what makes an execution
assumption auditable after the fact:

| Field | Meaning |
| --- | --- |
| `signal_timestamp` | the bar whose close produced the signal |
| `order_timestamp` | when the order entered the book |
| `fill_timestamp` | the bar on which it filled |
| `reference_price` | the fill price *before* spread and slippage |

Reading a stored run, you can confirm that a signal at 09:30 produced an order at 09:30 that
filled at 09:35 — and see exactly what the cost models did to the price.

## Determinism

Given the same bars, configuration and engine version, a run produces byte-identical results.
There is no wall-clock access, no randomness, and no dictionary-ordering dependence in the
execution path.

`input_digest` is a SHA-256 over the engine version, the strategy key and resolved parameters, the
full configuration, and a fingerprint of the bar series. Two runs with the same digest consumed
identical inputs. `engine_version` is stored alongside; results from different engine versions
are flagged as not directly comparable rather than silently compared.

Bump `ENGINE_VERSION` whenever a change can alter the numbers a run produces.

## Risk manager

Every entry answers two questions in order:

1. **Is it allowed?** — concurrency limit, pyramiding rule, cooldown bars, trading session window.
2. **How big?** — the sizing model, then clamped by max position size, buying power, and the
   per-trade risk ceiling.

Sizing models: `fixed_quantity`, `fixed_notional`, `percent_of_equity`, `fixed_risk_amount`,
`percent_risk`.

Risk-based sizing **requires a stop**. Without one there is no defensible size, so the signal is
rejected rather than silently sized by some other rule.

Rejections are never silent: each reason is counted and surfaced in the run's warnings, so a
backtest with suspiciously few trades can be explained rather than guessed at.

## Strategy safety

Strategies are selected **by key from a registry**. There is no `eval`, no `exec`, no
import-by-name and no pickle loading anywhere in the package. An unknown key is rejected before a
job is queued, and parameters are validated against the declared schema — including bounds — at
submission time, so an out-of-range value never reaches a worker.

User-authored strategy code is deliberately **not** supported. The interface is ready for a
separately sandboxed worker; the capability is absent rather than half-built.

Built-in strategies: `ema_cross`, `sma_cross`, `rsi_reversion`, `breakout`, `trend_following`.

## Replay

Replay is not an animation. Each step feeds the next real candle into the same
`BrokerSimulator`, with the same fill rules, cost models and intrabar assumptions. A stop placed
during a replay fills exactly where it would have filled in a backtest.

Two properties make it trustworthy:

- **The future is never sent to the client.** Responses contain candles up to the cursor only, so
  the browser cannot peek ahead even by reading the network tab.
- **State is server-side.** The simulator is rebuilt from persisted actions on every request, so
  a refresh or a second tab cannot desynchronise the session from its P&L.

The rebuild is O(n) in bars per step, which is acceptable for replay-length sessions and capped
at 20,000 bars. The cost is documented rather than hidden behind a cache that could drift.

## What the engine does not model

- Partial fills from insufficient liquidity — an order either fills in full or does not fill.
- Market impact.
- Borrow availability or hard-to-borrow fees for shorts.
- Dividends, splits and other corporate actions.
- Margin calls and forced liquidation.
- Multi-instrument portfolios in one run (a run is single-instrument).
