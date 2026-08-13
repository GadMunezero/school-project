import type { BreakdownRow, MetricMap } from "./types";
import type { DecimalString } from "./format";

/**
 * Helpers for reading the metric payload.
 *
 * Metrics arrive as `Record<string, string | number | null | object>` because the set grows
 * without a schema migration. These narrow a value safely, preserving the difference between
 * "null — undefined" and "0" that the whole product depends on.
 */

export function metricString(value: MetricMap[string] | undefined): DecimalString {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  return null;
}

export function metricNumber(value: MetricMap[string] | undefined): number | null {
  const asString = metricString(value);
  if (asString === null || asString === undefined) return null;
  const parsed = Number(asString);
  return Number.isFinite(parsed) ? parsed : null;
}

export function breakdownRows(value: unknown): BreakdownRow[] {
  return Array.isArray(value) ? (value as BreakdownRow[]) : [];
}

export interface RBucket {
  label: string;
  lower: string | null;
  upper: string | null;
  count: number;
}

export function rBuckets(value: unknown): RBucket[] {
  return Array.isArray(value) ? (value as RBucket[]) : [];
}

export interface MonthlyReturn {
  period: string;
  start_equity: string | null;
  end_equity: string | null;
  return_percent: string | null;
  net_change: string | null;
}

export function monthlyReturns(value: unknown): MonthlyReturn[] {
  return Array.isArray(value) ? (value as MonthlyReturn[]) : [];
}

export interface ExcursionPoint {
  sequence: number;
  mfe: string | null;
  mae: string | null;
  net_pnl: string | null;
  r_multiple: string | null;
}

export function excursionPoints(value: unknown): ExcursionPoint[] {
  return Array.isArray(value) ? (value as ExcursionPoint[]) : [];
}
