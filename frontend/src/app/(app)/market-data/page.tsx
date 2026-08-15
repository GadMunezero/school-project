"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import { CandlestickChart, Upload } from "lucide-react";

import { api } from "@/lib/api";
import { formatDate, formatInteger } from "@/lib/format";
import { queryKeys } from "@/lib/queries";
import type {
  CandleField,
  CandleImportResult,
  CandleInspection,
  CoverageRow,
  Instrument,
  Timeframe,
} from "@/lib/types";
import { Card, CardHeader } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import { Modal } from "@/components/ui/overlay";
import { Badge, Button, Field, Select } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/shell/page-header";

const TIMEFRAMES: Timeframe[] = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"];

/** Every field the parser reads. Only `volume` is optional. */
const FIELDS: { key: CandleField; label: string; required: boolean }[] = [
  { key: "timestamp", label: "Timestamp", required: true },
  { key: "open", label: "Open", required: true },
  { key: "high", label: "High", required: true },
  { key: "low", label: "Low", required: true },
  { key: "close", label: "Close", required: true },
  { key: "volume", label: "Volume", required: false },
];

const ZONES = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "Europe/London",
  "Asia/Tokyo",
];

export default function MarketDataPage() {
  const [importing, setImporting] = useState(false);

  const instruments = useQuery({
    queryKey: queryKeys.instruments({ page_size: 200 }),
    queryFn: () => api.list<Instrument>("/api/v1/instruments", { page_size: 200 }),
  });

  const rows = instruments.data?.data ?? [];

  return (
    <>
      <PageHeader
        title="Market data"
        description="Reports, backtests and replay all read these candles. Until you load your own, every figure they show describes the sample data this workspace was seeded with."
        action={
          <Button
            variant="primary"
            icon={<Upload className="h-3.5 w-3.5" />}
            onClick={() => setImporting(true)}
            disabled={rows.length === 0}
          >
            Import candles
          </Button>
        }
      />

      {instruments.isError ? (
        <ErrorState error={instruments.error} onRetry={() => void instruments.refetch()} />
      ) : instruments.isLoading ? (
        <Skeleton className="h-40 rounded" />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<CandlestickChart className="h-7 w-7" />}
          title="No instruments yet"
          description="Candles are stored against an instrument. Create one first, then import its history."
        />
      ) : (
        <div className="space-y-3">
          {rows.map((instrument) => (
            <InstrumentCoverage key={instrument.id} instrument={instrument} />
          ))}
        </div>
      )}

      <ImportModal
        open={importing}
        onClose={() => setImporting(false)}
        instruments={rows}
      />
    </>
  );
}

/** One instrument, and every series stored for it. */
function InstrumentCoverage({ instrument }: { instrument: Instrument }) {
  const coverage = useQuery({
    queryKey: queryKeys.coverage(instrument.id),
    queryFn: () =>
      api.list<CoverageRow>(`/api/v1/market-data/coverage/${instrument.id}`),
  });

  const rows = coverage.data?.data ?? [];

  return (
    <Card padded={false}>
      <CardHeader
        title={instrument.symbol}
        description={instrument.name ?? undefined}
        action={<Badge tone="neutral">{instrument.asset_type}</Badge>}
      />
      {coverage.isLoading ? (
        <div className="px-4 pb-4">
          <Skeleton className="h-10 rounded" />
        </div>
      ) : rows.length === 0 ? (
        <p className="px-4 pb-4 text-xs text-muted">
          No candles stored. Import a CSV to run reports on this instrument.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-2xs uppercase tracking-wide text-faint">
              <tr className="border-t border-line">
                <th className="px-4 py-2 text-left font-semibold">Timeframe</th>
                <th className="px-4 py-2 text-right font-semibold">Candles</th>
                <th className="px-4 py-2 text-left font-semibold">From</th>
                <th className="px-4 py-2 text-left font-semibold">To</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {rows.map((row) => (
                <tr key={`${row.source_id}-${row.timeframe}`}>
                  <td className="px-4 py-2 font-medium text-ink">{row.timeframe}</td>
                  <td className="px-4 py-2 text-right tabular-nums text-ink">
                    {formatInteger(row.bar_count)}
                  </td>
                  <td className="px-4 py-2 text-muted">
                    {row.first_bar_at ? formatDate(row.first_bar_at) : "—"}
                  </td>
                  <td className="px-4 py-2 text-muted">
                    {row.last_bar_at ? formatDate(row.last_bar_at) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/**
 * Upload, map, preview, commit.
 *
 * The preview step is not decoration. A wrong column mapping produces a series of plausible
 * candles that is silently wrong, and every report computed from it would be wrong in a way
 * nobody could see. The dry run parses the whole file and shows what would be stored — and what
 * would be refused, with reasons — before anything is written.
 */
function ImportModal({
  open,
  onClose,
  instruments,
}: {
  open: boolean;
  onClose: () => void;
  instruments: Instrument[];
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const inputRef = useRef<HTMLInputElement>(null);

  const [file, setFile] = useState<File | null>(null);
  const [inspection, setInspection] = useState<CandleInspection | null>(null);
  const [mapping, setMapping] = useState<Partial<Record<CandleField, string>>>({});
  const [instrumentId, setInstrumentId] = useState("");
  const [timeframe, setTimeframe] = useState<Timeframe>("1d");
  const [zone, setZone] = useState("UTC");
  const [preview, setPreview] = useState<CandleImportResult | null>(null);

  function reset() {
    setFile(null);
    setInspection(null);
    setMapping({});
    setPreview(null);
  }

  function close() {
    reset();
    onClose();
  }

  const inspect = useMutation({
    mutationFn: (chosen: File) => {
      const body = new FormData();
      body.append("file", chosen);
      return api.upload<CandleInspection>("/api/v1/market-data/import/inspect", body);
    },
    onSuccess: (result) => {
      setInspection(result);
      // The server only suggests exact header matches, so anything it left out is a real
      // ambiguity the user has to resolve rather than one we should guess at.
      setMapping(result.suggested_mapping);
      setPreview(null);
    },
    onError: (error) => toast.fromError(error, "Could not read that file"),
  });

  const submit = useMutation({
    mutationFn: (dryRun: boolean) => {
      const body = new FormData();
      body.append("file", file as File);
      body.append("instrument_id", instrumentId);
      body.append("timeframe", timeframe);
      body.append("column_mapping", JSON.stringify(mapping));
      body.append("source_timezone", zone);
      body.append("dry_run", String(dryRun));
      return api.upload<CandleImportResult>("/api/v1/market-data/import", body);
    },
    onSuccess: (result) => {
      if (result.dry_run) {
        setPreview(result);
        return;
      }
      void queryClient.invalidateQueries({ queryKey: queryKeys.allCoverage });
      toast.success(
        result.stored > 0
          ? `Stored ${formatInteger(result.stored)} candles for ${result.instrument.symbol}.`
          : `Nothing new to store — all ${formatInteger(result.already_stored ?? 0)} candles were already there.`,
      );
      close();
    },
    onError: (error) => toast.fromError(error, "Could not import those candles"),
  });

  const missing = useMemo(
    () => FIELDS.filter((field) => field.required && !mapping[field.key]).map((f) => f.label),
    [mapping],
  );
  const ready = Boolean(file && instrumentId && missing.length === 0);

  return (
    <Modal
      open={open}
      onClose={close}
      title="Import candles"
      description="OHLCV from a CSV export. Rows that cannot become a valid candle are listed with a reason rather than repaired."
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button
            variant="outline"
            loading={submit.isPending && submit.variables === true}
            disabled={!ready || submit.isPending}
            onClick={() => submit.mutate(true)}
          >
            Preview
          </Button>
          <Button
            variant="primary"
            loading={submit.isPending && submit.variables === false}
            disabled={!ready || submit.isPending || preview === null}
            onClick={() => submit.mutate(false)}
          >
            Import
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field
          label="File"
          htmlFor="md-file"
          hint="A CSV with one row per candle."
          required
        >
          <div className="flex items-center gap-2">
            <input
              ref={inputRef}
              id="md-file"
              type="file"
              accept=".csv,text/csv"
              className="sr-only"
              onChange={(event) => {
                const chosen = event.target.files?.[0] ?? null;
                reset();
                setFile(chosen);
                if (chosen) inspect.mutate(chosen);
              }}
            />
            <Button variant="outline" onClick={() => inputRef.current?.click()}>
              Choose file
            </Button>
            <span className="truncate text-xs text-muted">
              {file ? file.name : "No file selected"}
            </span>
          </div>
        </Field>

        {inspect.isPending ? <Skeleton className="h-24 rounded" /> : null}

        {inspection ? (
          <>
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="Instrument" htmlFor="md-instrument" required>
                <Select
                  id="md-instrument"
                  value={instrumentId}
                  onChange={(event) => {
                    setInstrumentId(event.target.value);
                    setPreview(null);
                  }}
                >
                  <option value="">Choose…</option>
                  {instruments.map((instrument) => (
                    <option key={instrument.id} value={instrument.id}>
                      {instrument.symbol}
                    </option>
                  ))}
                </Select>
              </Field>

              <Field label="Timeframe" htmlFor="md-timeframe" required>
                <Select
                  id="md-timeframe"
                  value={timeframe}
                  onChange={(event) => {
                    setTimeframe(event.target.value as Timeframe);
                    setPreview(null);
                  }}
                >
                  {TIMEFRAMES.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </Select>
              </Field>

              <Field
                label="Timestamps are in"
                htmlFor="md-zone"
                hint="Ignored when the file states an offset."
              >
                <Select
                  id="md-zone"
                  value={zone}
                  onChange={(event) => {
                    setZone(event.target.value);
                    setPreview(null);
                  }}
                >
                  {ZONES.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>

            <div>
              <p className="pb-1 text-2xs font-semibold uppercase tracking-wide text-faint">
                Columns
              </p>
              <div className="grid gap-2 sm:grid-cols-2">
                {FIELDS.map((field) => (
                  <Field
                    key={field.key}
                    label={field.label}
                    htmlFor={`md-map-${field.key}`}
                    required={field.required}
                  >
                    <Select
                      id={`md-map-${field.key}`}
                      value={mapping[field.key] ?? ""}
                      onChange={(event) => {
                        const value = event.target.value;
                        setMapping((current) => ({
                          ...current,
                          [field.key]: value || undefined,
                        }));
                        setPreview(null);
                      }}
                    >
                      <option value="">
                        {field.required ? "Choose a column…" : "Not in this file"}
                      </option>
                      {inspection.headers.map((header) => (
                        <option key={header} value={header}>
                          {header}
                        </option>
                      ))}
                    </Select>
                  </Field>
                ))}
              </div>
              <p className="pt-1 text-xs text-muted">
                {formatInteger(inspection.total_rows)} rows · {inspection.headers.length} columns
                {missing.length > 0 ? ` · still to map: ${missing.join(", ")}` : ""}
              </p>
            </div>

            {preview ? <PreviewSummary result={preview} /> : null}
          </>
        ) : null}
      </div>
    </Modal>
  );
}

/** What a dry run found, including every refusal and why. */
function PreviewSummary({ result }: { result: CandleImportResult }) {
  return (
    <div className="rounded border border-line bg-raised p-3">
      <p className="text-sm font-medium text-ink">
        {formatInteger(result.accepted)} of {formatInteger(result.total_rows)} rows parsed
      </p>
      <p className="pt-0.5 text-xs text-muted">
        {result.first_bar_at && result.last_bar_at
          ? `${formatDate(result.first_bar_at)} → ${formatDate(result.last_bar_at)}`
          : "No candles in range"}
      </p>

      {result.rejected > 0 ? (
        <div className="pt-2">
          <p className="text-xs font-medium text-warn">
            {formatInteger(result.rejected)} rejected
          </p>
          <ul className="max-h-32 overflow-y-auto pt-1 text-xs text-muted">
            {result.rejected_rows.map((row) => (
              <li key={row.row_number} className="py-0.5">
                Row {formatInteger(row.row_number)}: {row.reason}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="pt-1 text-xs text-profit">Every row parsed cleanly.</p>
      )}
    </div>
  );
}
