"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, CreditCard, ExternalLink, Minus } from "lucide-react";
import { useState } from "react";

import { api } from "@/lib/api";
import { formatBytes, formatDate, formatInteger, humanise } from "@/lib/format";
import { queryKeys } from "@/lib/queries";
import { useSession } from "@/lib/session";
import type { PlanOffer, SubscriptionSnapshot } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Card, CardHeader } from "@/components/ui/card";
import { ErrorState, Skeleton } from "@/components/ui/feedback";
import { Badge, Button, Select } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/shell/page-header";

const PLAN_ORDER = ["free", "pro", "enterprise"];

const PLAN_BLURB: Record<string, string> = {
  free: "Everything you need to keep an honest journal of one account.",
  pro: "Multiple accounts, the backtester, market replay and run comparison.",
  enterprise: "Shared workspaces, longer retention and API access.",
};

/** Limit keys rendered as a comparison table, in the order a reader would ask about them. */
const LIMIT_ROWS: { key: string; label: string; kind: "count" | "bytes" | "days" | "boolean" }[] = [
  { key: "max_accounts", label: "Trading accounts", kind: "count" },
  { key: "max_trades", label: "Trades stored", kind: "count" },
  { key: "max_open_trades", label: "Open trades at once", kind: "count" },
  { key: "max_backtests_per_day", label: "Backtests per day", kind: "count" },
  { key: "max_members", label: "Workspace members", kind: "count" },
  { key: "max_storage_bytes", label: "Attachment storage", kind: "bytes" },
  { key: "retention_days", label: "History retained", kind: "days" },
  { key: "replay_enabled", label: "Market replay", kind: "boolean" },
  { key: "comparison_enabled", label: "Compare backtest runs", kind: "boolean" },
  { key: "scheduled_reports", label: "Scheduled reports", kind: "boolean" },
  { key: "api_access", label: "API access", kind: "boolean" },
];

export default function BillingPage() {
  const toast = useToast();
  const { session } = useSession();
  const [interval, setInterval] = useState("monthly");

  const role = session?.active_organization?.role;
  const canManage = role === "owner" || role === "manager";

  const subscription = useQuery({
    queryKey: queryKeys.subscription,
    queryFn: () => api.get<SubscriptionSnapshot>("/api/v1/billing/subscription"),
  });

  const plans = useQuery({
    queryKey: queryKeys.billingPlans,
    queryFn: () => api.get<PlanOffer[]>("/api/v1/billing/plans"),
  });

  const checkout = useMutation({
    mutationFn: (plan: string) =>
      api.post<{ checkout_url: string }>("/api/v1/billing/checkout", { plan, interval }),
    // Stripe owns the payment flow; we hand the browser over rather than collecting card details.
    onSuccess: (data) => {
      window.location.href = data.checkout_url;
    },
    onError: (error) => toast.fromError(error, "Could not start checkout"),
  });

  const portal = useMutation({
    mutationFn: () => api.post<{ portal_url: string }>("/api/v1/billing/portal"),
    onSuccess: (data) => {
      window.location.href = data.portal_url;
    },
    onError: (error) => toast.fromError(error, "Could not open the billing portal"),
  });

  if (subscription.isError) {
    return (
      <>
        <PageHeader title="Billing" />
        <ErrorState error={subscription.error} onRetry={() => void subscription.refetch()} />
      </>
    );
  }

  if (subscription.isLoading || !subscription.data) {
    return (
      <>
        <PageHeader title="Billing" />
        <Skeleton className="h-72 rounded" />
      </>
    );
  }

  const current = subscription.data;
  const offers = [...(plans.data ?? [])].sort(
    (a, b) => PLAN_ORDER.indexOf(a.plan) - PLAN_ORDER.indexOf(b.plan),
  );

  return (
    <>
      <PageHeader
        title="Billing"
        description="Your plan decides limits, not correctness. Every figure in the product is computed the same way on every plan."
        action={
          current.billing_enabled && canManage ? (
            <Button
              variant="outline"
              icon={<ExternalLink className="h-3.5 w-3.5" />}
              loading={portal.isPending}
              onClick={() => portal.mutate()}
            >
              Manage payment details
            </Button>
          ) : null
        }
      />

      <Card className="mb-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <span className="flex items-center gap-2">
              <h2 className="text-sm font-semibold text-ink">
                {humanise(current.plan)} plan
              </h2>
              <Badge tone={current.status === "active" ? "profit" : "warn"}>
                {humanise(current.status)}
              </Badge>
            </span>
            <p className="mt-1 text-xs text-muted">
              {current.cancel_at_period_end
                ? `Cancels on ${formatDate(current.current_period_end)}. You keep every feature until then.`
                : current.current_period_end
                  ? `Renews on ${formatDate(current.current_period_end)}.`
                  : "No renewal date — this plan does not expire."}
            </p>
          </div>

          <dl className="flex gap-6">
            <div>
              <dt className="text-2xs uppercase tracking-wide text-faint">Accounts</dt>
              <dd className="tnum text-sm font-semibold text-ink">
                {formatInteger(current.usage.accounts)}
                {current.limits.max_accounts === null
                  ? " / unlimited"
                  : ` / ${formatInteger(current.limits.max_accounts)}`}
              </dd>
            </div>
            <div>
              <dt className="text-2xs uppercase tracking-wide text-faint">Trades</dt>
              <dd className="tnum text-sm font-semibold text-ink">
                {formatInteger(current.usage.trades)}
                {current.limits.max_trades === null
                  ? " / unlimited"
                  : ` / ${formatInteger(current.limits.max_trades)}`}
              </dd>
            </div>
          </dl>
        </div>

        {!current.billing_enabled ? (
          <p className="mt-3 rounded border border-line bg-raised p-3 text-xs text-muted">
            Payments are not configured on this deployment, so plans cannot be purchased here. An
            administrator can change a workspace&apos;s plan directly.
          </p>
        ) : null}
      </Card>

      {plans.isLoading ? (
        <Skeleton className="h-96 rounded" />
      ) : (
        <>
          <div className="mb-3 flex items-center justify-end gap-2">
            <label htmlFor="b-interval" className="text-xs text-muted">
              Billed
            </label>
            <Select
              id="b-interval"
              className="w-32"
              value={interval}
              onChange={(event) => setInterval(event.target.value)}
            >
              <option value="monthly">Monthly</option>
              <option value="yearly">Yearly</option>
            </Select>
          </div>

          <div className="grid gap-3 lg:grid-cols-3">
            {offers.map((offer) => {
              const isCurrent = offer.plan === current.plan;
              return (
                <Card
                  key={offer.plan}
                  className={cn("flex h-full flex-col", isCurrent && "border-accent")}
                >
                  <CardHeader
                    title={humanise(offer.plan)}
                    description={PLAN_BLURB[offer.plan]}
                    action={isCurrent ? <Badge tone="accent">Current</Badge> : null}
                  />

                  <ul className="flex-1 space-y-1.5">
                    {LIMIT_ROWS.map((row) => (
                      <li key={row.key} className="flex items-baseline justify-between gap-3 text-xs">
                        <span className="text-muted">{row.label}</span>
                        <span className="tnum font-medium text-ink">
                          {describeLimit(offer.limits[row.key as keyof typeof offer.limits], row.kind)}
                        </span>
                      </li>
                    ))}
                  </ul>

                  <div className="mt-4">
                    {isCurrent ? (
                      <Button variant="outline" className="w-full justify-center" disabled>
                        Your plan
                      </Button>
                    ) : offer.purchasable && canManage ? (
                      <Button
                        variant="primary"
                        className="w-full justify-center"
                        icon={<CreditCard className="h-3.5 w-3.5" />}
                        loading={checkout.isPending && checkout.variables === offer.plan}
                        onClick={() => checkout.mutate(offer.plan)}
                      >
                        Switch to {humanise(offer.plan)}
                      </Button>
                    ) : (
                      <Button variant="outline" className="w-full justify-center" disabled>
                        {!canManage ? "Managers only" : "Not available here"}
                      </Button>
                    )}
                  </div>
                </Card>
              );
            })}
          </div>
        </>
      )}
    </>
  );
}

/** Render a limit value. `null` means unlimited, never zero. */
function describeLimit(value: unknown, kind: "count" | "bytes" | "days" | "boolean") {
  if (kind === "boolean") {
    return value ? (
      <Check className="h-3.5 w-3.5 text-profit" aria-label="Included" />
    ) : (
      <Minus className="h-3.5 w-3.5 text-faint" aria-label="Not included" />
    );
  }
  if (value === null || value === undefined) return "Unlimited";
  if (kind === "bytes") return formatBytes(Number(value));
  if (kind === "days") return `${formatInteger(Number(value))} days`;
  return formatInteger(Number(value));
}
