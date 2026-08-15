/**
 * API contract types.
 *
 * Mirrors the Pydantic schemas. Every monetary field is typed `DecimalString` rather than
 * `number`, which makes it a compile error to accidentally do arithmetic on money — the type
 * system enforces the rule the backend depends on.
 */

import type { DecimalString } from "./format";

export type UUID = string;

export type Direction = "long" | "short";
export type OrderSide = "buy" | "sell";
export type OrderType = "market" | "limit" | "stop" | "stop_limit";
export type TradeStatus = "open" | "partially_closed" | "closed" | "cancelled";
export type TradeSource = "manual" | "import" | "broker_sync" | "backtest" | "replay";
export type AssetType =
  | "equity"
  | "etf"
  | "futures"
  | "option"
  | "forex"
  | "crypto"
  | "cfd"
  | "index";
export type TradingSession = "asia" | "london" | "new_york_am" | "new_york_pm" | "overnight";
export type Timeframe = "1m" | "5m" | "15m" | "30m" | "1h" | "4h" | "1d" | "1w";
export type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
export type MemberRole = "viewer" | "member" | "manager" | "owner";
export type SubscriptionPlan = "free" | "pro" | "enterprise";
export type AccountType = "live" | "paper" | "demo" | "prop_evaluation" | "prop_funded" | "backtest";
export type AccountStatus = "active" | "archived" | "closed";
export type StrategyKind = "builtin" | "journal_only";
export type ImportStatus =
  | "uploaded"
  | "mapping"
  | "validating"
  | "preview"
  | "committing"
  | "completed"
  | "failed"
  | "reverted";

// --- identity ----------------------------------------------------------------

export interface UserProfile {
  id: UUID;
  email: string;
  full_name: string | null;
  display_name: string | null;
  role: "user" | "support" | "admin";
  status: string;
  email_verified: boolean;
  timezone: string;
  locale: string;
  theme: string;
  avatar_url: string | null;
  preferences: Record<string, unknown>;
  created_at: string;
  last_login_at: string | null;
}

export interface OrganizationSummary {
  id: UUID;
  name: string;
  slug: string;
  role: MemberRole;
  is_personal: boolean;
  base_currency: string;
  timezone: string;
  plan: SubscriptionPlan;
}

export interface PlanLimits {
  max_accounts: number | null;
  max_open_trades: number | null;
  max_trades: number | null;
  max_backtests_per_day: number | null;
  max_storage_bytes: number | null;
  max_members: number | null;
  replay_enabled: boolean;
  comparison_enabled: boolean;
  scheduled_reports: boolean;
  api_access: boolean;
  retention_days: number | null;
  features: string[];
}

export interface Entitlements {
  plan: SubscriptionPlan;
  status: string;
  limits: PlanLimits;
  usage: { accounts: number; trades: number };
  cancel_at_period_end: boolean;
  current_period_end: string | null;
}

export interface SessionInfo {
  user: UserProfile;
  active_organization: OrganizationSummary | null;
  organizations: OrganizationSummary[];
  entitlements: Partial<Entitlements>;
  csrf_token: string;
  expires_at: string;
}

export interface ActiveSession {
  id: UUID;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  is_current: boolean;
}

// --- accounts ----------------------------------------------------------------

export interface Account {
  id: UUID;
  name: string;
  broker: string | null;
  account_type: AccountType;
  currency: string;
  initial_balance: DecimalString;
  current_balance: DecimalString;
  realized_pnl: DecimalString;
  total_deposits: DecimalString;
  total_withdrawals: DecimalString;
  total_commission: DecimalString;
  total_fees: DecimalString;
  leverage: DecimalString;
  timezone: string;
  commission_model: string;
  commission_config: Record<string, unknown>;
  default_risk_percent: DecimalString;
  status: AccountStatus;
  is_default: boolean;
  external_reference: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
  last_recalculated_at: string | null;
}

export interface AccountStats {
  open_trade_count: number;
  closed_trade_count: number;
  open_quantity_notional: DecimalString;
  unrealized_pnl: DecimalString;
  equity: DecimalString;
  win_rate: DecimalString;
  profit_factor: DecimalString;
  net_pnl: DecimalString;
  first_trade_at: string | null;
  last_trade_at: string | null;
}

export interface AccountDetail {
  account: Account;
  stats: AccountStats;
}

export interface AccountSnapshot {
  as_of_date: string;
  opening_balance: DecimalString;
  closing_balance: DecimalString;
  equity: DecimalString;
  realized_pnl: DecimalString;
  unrealized_pnl: DecimalString;
  commission: DecimalString;
  fees: DecimalString;
  net_cash_flow: DecimalString;
  trade_count: number;
  peak_equity: DecimalString;
  drawdown: DecimalString;
  drawdown_percent: DecimalString;
}

export interface CashTransaction {
  id: UUID;
  account_id: UUID;
  kind: string;
  amount: DecimalString;
  currency: string;
  occurred_at: string;
  description: string | null;
  created_at: string;
}

// --- trading -----------------------------------------------------------------

export interface TagRef {
  id: UUID;
  name: string;
  slug: string;
  color: string | null;
  category: string;
}

export interface Trade {
  id: UUID;
  account_id: UUID;
  instrument_id: UUID | null;
  symbol: string;
  asset_type: AssetType;
  currency: string;
  contract_multiplier: DecimalString;
  direction: Direction;
  status: TradeStatus;
  source: TradeSource;
  entry_timestamp: string;
  exit_timestamp: string | null;
  entry_price: DecimalString;
  exit_price: DecimalString;
  quantity: DecimalString;
  closed_quantity: DecimalString;
  remaining_quantity: DecimalString;
  stop_loss: DecimalString;
  initial_stop_loss: DecimalString;
  take_profit: DecimalString;
  commission: DecimalString;
  fees: DecimalString;
  slippage: DecimalString;
  gross_pnl: DecimalString;
  net_pnl: DecimalString;
  risk_amount: DecimalString;
  r_multiple: DecimalString;
  return_percentage: DecimalString;
  holding_seconds: number | null;
  mfe_price: DecimalString;
  mae_price: DecimalString;
  mfe_amount: DecimalString;
  mae_amount: DecimalString;
  strategy_id: UUID | null;
  setup_id: UUID | null;
  session: TradingSession | null;
  notes: string | null;
  rating: number | null;
  custom_metadata: Record<string, unknown>;
  external_id: string | null;
  import_id: UUID | null;
  created_at: string;
  updated_at: string;
  tags: TagRef[];
  account_name: string | null;
  strategy_name: string | null;
  setup_name: string | null;
}

export interface Order {
  id: UUID;
  account_id: UUID;
  trade_id: UUID | null;
  symbol: string;
  side: OrderSide;
  order_type: OrderType;
  time_in_force: string;
  status: string;
  quantity: DecimalString;
  filled_quantity: DecimalString;
  limit_price: DecimalString;
  stop_price: DecimalString;
  average_fill_price: DecimalString;
  commission: DecimalString;
  fees: DecimalString;
  placed_at: string;
  filled_at: string | null;
  is_entry: boolean | null;
  external_id: string | null;
  notes: string | null;
}

export interface Position {
  id: UUID;
  account_id: UUID;
  instrument_id: UUID | null;
  trade_id: UUID | null;
  symbol: string;
  direction: Direction;
  status: "open" | "closed";
  quantity: DecimalString;
  average_price: DecimalString;
  contract_multiplier: DecimalString;
  realized_pnl: DecimalString;
  last_price: DecimalString;
  unrealized_pnl: DecimalString;
  marked_at: string | null;
  opened_at: string;
  closed_at: string | null;
}

export interface Screenshot {
  id: UUID;
  file_object_id: UUID;
  caption: string | null;
  phase: string;
  timeframe: string | null;
  display_order: number;
  content_type: string;
  size_bytes: number;
  original_filename: string | null;
  created_at: string;
  url: string | null;
}

export interface TradeDetail {
  trade: Trade;
  orders: Order[];
  screenshots: Screenshot[];
  planned_reward_risk: DecimalString;
  efficiency: DecimalString;
}

// --- catalogue ---------------------------------------------------------------

export interface Instrument {
  id: UUID;
  organization_id: UUID | null;
  symbol: string;
  name: string | null;
  asset_type: AssetType;
  exchange: string | null;
  currency: string;
  tick_size: DecimalString;
  contract_multiplier: DecimalString;
  lot_size: DecimalString;
  price_precision: number;
  is_active: boolean;
  expires_on: string | null;
  is_global: boolean;
}

export interface Tag extends TagRef {
  description: string | null;
  created_at: string;
  trade_count: number;
}

export interface Setup {
  id: UUID;
  name: string;
  description: string | null;
  strategy_id: UUID | null;
  color: string | null;
  checklist: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
  trade_count: number;
}

export interface ParameterSpec {
  name: string;
  label: string | null;
  param_type: "integer" | "decimal" | "boolean" | "string" | "choice";
  default_value: string | null;
  minimum: DecimalString;
  maximum: DecimalString;
  step: DecimalString;
  choices: string[];
  description: string | null;
  display_order: number;
}

export interface EngineStrategyInfo {
  key: string;
  name: string;
  description: string;
  category: string;
  parameters: ParameterSpec[];
}

export interface Strategy {
  id: UUID;
  name: string;
  description: string | null;
  kind: StrategyKind;
  engine_key: string | null;
  status: string;
  color: string | null;
  playbook: Record<string, unknown>;
  current_version_id: UUID | null;
  created_at: string;
  updated_at: string;
  trade_count: number;
  net_pnl: DecimalString;
  win_rate: DecimalString;
}

export interface StrategyVersion {
  id: UUID;
  strategy_id: UUID;
  version: number;
  engine_key: string | null;
  parameters: Record<string, unknown>;
  notes: string | null;
  is_published: boolean;
  created_at: string;
}

export interface StrategyDetail {
  strategy: Strategy;
  versions: StrategyVersion[];
  parameter_specs: ParameterSpec[];
}

// --- analytics ---------------------------------------------------------------

/** Metric values arrive as decimal strings or null. `null` means undefined, never zero. */
export type MetricMap = Record<string, string | number | null | Record<string, unknown>>;

export interface BreakdownRow {
  label: string;
  trades: number;
  net_pnl: DecimalString;
  win_rate: DecimalString;
  average_trade: DecimalString;
  profit_factor: DecimalString;
  average_r?: DecimalString;
}

export interface EquityPoint {
  timestamp: string;
  equity: DecimalString;
  realized_pnl?: DecimalString;
}

export interface DrawdownPoint {
  timestamp: string;
  drawdown: DecimalString;
  drawdown_percent: DecimalString;
  peak: DecimalString;
}

export interface CalendarDay {
  date: string;
  net_pnl: DecimalString;
  trades: number;
  wins: number;
}

export interface AnalyticsResult {
  metrics: MetricMap;
  breakdowns: Record<string, BreakdownRow[] | unknown>;
  equity_curve: EquityPoint[];
  drawdown_curve: DrawdownPoint[];
  trade_count: number;
  truncated: boolean;
  generated_at: string;
}

export interface DashboardResult extends AnalyticsResult {
  open_positions: {
    count: number;
    symbols: string[];
    trades: {
      id: UUID;
      symbol: string;
      direction: Direction;
      quantity: DecimalString;
      entry_price: DecimalString;
      entry_timestamp: string;
    }[];
  };
  recent_trades: {
    id: UUID;
    symbol: string;
    direction: Direction;
    net_pnl: DecimalString;
    r_multiple: DecimalString;
    exit_timestamp: string | null;
  }[];
  window_days: number;
}

// --- imports -----------------------------------------------------------------

export interface ImportColumn {
  name: string;
  index: number;
  samples: string[];
  detected_field: string | null;
  confidence: number;
  non_empty_count: number;
}

export interface ImportInspection {
  delimiter: string;
  encoding: string;
  headers: string[];
  total_rows: number;
  preview: Record<string, string>[];
  suggested_mapping: Record<string, string>;
  detected_template: string | null;
  columns: ImportColumn[];
}

export interface ImportRecord {
  id: UUID;
  account_id: UUID;
  status: ImportStatus;
  filename: string;
  row_kind: string;
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  duplicate_rows: number;
  imported_rows: number;
  created_order_count: number;
  created_trade_count: number;
  column_mapping: Record<string, string>;
  options: Record<string, unknown>;
  inspection: ImportInspection;
  error_summary: Record<string, unknown>;
  committed_at: string | null;
  reverted_at: string | null;
  created_at: string;
  can_revert: boolean;
}

export interface ImportRowPreview {
  row_number: number;
  status: string;
  raw: Record<string, string>;
  normalized: Record<string, string | null>;
  errors: { field: string; code: string; message: string }[];
  warnings: string[];
}

export interface ImportPreview {
  import_id: UUID;
  status: ImportStatus;
  totals: { total: number; valid: number; invalid: number; duplicate: number };
  rows: ImportRowPreview[];
  invalid_rows: ImportRowPreview[];
}

export interface ImportTemplate {
  id: UUID;
  key: string;
  name: string;
  broker: string | null;
  description: string | null;
  column_mapping: Record<string, string>;
  options: Record<string, unknown>;
  is_system: boolean;
}

// --- market data -------------------------------------------------------------

export interface MarketDataSource {
  id: UUID;
  key: string;
  name: string;
  description: string | null;
  provider_type: string;
  /** Only ever true for a provider that genuinely streams live prices. */
  is_realtime: boolean;
  last_synced_at: string | null;
}

export interface Candle {
  time: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
}

export interface CandleResponse {
  instrument: { id: UUID; symbol: string; price_precision: number; tick_size: string };
  source: { id: UUID; key: string; name: string; is_realtime: boolean };
  timeframe: Timeframe;
  candles: Candle[];
  truncated: boolean;
}

export interface CoverageRow {
  source_id: UUID;
  timeframe: Timeframe;
  bar_count: number;
  first_bar_at: string | null;
  last_bar_at: string | null;
  quality: Record<string, unknown>;
}

/** What the importer found in an uploaded file, before any of it is stored. */
export interface AdminInvite {
  id: UUID;
  code: string;
  note: string | null;
  max_uses: number;
  used_count: number;
  uses_left: number;
  /** active | used | expired | revoked — revoked outranks the rest. */
  state: "active" | "used" | "expired" | "revoked";
  expires_at: string | null;
  revoked_at: string | null;
  created_at: string;
  redeemed_by: string[];
}

export interface FeedbackReport {
  id: UUID;
  kind: "bug" | "idea" | "question" | "other";
  message: string;
  page: string | null;
  /** Whatever the client attached. Untrusted: rendered as data, never interpreted. */
  context: Record<string, string>;
  status: "new" | "reviewed" | "closed";
  reporter_email: string | null;
  organization_id: UUID | null;
  created_at: string;
}

export interface CandleInspection {
  headers: string[];
  delimiter: string;
  total_rows: number;
  preview: Record<string, string>[];
  /** Only exact header matches; anything unmapped is the user's to choose. */
  suggested_mapping: Partial<Record<CandleField, string>>;
}

export type CandleField = "timestamp" | "open" | "high" | "low" | "close" | "volume";

export interface RejectedCandleRow {
  row_number: number;
  reason: string;
  raw: Record<string, string>;
}

export interface CandleImportResult {
  total_rows: number;
  accepted: number;
  rejected: number;
  timeframe: Timeframe;
  first_bar_at: string | null;
  last_bar_at: string | null;
  rejected_rows: RejectedCandleRow[];
  dry_run: boolean;
  instrument: { id: UUID; symbol: string };
  /** Bars newly written. A re-import of an overlapping export writes none. */
  stored: number;
  already_stored?: number;
  source?: { id: UUID; name: string };
  quality?: Record<string, unknown>;
}

// --- backtesting -------------------------------------------------------------

export interface Backtest {
  id: UUID;
  name: string;
  description: string | null;
  strategy_id: UUID;
  strategy_version_id: UUID;
  instrument_id: UUID;
  market_data_source_id: UUID;
  timeframe: Timeframe;
  start_date: string;
  end_date: string;
  initial_capital: DecimalString;
  currency: string;
  leverage: DecimalString;
  position_sizing: string;
  risk_percent: DecimalString;
  max_concurrent_positions: number;
  allow_pyramiding: boolean;
  cooldown_bars: number;
  execution_model: string;
  commission_config: Record<string, unknown>;
  slippage_config: Record<string, unknown>;
  spread_config: Record<string, unknown>;
  session_config: Record<string, unknown>;
  parameters: Record<string, unknown>;
  last_run_id: UUID | null;
  created_at: string;
  updated_at: string;
}

export interface BacktestRun {
  id: UUID;
  backtest_id: UUID;
  mode: "backtest" | "replay";
  status: JobStatus;
  progress_percent: number;
  engine_version: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  bars_processed: number;
  trade_count: number;
  final_equity: DecimalString;
  total_return_percent: DecimalString;
  max_drawdown_percent: DecimalString;
  profit_factor: DecimalString;
  input_digest: string | null;
  config_snapshot: Record<string, unknown>;
  data_snapshot: Record<string, unknown>;
  warnings: { items?: string[] };
  error: { code?: string; message?: string };
  created_at: string;
  job_id: UUID | null;
}

export interface BacktestTrade {
  sequence: number;
  symbol: string;
  direction: Direction;
  entry_timestamp: string;
  exit_timestamp: string | null;
  entry_price: DecimalString;
  exit_price: DecimalString;
  quantity: DecimalString;
  stop_loss: DecimalString;
  take_profit: DecimalString;
  commission: DecimalString;
  slippage: DecimalString;
  gross_pnl: DecimalString;
  net_pnl: DecimalString;
  risk_amount: DecimalString;
  r_multiple: DecimalString;
  return_percentage: DecimalString;
  holding_seconds: number | null;
  mfe_amount: DecimalString;
  mae_amount: DecimalString;
  exit_reason: string | null;
  equity_after: DecimalString;
}

export interface BacktestEquityPoint {
  timestamp: string;
  equity: DecimalString;
  cash: DecimalString;
  realized_pnl: DecimalString;
  unrealized_pnl: DecimalString;
  open_positions: number;
  exposure: DecimalString;
}

export interface BacktestDrawdown {
  started_at: string;
  trough_at: string;
  recovered_at: string | null;
  peak_equity: DecimalString;
  trough_equity: DecimalString;
  depth: DecimalString;
  depth_percent: DecimalString;
  duration_seconds: number;
  recovery_seconds: number | null;
}

export interface BacktestResult {
  run: BacktestRun;
  metrics: MetricMap;
  breakdowns: Record<string, unknown>;
  equity_curve: BacktestEquityPoint[];
  drawdowns: BacktestDrawdown[];
  trades: BacktestTrade[];
}

export interface JobRecord {
  id: UUID;
  kind: string;
  status: JobStatus;
  queue: string;
  progress_percent: number;
  progress_message: string | null;
  attempts: number;
  max_attempts: number;
  queued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  error_code: string | null;
  error_message: string | null;
  result: Record<string, unknown>;
}

export interface RunSubmission {
  run_id: UUID;
  job_id: UUID;
  status: JobStatus;
  task_id: string | null;
  queued: boolean;
}

// --- replay ------------------------------------------------------------------

export interface ReplayState {
  id: UUID;
  name: string;
  timeframe: Timeframe;
  cursor_index: number;
  total_bars: number;
  is_finished: boolean;
  currency: string;
  initial_capital: DecimalString;
  equity: DecimalString;
  cash: DecimalString;
  realized_pnl: DecimalString;
  unrealized_pnl: DecimalString;
  position: {
    direction: Direction;
    quantity: DecimalString;
    average_price: DecimalString;
    stop_loss: DecimalString;
    take_profit: DecimalString;
    unrealized_pnl: DecimalString;
    opened_at: string;
  } | null;
  working_orders: {
    id: number;
    side: OrderSide;
    order_type: OrderType;
    quantity: DecimalString;
    limit_price: DecimalString;
    stop_price: DecimalString;
    intent: string;
    status: string;
  }[];
  closed_trades: {
    sequence: number;
    direction: Direction;
    entry_timestamp: string;
    exit_timestamp: string | null;
    entry_price: DecimalString;
    exit_price: DecimalString;
    quantity: DecimalString;
    net_pnl: DecimalString;
    r_multiple: DecimalString;
    exit_reason: string | null;
  }[];
  equity_curve: { time: string; equity: DecimalString }[];
  /** Only bars up to the cursor — the server never sends the future. */
  visible_candles: Candle[];
  current_bar: { time: string; open: string; high: string; low: string; close: string } | null;
  instrument: { id: UUID; symbol: string };
}

export interface ReplaySummary {
  id: UUID;
  name: string;
  timeframe: Timeframe;
  cursor_index: number;
  total_bars: number;
  is_finished: boolean;
  created_at: string;
  last_interacted_at: string | null;
}

// --- edge reports ------------------------------------------------------------

export type ReportOutcome =
  | "broke_up_only"
  | "broke_down_only"
  | "broke_both"
  | "stayed_inside"
  | "filled"
  | "unfilled"
  | "no_setup";

export interface ReportSpec {
  key: string;
  name: string;
  question: string;
  description: string;
  parameters: ParameterSpec[];
}

export interface ReportLevel {
  key: string;
  label: string;
  /** A price, as a decimal string like every other price the API sends. */
  price: DecimalString;
}

export interface ReportSession {
  session_date: string;
  outcome: ReportOutcome;
  levels: ReportLevel[];
  triggered_at: string | null;
  /** The span to plot when this day is opened for verification. */
  window_start: string;
  window_end: string;
  measures: Record<string, DecimalString>;
}

export interface ConditionValue {
  value: string;
  label: string;
  /** Null when no session in this slice qualified — an undefined rate, never zero. */
  hit_rate: DecimalString;
  sample_size: number;
  session_dates: string[];
}

export interface ReportCondition {
  key: string;
  name: string;
  values: ConditionValue[];
}

export interface ReportRun {
  key: string;
  name: string;
  question: string;
  headline_outcomes: ReportOutcome[];
  /** Null when no session qualified — an undefined rate, never zero. */
  hit_rate: DecimalString;
  sample_size: number;
  total_sessions: number;
  buckets: Record<string, number>;
  sessions: ReportSession[];
  instrument: { id: UUID; symbol: string; name: string };
  timeframe: Timeframe;
  session_timezone: string;
  /**
   * The market's own trading day, when it has one. Futures and FX open in the New York evening
   * and run through the next afternoon, so their sessions are cut by this rather than by the
   * requested timezone — which the UI must say out loud rather than leave a control that does
   * nothing.
   */
  session_boundary: { opens_at: string; timezone: string } | null;
  source: { id: UUID; name: string };
  /** False when the sample is too small to lean on; the UI says so rather than hiding it. */
  sufficient_sample: boolean;
  minimum_sample: number;
  /** The same rate split by what was knowable before each session opened. */
  conditions: ReportCondition[];
}

// --- platform ----------------------------------------------------------------

export interface Notification {
  id: UUID;
  kind: string;
  severity: "info" | "success" | "warning" | "error";
  title: string;
  body: string | null;
  link: string | null;
  data: Record<string, unknown>;
  read_at: string | null;
  created_at: string;
}

export interface SearchHit {
  type: string;
  id: UUID;
  title: string;
  subtitle: string | null;
  href: string;
}

export interface JournalEntry {
  id: UUID;
  entry_date: string;
  title: string | null;
  body: string;
  entry_type: string;
  mood: string | null;
  discipline_rating: number | null;
  lessons: Record<string, unknown>;
  trade_id: UUID | null;
  account_id: UUID | null;
  pinned_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PlanOption {
  plan: SubscriptionPlan;
  limits: PlanLimits;
  purchasable: boolean;
}

export interface OrganizationMember {
  id: UUID;
  user_id: UUID;
  email: string;
  full_name: string | null;
  role: MemberRole;
  status: string;
  joined_at: string | null;
  created_at: string;
}

export interface Organization {
  id: UUID;
  name: string;
  slug: string;
  is_personal: boolean;
  base_currency: string;
  timezone: string;
  settings: Record<string, unknown>;
  owner_user_id: UUID;
  created_at: string;
  member_count: number;
  your_role: MemberRole | null;
}

export interface PlanOffer {
  plan: SubscriptionPlan;
  limits: PlanLimits;
  /** False when Stripe is not configured — the UI disables the button rather than 500ing. */
  purchasable: boolean;
}

export interface SubscriptionSnapshot extends Entitlements {
  billing_enabled: boolean;
}

export interface AdminUserRow {
  id: UUID;
  email: string;
  full_name: string | null;
  role: string;
  status: string;
  email_verified: boolean;
  created_at: string;
  last_login_at: string | null;
  deletion_requested_at: string | null;
}

export interface AdminOrganizationRow {
  id: UUID;
  name: string;
  slug: string;
  is_personal: boolean;
  created_at: string;
  member_count: number;
  trade_count: number;
  plan: SubscriptionPlan;
  subscription_status: string | null;
}

export interface AdminJobRow extends JobRecord {
  /** Admin-only: the internal detail that ordinary users never see. */
  error_detail: Record<string, unknown> | null;
}

export interface AuditLogEntry {
  id: UUID;
  created_at: string;
  organization_id: UUID | null;
  actor_email: string | null;
  action: string;
  entity_type: string | null;
  entity_id: UUID | null;
  summary: string | null;
  changes: Record<string, unknown>;
  ip_address: string | null;
  request_id: string | null;
}

export interface AdminOverview {
  users: {
    total: number;
    active: number;
    pending: number;
    suspended: number;
    deletion_requested: number;
  };
  organizations: number;
  trades: number;
  jobs: Record<string, number>;
  failed_jobs_by_kind: Record<string, number>;
  failed_imports: number;
  failed_logins_24h: number;
  generated_at: string;
}
