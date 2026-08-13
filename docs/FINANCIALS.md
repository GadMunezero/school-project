# Financial definitions

Every number the product shows is defined here. Where a quantity is undefined, Tradeloom returns
`null` rather than `0` — a profit factor with no losing trades is *undefined*, and reporting zero
would corrupt every aggregate built on top of it.

## 1. Numeric conventions

| Rule | Value |
| --- | --- |
| Type | `decimal.Decimal` everywhere; never `float` for money |
| Working precision | 34 significant digits |
| Rounding | `ROUND_HALF_EVEN` (banker's rounding), the only mode used anywhere |
| Stored money / price / quantity | `NUMERIC(28, 10)` |
| Stored percent / ratio | `NUMERIC(18, 8)` |
| Settlement rounding | the currency's minor unit — 2 for USD/EUR, 0 for JPY, 8 for BTC |
| JSON transport | Decimals serialise as **strings**, so precision survives the wire |

Floats appear in exactly one place: statistical ratios (Sharpe, Sortino) at the point of taking a
square root. The inputs are already returns, and the result converts straight back to `Decimal`.

`safe_div` returns `None` on a zero denominator. Callers must decide what an undefined ratio
means; nothing silently becomes zero.

## 2. From fills to trades

An **order** is an executed fill: side, quantity, price, timestamp, commission, fees. Orders are
the atomic facts; everything else is derived.

Fills for one `(account, instrument)` are processed in timestamp order (ties broken by a sequence
number, so same-millisecond fills are deterministic):

- **No open trade** → the fill opens one. Long for a buy, short for a sell.
- **Same direction** → **scale in**. The average entry price becomes the quantity-weighted
  average of the existing basis and the new fill.
- **Opposite direction** → **scale out**. Realised P&L is booked for the closed quantity. *The
  average entry price does not change* — the surviving quantity keeps its original basis.
- **Opposite fill larger than the open quantity** → **flip**: the trade closes at exactly the
  remaining size, and the surplus opens a new trade in the other direction. The fill's commission
  and fees are split between the two trades in proportion to quantity.

A trade is `closed` when remaining quantity reaches zero; `exit_timestamp` is the timestamp of the
final closing fill.

### Cost basis: weighted average, not FIFO

The two methods differ only for partially-closed positions. Average cost matches the "average
price" a broker platform shows while a trade is being managed, which is the number a trader is
actually looking at. Tax-lot accounting is a reporting concern, not a journalling one.

## 3. Per-trade values

Let `d = +1` for long, `-1` for short, and `m` = contract multiplier.

| Value | Definition |
| --- | --- |
| Realised P&L on an exit | `(exit_price − avg_entry_price) × qty × m × d` |
| `gross_pnl` | sum of realised P&L across all exits |
| `net_pnl` | `gross_pnl − commission − fees` |
| `cost_basis` | `avg_entry_price × total_quantity × m` |
| `return_percentage` | `net_pnl / |cost_basis| × 100` (null when basis is 0) |
| `holding_seconds` | `exit_timestamp − entry_timestamp` (null while open) |
| `unrealized_pnl` | `(mark − avg_entry_price) × open_qty × m × d`; **null without a mark price** |

Slippage is recorded for analysis only — it is already reflected in the fill prices, so
subtracting it again would double-count.

### Risk and R

```
risk_per_unit = (entry_price − stop_loss) × d          # null if ≤ 0
risk_amount   = risk_per_unit × quantity × m
r_multiple    = net_pnl / risk_amount                  # null when risk is unknown
```

R is measured against the **initial** stop (`initial_stop_loss`), not a trailed one — otherwise
moving a stop would retroactively rewrite the risk you actually took.

A stop on the wrong side of entry (a long with the stop above entry) yields `null`, not a negative
risk: it does not describe risk at all.

A trade with no stop and no declared risk amount has **no R**. It is excluded from R averages
rather than counted as 0R.

### Excursions

MFE and MAE are computed from candles covering the holding period, relative to the average entry
price, and are **magnitudes** (never negative):

```
MFE = max(0, (best_price  − entry) × d) × qty × m
MAE = max(0, (entry − worst_price)  × d) × qty × m
```

With no covering candles both are `null` — unknown, not zero. `efficiency = net_pnl / MFE` says
how much of the available favourable move the exit captured.

## 4. Account balances

```
current_balance = initial_balance
                + Σ cash_transactions.amount        (deposits, withdrawals, fees, adjustments)
                + Σ net_pnl of CLOSED trades
```

`current_balance` is a **cache** of that expression, recomputed by `AccountService.recalculate`
whenever an input changes. It is never incremented in place, so a double-applied event cannot
drift the balance permanently.

Cash transaction signs are derived from `kind` server-side — a client cannot turn a withdrawal
into a deposit by sending a negative number.

`equity = current_balance + unrealised P&L` when marks exist, otherwise `current_balance`.

## 5. Performance metrics

Computed by `PerformanceAnalyzer`, shared by the backtester and the journal.

### Returns and profit

| Metric | Definition |
| --- | --- |
| Net profit | `Σ net_pnl` |
| Total return % | `(final_equity − initial_capital) / initial_capital × 100` |
| Gross profit | `Σ net_pnl` over winners |
| Gross loss | `Σ net_pnl` over losers (negative) |
| Average trade | `net_profit / trade_count` |
| Expectancy | equals average trade — `P(win)·avg_win + P(loss)·avg_loss` reduces to it |
| Profit factor | `gross_profit / |gross_loss|`; **null** when there are no losses |
| Payoff ratio | `avg_win / |avg_loss|`; null when either side is empty |

### Rates

Win rate is `winners / total × 100` where a winner is `net_pnl > 0`. Breakeven trades
(`net_pnl == 0`) are counted separately and are neither wins nor losses, so win rate + loss rate
does not always equal 100%.

### Drawdown

An episode opens when equity falls below the running peak and closes when equity regains that
peak.

```
depth          = peak_equity − trough_equity
depth_percent  = depth / peak_equity × 100
duration       = recovered_at (or trough_at, if never recovered) − peak_at
recovery       = recovered_at − trough_at        # null if still under water
```

An episode still open at the end has `recovered_at = null`, which is meaningfully different from
"recovered on the last bar".

### Risk-adjusted

Per-period returns come from the equity curve: `r_t = (E_t − E_{t−1}) / E_{t−1}`. Periods with
non-positive starting equity are skipped rather than treated as −100%.

```
Sharpe  = (mean(r) − rf_period) / stdev(r, sample) × √periods_per_year
Sortino = (mean(r) − rf_period) / downside_deviation × √periods_per_year
```

Downside deviation is the root-mean-square of `min(0, r_t − MAR)` over **all** periods — the
standard definition, not the standard deviation of negative returns only.

Both are `null` with fewer than two periods, or when the denominator is zero.

```
CAGR   = ((final_equity / initial_capital) ^ (1 / years) − 1) × 100
Calmar = CAGR / |max_drawdown_percent|
```

CAGR is `null` for runs shorter than a day: annualising a three-hour backtest produces a number
that looks authoritative and means nothing.

`periods_per_year` is an explicit assumption (252 trading days; ~6.5-hour session for intraday
timeframes) stored with each run so a result can be recomputed under a different one.

### Other

| Metric | Definition |
| --- | --- |
| Exposure % | `bars_with_a_position / total_bars × 100` |
| Max consecutive wins/losses | longest run of `net_pnl > 0` / `< 0`; a breakeven trade resets both |
| Average R / median R | over trades that **have** an R only; `trades_with_r` reports how many |
| R distribution | buckets `[−∞,−3) … [3,+∞)` |

## 6. Costs

**Commission** is charged on *every fill*, entries and exits alike, which is how brokers bill.

| Model | Charge |
| --- | --- |
| `per_share` / `per_contract` | `rate × quantity` |
| `per_trade` | flat `rate` |
| `percent_of_notional` | `rate% × price × quantity × multiplier` |

clamped by `minimum` and `maximum`.

**Slippage** always works against the trade — buys fill higher, sells fill lower. There is no
setting that makes it favourable, because a cost model that sometimes helps you is a model that
flatters your backtest.

**Spread**: candles are mid/last prices; a real buy pays the ask and a real sell receives the bid,
so half the spread is applied to each side.

## 7. Capital model in simulation

Backtests simulate a **margin account**:

```
equity       = initial_capital + realised P&L + unrealised P&L
exposure     = |quantity| × price × multiplier
buying power = equity × leverage − exposure
```

Cash is not decremented by the full notional on entry, because a leveraged account does not pay
the full notional. Consequently "ran out of money" means *exceeded buying power*, not
*notional exceeded cash*.

Position sizes are floored to a tradable increment. Rounding **down** never over-risks the
account.
