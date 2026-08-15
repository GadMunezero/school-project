"use client";

import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, ChevronsUpDown } from "lucide-react";
import type { ReactNode } from "react";

import type { PageMeta } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "./primitives";
import { TableSkeleton } from "./feedback";

export interface DataTableProps<T> {
  data: T[];
  columns: ColumnDef<T, unknown>[];
  meta?: PageMeta;
  loading?: boolean;
  /**
   * Sorting and pagination are **server-side**: the table reports intent and the caller refetches.
   * Sorting a page of 50 rows client-side would sort the page, not the dataset, which is a subtly
   * wrong answer on a journal with thousands of trades.
   */
  sorting?: SortingState;
  onSortingChange?: (sorting: SortingState) => void;
  onPageChange?: (page: number) => void;
  onRowClick?: (row: T) => void;
  emptyState?: ReactNode;
  /** Stable row identity, so selection survives a refetch. */
  getRowId?: (row: T) => string;
  selectedIds?: Set<string>;
}

export function DataTable<T>({
  data,
  columns,
  meta,
  loading,
  sorting = [],
  onSortingChange,
  onPageChange,
  onRowClick,
  emptyState,
  getRowId,
  selectedIds,
}: DataTableProps<T>) {
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    manualSorting: true,
    manualPagination: true,
    onSortingChange: (updater) => {
      if (!onSortingChange) return;
      onSortingChange(typeof updater === "function" ? updater(sorting) : updater);
    },
    getCoreRowModel: getCoreRowModel(),
    ...(getRowId ? { getRowId: (row: T) => getRowId(row) } : {}),
  });

  if (loading && data.length === 0) {
    return <TableSkeleton columns={columns.length} />;
  }

  if (!loading && data.length === 0) {
    return <>{emptyState}</>;
  }

  return (
    <div className="space-y-3">
      <div className="overflow-x-auto rounded border border-line">
        <table className="w-full border-collapse text-sm">
          <thead className="bg-raised">
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => {
                  const sortable = header.column.getCanSort() && Boolean(onSortingChange);
                  const direction = header.column.getIsSorted();
                  return (
                    <th
                      key={header.id}
                      scope="col"
                      style={header.getSize() !== 150 ? { width: header.getSize() } : undefined}
                      className="whitespace-nowrap px-3 py-2 text-left text-2xs font-semibold uppercase tracking-wide text-faint"
                      aria-sort={
                        direction === "asc" ? "ascending" : direction === "desc" ? "descending" : undefined
                      }
                    >
                      {header.isPlaceholder ? null : sortable ? (
                        <button
                          type="button"
                          onClick={header.column.getToggleSortingHandler()}
                          className="inline-flex items-center gap-1 transition-colors hover:text-ink"
                        >
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {direction === "asc" ? (
                            <ArrowUp className="h-3 w-3" aria-hidden />
                          ) : direction === "desc" ? (
                            <ArrowDown className="h-3 w-3" aria-hidden />
                          ) : (
                            <ChevronsUpDown className="h-3 w-3 opacity-40" aria-hidden />
                          )}
                        </button>
                      ) : (
                        flexRender(header.column.columnDef.header, header.getContext())
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody className={cn(loading && "opacity-60 transition-opacity")}>
            {table.getRowModel().rows.map((row) => (
              <tr
                key={row.id}
                onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                className={cn(
                  "border-t border-line",
                  onRowClick && "cursor-pointer",
                  "hover:bg-raised/60",
                  selectedIds?.has(row.id) && "bg-accent/5",
                )}
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="whitespace-nowrap px-3 py-2 text-ink">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {meta && meta.total_pages > 1 ? (
        <nav className="flex items-center justify-between text-xs text-muted" aria-label="Pagination">
          <p className="tnum">
            Page {meta.page} of {meta.total_pages} · {meta.total.toLocaleString()} rows
          </p>
          <div className="flex gap-1">
            <Button
              size="sm"
              variant="outline"
              disabled={meta.page <= 1 || loading}
              onClick={() => onPageChange?.(meta.page - 1)}
              icon={<ChevronLeft className="h-3.5 w-3.5" />}
            >
              Previous
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={!meta.has_next || loading}
              onClick={() => onPageChange?.(meta.page + 1)}
            >
              Next
              <ChevronRight className="h-3.5 w-3.5" aria-hidden />
            </Button>
          </div>
        </nav>
      ) : null}
    </div>
  );
}
