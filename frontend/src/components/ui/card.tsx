import type { ReactNode } from "react";

import { cn } from "@/lib/utils";
import { formatMoney, formatPercent, pnlClass, type DecimalString } from "@/lib/format";

export function Card({
  children,
  className,
  padded = true,
}: {
  children: ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return (
    <section
      className={cn(
        "rounded border border-line bg-surface shadow-card",
        padded && "p-4",
        className,
      )}
    >
      {children}
    </section>
  );
}

export function CardHeader({
  title,
  description,
  action,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <header className={cn("mb-4 flex items-start justify-between gap-3", className)}>
      <div className="min-w-0">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {description ? <p className="mt-0.5 text-xs text-muted">{description}</p> : null}
      </div>
      {action}
    </header>
  );
}

export interface MetricCardProps {
  label: string;
  /** Pre-formatted display value. Formatting happens at the call site so units stay explicit. */
  value: ReactNode;
  hint?: ReactNode;
  tone?: "neutral" | "pnl";
  /** Raw decimal string, used only to pick a colour when `tone` is "pnl". */
  raw?: DecimalString;
  className?: string;
}

export function MetricCard({ label, value, hint, tone = "neutral", raw, className }: MetricCardProps) {
  return (
    <div className={cn("rounded border border-line bg-surface p-3.5", className)}>
      <p className="text-2xs font-medium uppercase tracking-wide text-faint">{label}</p>
      <p
        className={cn(
          "tnum mt-1.5 text-xl font-semibold",
          tone === "pnl" ? pnlClass(raw) : "text-ink",
        )}
      >
        {value}
      </p>
      {hint ? <p className="mt-0.5 text-xs text-muted">{hint}</p> : null}
    </div>
  );
}

/** Money + percentage pair, the shape most dashboard tiles need. */
export function PnlMetric({
  label,
  amount,
  percent,
  currency = "USD",
}: {
  label: string;
  amount: DecimalString;
  percent?: DecimalString;
  currency?: string;
}) {
  return (
    <MetricCard
      label={label}
      tone="pnl"
      raw={amount}
      value={formatMoney(amount, currency, { signed: true })}
      hint={percent !== undefined ? formatPercent(percent, { signed: true }) : undefined}
    />
  );
}

export function ChartCard({
  title,
  description,
  action,
  children,
  className,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Card className={className}>
      <CardHeader title={title} description={description} action={action} />
      {children}
    </Card>
  );
}
