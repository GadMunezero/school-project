"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { CornerDownLeft, Search } from "lucide-react";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import type { SearchHit } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Spinner } from "@/components/ui/feedback";

const TYPE_LABELS: Record<string, string> = {
  trade: "Trade",
  account: "Account",
  strategy: "Strategy",
  setup: "Setup",
  tag: "Tag",
  instrument: "Instrument",
};

/**
 * Global search.
 *
 * Debounced and server-side: the API returns a bounded, tenant-scoped result set, so the browser
 * never holds a searchable copy of the workspace.
 */
export function CommandMenu({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [term, setTerm] = useState("");
  const [debounced, setDebounced] = useState("");
  const [highlighted, setHighlighted] = useState(0);

  useEffect(() => {
    if (!open) {
      setTerm("");
      setDebounced("");
      setHighlighted(0);
      return;
    }
    inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(term.trim()), 220);
    return () => window.clearTimeout(timer);
  }, [term]);

  const results = useQuery({
    queryKey: queryKeys.search(debounced),
    queryFn: () => api.get<SearchHit[]>("/api/v1/search", { q: debounced }),
    enabled: open && debounced.length >= 2,
  });

  // `results.data ?? []` would allocate a new array on every render, re-attaching the keydown
  // listener below each time. Memoising keeps the listener stable between queries.
  const hits = useMemo(() => results.data ?? [], [results.data]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setHighlighted((index) => Math.min(index + 1, hits.length - 1));
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setHighlighted((index) => Math.max(index - 1, 0));
      }
      if (event.key === "Enter") {
        const hit = hits[highlighted];
        if (hit) {
          router.push(hit.href);
          onClose();
        }
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, hits, highlighted, router, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center p-4 pt-[12vh]">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} aria-hidden />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Search"
        className="relative z-10 w-full max-w-xl animate-fade-in overflow-hidden rounded border border-line bg-surface shadow-pop"
      >
        <div className="flex items-center gap-2 border-b border-line px-3">
          <Search className="h-4 w-4 shrink-0 text-faint" aria-hidden />
          <input
            ref={inputRef}
            value={term}
            onChange={(event) => {
              setTerm(event.target.value);
              setHighlighted(0);
            }}
            placeholder="Search trades, accounts, strategies, setups…"
            aria-label="Search"
            className="h-11 flex-1 bg-transparent text-sm outline-none placeholder:text-faint"
          />
          {results.isFetching ? <Spinner /> : null}
        </div>

        <div className="max-h-80 overflow-y-auto p-1">
          {debounced.length < 2 ? (
            <p className="px-3 py-6 text-center text-xs text-faint">
              Type at least two characters to search.
            </p>
          ) : hits.length === 0 && !results.isFetching ? (
            <p className="px-3 py-6 text-center text-xs text-faint">
              No matches for “{debounced}”.
            </p>
          ) : (
            hits.map((hit, index) => (
              <button
                key={`${hit.type}-${hit.id}`}
                type="button"
                onMouseEnter={() => setHighlighted(index)}
                onClick={() => {
                  router.push(hit.href);
                  onClose();
                }}
                className={cn(
                  "flex w-full items-center gap-3 rounded px-3 py-2 text-left transition-colors",
                  index === highlighted ? "bg-raised" : "hover:bg-raised/60",
                )}
              >
                <span className="w-16 shrink-0 text-2xs uppercase tracking-wide text-faint">
                  {TYPE_LABELS[hit.type] ?? hit.type}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-ink">{hit.title}</span>
                  {hit.subtitle ? (
                    <span className="block truncate text-xs text-muted">{hit.subtitle}</span>
                  ) : null}
                </span>
                {index === highlighted ? (
                  <CornerDownLeft className="h-3.5 w-3.5 text-faint" aria-hidden />
                ) : null}
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
