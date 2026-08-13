"use client";

import type { ReactNode } from "react";
import { AlertTriangle, Inbox, Loader2, RefreshCw } from "lucide-react";

import { ApiError, NetworkError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "./primitives";

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton h-4 w-full", className)} aria-hidden />;
}

export function TableSkeleton({ rows = 8, columns = 6 }: { rows?: number; columns?: number }) {
  return (
    <div className="space-y-2" role="status" aria-label="Loading">
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <div key={rowIndex} className="flex gap-3">
          {Array.from({ length: columns }).map((_, columnIndex) => (
            <Skeleton key={columnIndex} className="h-8 flex-1" />
          ))}
        </div>
      ))}
    </div>
  );
}

export function MetricsSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" role="status" aria-label="Loading">
      {Array.from({ length: count }).map((_, index) => (
        <Skeleton key={index} className="h-[86px] rounded" />
      ))}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 aria-hidden className={cn("h-4 w-4 animate-spin text-muted", className)} />;
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded border border-dashed border-line px-6 py-12 text-center",
        className,
      )}
    >
      <div className="mb-3 text-faint">{icon ?? <Inbox className="h-7 w-7" aria-hidden />}</div>
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      {description ? <p className="mt-1 max-w-sm text-xs text-muted">{description}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

/**
 * Error display.
 *
 * Shows the server's own message — it is written to be user-safe — plus the request id, which is
 * what makes a support conversation actionable. It never invents a friendlier explanation than
 * the one the API gave.
 */
export function ErrorState({
  error,
  onRetry,
  className,
}: {
  error: unknown;
  onRetry?: () => void;
  className?: string;
}) {
  const { message, requestId } = describeError(error);

  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center justify-center rounded border border-loss/30 bg-loss/5 px-6 py-10 text-center",
        className,
      )}
    >
      <AlertTriangle className="mb-3 h-6 w-6 text-loss" aria-hidden />
      <p className="text-sm font-medium text-ink">{message}</p>
      {requestId ? (
        <p className="mt-1 font-mono text-2xs text-faint">Reference: {requestId}</p>
      ) : null}
      {onRetry ? (
        <Button className="mt-4" size="sm" variant="outline" icon={<RefreshCw className="h-3.5 w-3.5" />} onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}

export function describeError(error: unknown): { message: string; requestId?: string } {
  if (error instanceof ApiError) {
    return error.requestId ? { message: error.message, requestId: error.requestId } : { message: error.message };
  }
  if (error instanceof NetworkError) return { message: error.message };
  if (error instanceof Error) return { message: error.message };
  return { message: "Something went wrong." };
}

/** Inline banner for a plan restriction, with the plan the feature needs. */
export function UpgradeNotice({ error, className }: { error: ApiError; className?: string }) {
  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded border border-warn/30 bg-warn/5 p-4 text-sm",
        className,
      )}
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn" aria-hidden />
      <div>
        <p className="font-medium text-ink">{error.message}</p>
        {error.requiredPlan ? (
          <p className="mt-1 text-xs text-muted">
            Available on the {error.requiredPlan} plan.{" "}
            <a href="/billing" className="font-medium text-accent underline underline-offset-2">
              View plans
            </a>
          </p>
        ) : null}
      </div>
    </div>
  );
}
