"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { FlaskConical, Plus } from "lucide-react";

import { ApiError, api } from "@/lib/api";
import { formatDate, formatMoney, formatPercent } from "@/lib/format";
import { queryKeys } from "@/lib/queries";
import type { Backtest } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton, UpgradeNotice } from "@/components/ui/feedback";
import { Badge, Button } from "@/components/ui/primitives";
import { PageHeader } from "@/components/shell/page-header";
import { BacktestFormDialog } from "@/components/backtests/backtest-form";

export default function BacktesterPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);

  const backtests = useQuery({
    queryKey: queryKeys.backtests(),
    queryFn: () => api.list<Backtest>("/api/v1/backtests", { page_size: 50 }),
  });

  if (backtests.isError && backtests.error instanceof ApiError && backtests.error.isEntitlement) {
    return (
      <>
        <PageHeader title="Backtester" />
        <UpgradeNotice error={backtests.error} />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Backtester"
        description="Deterministic simulation. Same execution rules as replay; results are reproducible."
        action={
          <Button variant="primary" icon={<Plus className="h-3.5 w-3.5" />} onClick={() => setCreating(true)}>
            New backtest
          </Button>
        }
      />

      {backtests.isError ? (
        <ErrorState error={backtests.error} onRetry={() => void backtests.refetch()} />
      ) : backtests.isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((index) => (
            <Skeleton key={index} className="h-32 rounded" />
          ))}
        </div>
      ) : (backtests.data?.data.length ?? 0) === 0 ? (
        <EmptyState
          icon={<FlaskConical className="h-7 w-7" />}
          title="No backtests yet"
          description="Pick a built-in strategy, an instrument with loaded candles, and a date range. The run is queued and executed by a worker — nothing blocks."
          action={
            <Button variant="primary" onClick={() => setCreating(true)}>
              Configure a backtest
            </Button>
          }
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {backtests.data?.data.map((backtest) => (
            <Link key={backtest.id} href={`/backtester/${backtest.id}`}>
              <Card className="h-full transition-colors hover:border-accent/50">
                <div className="flex items-start justify-between gap-2">
                  <h3 className="text-sm font-semibold text-ink">{backtest.name}</h3>
                  <Badge>{backtest.timeframe}</Badge>
                </div>
                <p className="mt-1 text-xs text-muted">
                  {formatDate(backtest.start_date)} → {formatDate(backtest.end_date)}
                </p>
                <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
                  <div>
                    <dt className="text-faint">Capital</dt>
                    <dd className="tnum text-ink">{formatMoney(backtest.initial_capital, backtest.currency)}</dd>
                  </div>
                  <div>
                    <dt className="text-faint">Risk</dt>
                    <dd className="tnum text-ink">{formatPercent(backtest.risk_percent)}</dd>
                  </div>
                  <div>
                    <dt className="text-faint">Sizing</dt>
                    <dd className="text-ink">{backtest.position_sizing.replace(/_/g, " ")}</dd>
                  </div>
                  <div>
                    <dt className="text-faint">Execution</dt>
                    <dd className="text-ink">{backtest.execution_model.replace(/_/g, " ")}</dd>
                  </div>
                </dl>
              </Card>
            </Link>
          ))}
        </div>
      )}

      <BacktestFormDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(backtest) => {
          void queryClient.invalidateQueries({ queryKey: ["backtests"] });
          router.push(`/backtester/${backtest.id}`);
        }}
      />
    </>
  );
}
