"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import type { ColumnDef, SortingState } from "@tanstack/react-table";
import { Download, Filter, Plus, Trash2, X } from "lucide-react";

import { API_BASE_URL, api, type QueryParams } from "@/lib/api";
import {
  formatDateTime,
  formatDuration,
  formatMoney,
  formatPercent,
  formatQuantity,
  formatR,
  humanise,
  pnlClass,
} from "@/lib/format";
import { queryKeys } from "@/lib/queries";
import type { Account, Setup, Strategy, Tag, Trade } from "@/lib/types";
import { cn } from "@/lib/utils";
import { DataTable } from "@/components/ui/data-table";
import { EmptyState, ErrorState } from "@/components/ui/feedback";
import { Badge, Button, Checkbox, Field, Input, Select } from "@/components/ui/primitives";
import { ConfirmDialog } from "@/components/ui/overlay";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/shell/page-header";
import { TradeFormDialog } from "@/components/trades/trade-form";

const PAGE_SIZE = 25;

interface Filters {
  account_id: string;
  symbol: string;
  direction: string;
  status: string;
  strategy_id: string;
  setup_id: string;
  tag_id: string;
  outcome: string;
  date_from: string;
  date_to: string;
  search: string;
}

const EMPTY_FILTERS: Filters = {
  account_id: "",
  symbol: "",
  direction: "",
  status: "",
  strategy_id: "",
  setup_id: "",
  tag_id: "",
  outcome: "",
  date_from: "",
  date_to: "",
  search: "",
};

export default function JournalPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const toast = useToast();

  const [filters, setFilters] = useState<Filters>({
    ...EMPTY_FILTERS,
    status: searchParams.get("status") ?? "",
    symbol: searchParams.get("symbol") ?? "",
    tag_id: searchParams.get("tag_id") ?? "",
  });
  const [page, setPage] = useState(1);
  const [sorting, setSorting] = useState<SortingState>([{ id: "entry_timestamp", desc: true }]);
  const [showFilters, setShowFilters] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [creating, setCreating] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const accounts = useQuery({
    queryKey: queryKeys.accounts(),
    queryFn: () => api.list<Account>("/api/v1/accounts", { page_size: 100 }),
  });
  const strategies = useQuery({
    queryKey: queryKeys.strategies(),
    queryFn: () => api.list<Strategy>("/api/v1/strategies", { page_size: 100 }),
  });
  const setups = useQuery({ queryKey: queryKeys.setups, queryFn: () => api.get<Setup[]>("/api/v1/setups") });
  const tags = useQuery({ queryKey: queryKeys.tags, queryFn: () => api.get<Tag[]>("/api/v1/tags") });

  const params = useMemo<QueryParams>(() => {
    const sort = sorting[0];
    const query: QueryParams = {
      page,
      page_size: PAGE_SIZE,
      sort_by: sort?.id ?? "entry_timestamp",
      sort_dir: sort?.desc === false ? "asc" : "desc",
    };
    // Only non-empty filters are sent, so the URL stays readable and the server does less work.
    for (const [key, value] of Object.entries(filters)) {
      if (value) query[key] = value;
    }
    return query;
  }, [filters, page, sorting]);

  const trades = useQuery({
    queryKey: queryKeys.trades(params),
    queryFn: () => api.list<Trade>("/api/v1/trades", params),
  });

  const bulkDelete = useMutation({
    mutationFn: (ids: string[]) =>
      api.action<{ succeeded: number }>("/api/v1/trades/bulk/delete", { trade_ids: ids }),
    onSuccess: (result) => {
      toast.success(`Deleted ${result.succeeded} trade${result.succeeded === 1 ? "" : "s"}.`);
      setSelected(new Set());
      setConfirmDelete(false);
      void queryClient.invalidateQueries({ queryKey: ["trades"] });
      void queryClient.invalidateQueries({ queryKey: ["analytics"] });
    },
    onError: (error) => toast.fromError(error, "Could not delete trades"),
  });

  const activeFilterCount = Object.values(filters).filter(Boolean).length;

  const columns = useMemo<ColumnDef<Trade, unknown>[]>(
    () => [
      {
        id: "select",
        size: 36,
        enableSorting: false,
        header: () => <span className="sr-only">Select</span>,
        cell: ({ row }) => (
          <Checkbox
            aria-label={`Select ${row.original.symbol} trade`}
            checked={selected.has(row.original.id)}
            onClick={(event) => event.stopPropagation()}
            onChange={(event) => {
              const next = new Set(selected);
              if (event.target.checked) next.add(row.original.id);
              else next.delete(row.original.id);
              setSelected(next);
            }}
          />
        ),
      },
      {
        accessorKey: "entry_timestamp",
        header: "Entry",
        cell: ({ row }) => (
          <span className="tnum text-xs text-muted">{formatDateTime(row.original.entry_timestamp)}</span>
        ),
      },
      {
        accessorKey: "symbol",
        header: "Symbol",
        cell: ({ row }) => (
          <span className="font-medium">{row.original.symbol}</span>
        ),
      },
      {
        accessorKey: "direction",
        header: "Side",
        enableSorting: false,
        cell: ({ row }) => (
          <Badge tone={row.original.direction === "long" ? "info" : "warn"}>
            {row.original.direction}
          </Badge>
        ),
      },
      {
        accessorKey: "quantity",
        header: "Qty",
        cell: ({ row }) => <span className="tnum text-xs">{formatQuantity(row.original.quantity)}</span>,
      },
      {
        accessorKey: "entry_price",
        header: "In / out",
        enableSorting: false,
        cell: ({ row }) => (
          <span className="tnum text-xs text-muted">
            {row.original.entry_price}
            {row.original.exit_price ? ` → ${row.original.exit_price}` : ""}
          </span>
        ),
      },
      {
        accessorKey: "net_pnl",
        header: "Net P&L",
        cell: ({ row }) => (
          <span className={cn("tnum font-medium", pnlClass(row.original.net_pnl))}>
            {row.original.status === "closed"
              ? formatMoney(row.original.net_pnl, row.original.currency, { signed: true })
              : "—"}
          </span>
        ),
      },
      {
        accessorKey: "r_multiple",
        header: "R",
        cell: ({ row }) => (
          <span className={cn("tnum text-xs", pnlClass(row.original.r_multiple))}>
            {formatR(row.original.r_multiple)}
          </span>
        ),
      },
      {
        accessorKey: "return_percentage",
        header: "Return",
        cell: ({ row }) => (
          <span className="tnum text-xs text-muted">{formatPercent(row.original.return_percentage)}</span>
        ),
      },
      {
        accessorKey: "holding_seconds",
        header: "Held",
        cell: ({ row }) => (
          <span className="tnum text-xs text-muted">{formatDuration(row.original.holding_seconds)}</span>
        ),
      },
      {
        id: "context",
        header: "Setup",
        enableSorting: false,
        cell: ({ row }) => (
          <span className="text-xs text-muted">
            {row.original.setup_name ?? row.original.strategy_name ?? "—"}
          </span>
        ),
      },
      {
        id: "status",
        header: "Status",
        enableSorting: false,
        cell: ({ row }) => (
          <Badge tone={row.original.status === "closed" ? "neutral" : "accent"}>
            {humanise(row.original.status)}
          </Badge>
        ),
      },
    ],
    [selected],
  );

  const exportHref = `${API_BASE_URL}/api/v1/users/me/export`;

  return (
    <>
      <PageHeader
        title="Journal"
        description="Every execution, position and round trip you have recorded."
        action={
          <>
            <Button
              variant="outline"
              icon={<Filter className="h-3.5 w-3.5" />}
              onClick={() => setShowFilters((open) => !open)}
            >
              Filters
              {activeFilterCount > 0 ? (
                <span className="ml-1 rounded bg-accent px-1.5 text-2xs text-accent-ink">
                  {activeFilterCount}
                </span>
              ) : null}
            </Button>
            <a href={exportHref} download>
              <Button variant="outline" icon={<Download className="h-3.5 w-3.5" />}>
                Export
              </Button>
            </a>
            <Button variant="primary" icon={<Plus className="h-3.5 w-3.5" />} onClick={() => setCreating(true)}>
              Record trade
            </Button>
          </>
        }
      />

      {showFilters ? (
        <div className="mb-4 rounded border border-line bg-surface p-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Search" htmlFor="filter-search">
              <Input
                id="filter-search"
                placeholder="Symbol, notes, reference"
                value={filters.search}
                onChange={(event) => {
                  setFilters((current) => ({ ...current, search: event.target.value }));
                  setPage(1);
                }}
              />
            </Field>
            <FilterSelect
              label="Account"
              value={filters.account_id}
              onChange={(value) => {
                setFilters((current) => ({ ...current, account_id: value }));
                setPage(1);
              }}
              options={(accounts.data?.data ?? []).map((account) => ({ value: account.id, label: account.name }))}
            />
            <FilterSelect
              label="Direction"
              value={filters.direction}
              onChange={(value) => setFilters((current) => ({ ...current, direction: value }))}
              options={[
                { value: "long", label: "Long" },
                { value: "short", label: "Short" },
              ]}
            />
            <FilterSelect
              label="Status"
              value={filters.status}
              onChange={(value) => setFilters((current) => ({ ...current, status: value }))}
              options={[
                { value: "open", label: "Open" },
                { value: "partially_closed", label: "Partially closed" },
                { value: "closed", label: "Closed" },
              ]}
            />
            <FilterSelect
              label="Outcome"
              value={filters.outcome}
              onChange={(value) => setFilters((current) => ({ ...current, outcome: value }))}
              options={[
                { value: "winners", label: "Winners" },
                { value: "losers", label: "Losers" },
                { value: "breakeven", label: "Breakeven" },
              ]}
            />
            <FilterSelect
              label="Strategy"
              value={filters.strategy_id}
              onChange={(value) => setFilters((current) => ({ ...current, strategy_id: value }))}
              options={(strategies.data?.data ?? []).map((item) => ({ value: item.id, label: item.name }))}
            />
            <FilterSelect
              label="Setup"
              value={filters.setup_id}
              onChange={(value) => setFilters((current) => ({ ...current, setup_id: value }))}
              options={(setups.data ?? []).map((item) => ({ value: item.id, label: item.name }))}
            />
            <FilterSelect
              label="Tag"
              value={filters.tag_id}
              onChange={(value) => setFilters((current) => ({ ...current, tag_id: value }))}
              options={(tags.data ?? []).map((item) => ({ value: item.id, label: item.name }))}
            />
            <Field label="From" htmlFor="filter-from">
              <Input
                id="filter-from"
                type="date"
                value={filters.date_from}
                onChange={(event) => setFilters((current) => ({ ...current, date_from: event.target.value }))}
              />
            </Field>
            <Field label="To" htmlFor="filter-to">
              <Input
                id="filter-to"
                type="date"
                value={filters.date_to}
                onChange={(event) => setFilters((current) => ({ ...current, date_to: event.target.value }))}
              />
            </Field>
          </div>
          {activeFilterCount > 0 ? (
            <Button
              size="sm"
              variant="ghost"
              className="mt-3"
              icon={<X className="h-3.5 w-3.5" />}
              onClick={() => {
                setFilters(EMPTY_FILTERS);
                setPage(1);
              }}
            >
              Clear filters
            </Button>
          ) : null}
        </div>
      ) : null}

      {selected.size > 0 ? (
        <div className="mb-3 flex items-center gap-3 rounded border border-accent/30 bg-accent/5 px-3 py-2 text-sm">
          <span className="font-medium">{selected.size} selected</span>
          <Button
            size="sm"
            variant="danger"
            icon={<Trash2 className="h-3.5 w-3.5" />}
            onClick={() => setConfirmDelete(true)}
          >
            Delete
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
            Clear selection
          </Button>
        </div>
      ) : null}

      {trades.isError ? (
        <ErrorState error={trades.error} onRetry={() => void trades.refetch()} />
      ) : (
        <DataTable
          data={trades.data?.data ?? []}
          columns={columns}
          {...(trades.data?.meta ? { meta: trades.data.meta } : {})}
          loading={trades.isLoading || trades.isFetching}
          sorting={sorting}
          onSortingChange={(next) => {
            setSorting(next);
            setPage(1);
          }}
          onPageChange={setPage}
          onRowClick={(trade) => router.push(`/journal/${trade.id}`)}
          getRowId={(trade) => trade.id}
          selectedIds={selected}
          emptyState={
            <EmptyState
              title={activeFilterCount > 0 ? "No trades match these filters" : "No trades yet"}
              description={
                activeFilterCount > 0
                  ? "Try widening the date range or clearing a filter."
                  : "Record your first trade, or import your broker's execution history."
              }
              action={
                <Button variant="primary" onClick={() => setCreating(true)}>
                  Record a trade
                </Button>
              }
            />
          }
        />
      )}

      <TradeFormDialog
        open={creating}
        onClose={() => setCreating(false)}
        accounts={accounts.data?.data ?? []}
        strategies={strategies.data?.data ?? []}
        setups={setups.data ?? []}
        tags={tags.data ?? []}
      />

      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => bulkDelete.mutate([...selected])}
        title="Delete selected trades?"
        message={`${selected.size} trade${selected.size === 1 ? "" : "s"} will be removed from your journal and excluded from analytics. This cannot be undone from the interface.`}
        confirmLabel="Delete"
        destructive
        loading={bulkDelete.isPending}
      />
    </>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
}) {
  const id = `filter-${label.toLowerCase().replace(/\s+/g, "-")}`;
  return (
    <Field label={label} htmlFor={id}>
      <Select id={id} value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">All</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </Select>
    </Field>
  );
}
