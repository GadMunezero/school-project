"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowLeft, Check, Undo2 } from "lucide-react";

import { api } from "@/lib/api";
import { formatDateTime, formatInteger, humanise } from "@/lib/format";
import { queryKeys } from "@/lib/queries";
import type { ImportPreview, ImportRecord, ImportRowPreview, ImportTemplate } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Card, CardHeader } from "@/components/ui/card";
import { ErrorState, Skeleton } from "@/components/ui/feedback";
import { ConfirmDialog } from "@/components/ui/overlay";
import { Badge, Button, Field, Select } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/shell/page-header";

/**
 * The five fields the pipeline refuses to run without. Keep this list identical to
 * `REQUIRED_FIELDS` in `backend/tradeloom/services/imports/pipeline.py` — the server enforces it,
 * this copy only decides when to enable the button.
 */
const REQUIRED_FIELDS = ["timestamp", "symbol", "side", "quantity", "price"] as const;

const MAPPABLE_FIELDS: { field: string; label: string; hint?: string }[] = [
  { field: "timestamp", label: "Fill time", hint: "When the execution happened" },
  { field: "symbol", label: "Symbol" },
  { field: "side", label: "Side", hint: "Buy or sell" },
  { field: "quantity", label: "Quantity" },
  { field: "price", label: "Fill price" },
  { field: "commission", label: "Commission" },
  { field: "fees", label: "Other fees" },
  { field: "external_id", label: "Execution ID", hint: "Used to skip rows already imported" },
  { field: "stop_loss", label: "Stop loss" },
  { field: "take_profit", label: "Take profit" },
  { field: "notes", label: "Notes" },
  { field: "account", label: "Broker account" },
];

const TIMEZONES = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Los_Angeles",
  "Europe/London",
  "Europe/Berlin",
  "Asia/Tokyo",
  "Asia/Singapore",
  "Australia/Sydney",
];

const STEPS = [
  { key: "map", label: "Map columns" },
  { key: "review", label: "Review rows" },
  { key: "commit", label: "Commit" },
];

/** Which wizard step a server-side status corresponds to. The server's status is the truth. */
function stepFor(status: string): number {
  if (status === "completed" || status === "reverted") return 2;
  if (status === "preview" || status === "importing") return 1;
  return 0;
}

export default function ImportDetailPage() {
  const params = useParams<{ importId: string }>();
  const importId = params.importId;
  const queryClient = useQueryClient();
  const toast = useToast();
  const [confirmRevert, setConfirmRevert] = useState(false);

  const record = useQuery({
    queryKey: queryKeys.importRecord(importId),
    queryFn: () => api.get<ImportRecord>(`/api/v1/imports/${importId}`),
  });

  const templates = useQuery({
    queryKey: queryKeys.importTemplates,
    queryFn: () => api.get<ImportTemplate[]>("/api/v1/imports/templates/available"),
  });

  const data = record.data;
  const step = data ? stepFor(data.status) : 0;

  const preview = useQuery({
    queryKey: queryKeys.importPreview(importId),
    queryFn: () => api.get<ImportPreview>(`/api/v1/imports/${importId}/preview`, { limit: 50 }),
    enabled: step >= 1,
  });

  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [timezone, setTimezone] = useState("UTC");

  // Seed the form from the server's suggestion once the record arrives, and re-seed if the record
  // is replaced (e.g. after applying a broker template).
  useEffect(() => {
    if (!data) return;
    setMapping(data.column_mapping ?? {});
    const stored = data.options?.timezone;
    if (typeof stored === "string") setTimezone(stored);
  }, [data]);

  const setStep = (next: ImportRecord) => {
    queryClient.setQueryData(queryKeys.importRecord(importId), next);
    void queryClient.invalidateQueries({ queryKey: ["imports"] });
  };

  const saveMapping = useMutation({
    mutationFn: () =>
      api.put<ImportRecord>(`/api/v1/imports/${importId}/mapping`, {
        column_mapping: Object.fromEntries(
          Object.entries(mapping).filter(([, column]) => Boolean(column)),
        ),
        options: { timezone },
      }),
    onSuccess: (next) => setStep(next),
    onError: (error) => toast.fromError(error, "Could not save the mapping"),
  });

  const validate = useMutation({
    mutationFn: () => api.post<ImportRecord>(`/api/v1/imports/${importId}/validate`),
    onSuccess: (next) => {
      setStep(next);
      void queryClient.invalidateQueries({ queryKey: queryKeys.importPreview(importId) });
    },
    onError: (error) => toast.fromError(error, "Validation failed"),
  });

  const commit = useMutation({
    mutationFn: () => api.post<ImportRecord>(`/api/v1/imports/${importId}/commit`),
    onSuccess: (next) => {
      setStep(next);
      void queryClient.invalidateQueries({ queryKey: ["trades"] });
      void queryClient.invalidateQueries({ queryKey: ["accounts"] });
      toast.success(
        "Import committed",
        `${formatInteger(next.created_trade_count)} trades built from ${formatInteger(next.imported_rows)} rows.`,
      );
    },
    onError: (error) => toast.fromError(error, "Could not commit the import"),
  });

  const revert = useMutation({
    mutationFn: () => api.post<ImportRecord>(`/api/v1/imports/${importId}/revert`),
    onSuccess: (next) => {
      setConfirmRevert(false);
      setStep(next);
      void queryClient.invalidateQueries({ queryKey: ["trades"] });
      void queryClient.invalidateQueries({ queryKey: ["accounts"] });
      toast.success("Import reverted", "Every trade and order it created has been removed.");
    },
    onError: (error) => toast.fromError(error, "Could not revert the import"),
  });

  const headers = useMemo(() => data?.inspection?.headers ?? [], [data]);
  const missing = REQUIRED_FIELDS.filter((field) => !mapping[field]);

  if (record.isError) {
    return (
      <>
        <PageHeader title="Import" />
        <ErrorState error={record.error} onRetry={() => void record.refetch()} />
      </>
    );
  }

  if (record.isLoading || !data) {
    return (
      <>
        <PageHeader title="Import" />
        <Skeleton className="h-96 rounded" />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={data.filename}
        description={`${formatInteger(data.total_rows)} rows · uploaded ${formatDateTime(data.created_at)}`}
        action={
          <>
            <Link href="/imports">
              <Button variant="ghost" icon={<ArrowLeft className="h-3.5 w-3.5" />}>
                Back
              </Button>
            </Link>
            {data.can_revert ? (
              <Button
                variant="outline"
                icon={<Undo2 className="h-3.5 w-3.5" />}
                onClick={() => setConfirmRevert(true)}
              >
                Revert import
              </Button>
            ) : null}
          </>
        }
      />

      <Stepper current={step} />

      {step === 0 ? (
        <div className="mt-4 space-y-4">
          {templates.data && templates.data.length > 0 ? (
            <Card>
              <CardHeader
                title="Broker template"
                description={
                  data.inspection?.detected_template
                    ? `We recognised this file as ${data.inspection.detected_template}. Confirm or pick another.`
                    : "Start from a known broker layout, then adjust anything that differs."
                }
              />
              <div className="flex flex-wrap gap-2">
                {templates.data.map((template) => (
                  <Button
                    key={template.id}
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setMapping(template.column_mapping);
                      const tz = template.options?.timezone;
                      if (typeof tz === "string") setTimezone(tz);
                    }}
                  >
                    {template.name}
                  </Button>
                ))}
              </div>
            </Card>
          ) : null}

          <Card>
            <CardHeader
              title="Map your columns"
              description="Pick which column in your file holds each field. Anything left unmapped is simply not imported."
            />

            <div className="grid gap-3 sm:grid-cols-2">
              {MAPPABLE_FIELDS.map(({ field, label, hint }) => {
                const required = (REQUIRED_FIELDS as readonly string[]).includes(field);
                return (
                  <Field
                    key={field}
                    label={label}
                    htmlFor={`map-${field}`}
                    hint={hint}
                    required={required}
                  >
                    <Select
                      id={`map-${field}`}
                      value={mapping[field] ?? ""}
                      aria-invalid={required && !mapping[field]}
                      onChange={(event) =>
                        setMapping((current) => ({ ...current, [field]: event.target.value }))
                      }
                    >
                      <option value="">Not in this file</option>
                      {headers.map((header) => (
                        <option key={header} value={header}>
                          {header}
                        </option>
                      ))}
                    </Select>
                  </Field>
                );
              })}
            </div>

            <div className="mt-4 border-t border-line pt-4">
              <Field
                label="Timestamps in this file are in"
                htmlFor="map-tz"
                hint="Times are converted to UTC on import. Getting this wrong shifts every trade."
                className="max-w-sm"
              >
                <Select
                  id="map-tz"
                  value={timezone}
                  onChange={(event) => setTimezone(event.target.value)}
                >
                  {TIMEZONES.map((zone) => (
                    <option key={zone} value={zone}>
                      {zone}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>
          </Card>

          <SamplePreview headers={headers} rows={data.inspection?.preview ?? []} />

          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-muted">
              {missing.length > 0
                ? `Still to map: ${missing.map((field) => humanise(field)).join(", ")}`
                : "All required fields are mapped."}
            </p>
            <Button
              variant="primary"
              size="lg"
              disabled={missing.length > 0}
              loading={saveMapping.isPending || validate.isPending}
              onClick={async () => {
                await saveMapping.mutateAsync();
                await validate.mutateAsync();
              }}
            >
              Validate rows
            </Button>
          </div>
        </div>
      ) : null}

      {step === 1 ? (
        <div className="mt-4 space-y-4">
          <section className="grid gap-3 sm:grid-cols-4">
            <Tally label="Rows in file" value={data.total_rows} />
            <Tally label="Will be imported" value={data.valid_rows} tone="profit" />
            <Tally label="Rejected" value={data.invalid_rows} tone={data.invalid_rows > 0 ? "loss" : "neutral"} />
            <Tally
              label="Already imported"
              value={data.duplicate_rows}
              tone={data.duplicate_rows > 0 ? "warn" : "neutral"}
            />
          </section>

          {data.invalid_rows > 0 ? (
            <Card>
              <CardHeader
                title="Rejected rows"
                description="These are skipped. Fix them in your file and upload again if you need them."
              />
              <RowTable rows={preview.data?.invalid_rows ?? []} loading={preview.isLoading} showErrors />
            </Card>
          ) : null}

          <Card>
            <CardHeader
              title="Parsed rows"
              description="What the importer read from your file, after conversion. Check a few before committing."
            />
            <RowTable rows={preview.data?.rows ?? []} loading={preview.isLoading} />
          </Card>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-muted">
              Committing groups these fills by symbol and replays them in time order, producing the
              same trades a manual entry would.
            </p>
            <div className="flex gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  // Going back means re-mapping, which the server allows until commit.
                  queryClient.setQueryData(queryKeys.importRecord(importId), {
                    ...data,
                    status: "mapping",
                  });
                }}
              >
                Change mapping
              </Button>
              <Button
                variant="primary"
                size="lg"
                disabled={data.valid_rows === 0}
                loading={commit.isPending}
                onClick={() => commit.mutate()}
              >
                Import {formatInteger(data.valid_rows)} rows
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      {step === 2 ? (
        <div className="mt-4 space-y-4">
          <Card>
            <div className="flex items-start gap-3">
              <span
                className={cn(
                  "flex h-9 w-9 shrink-0 items-center justify-center rounded-full",
                  data.status === "reverted" ? "bg-raised text-muted" : "bg-profit/12 text-profit",
                )}
              >
                {data.status === "reverted" ? (
                  <Undo2 className="h-4 w-4" aria-hidden />
                ) : (
                  <Check className="h-4 w-4" aria-hidden />
                )}
              </span>
              <div>
                <h2 className="text-sm font-semibold text-ink">
                  {data.status === "reverted" ? "Import reverted" : "Import complete"}
                </h2>
                <p className="mt-0.5 text-sm text-muted">
                  {data.status === "reverted"
                    ? `Every order and trade this import created has been removed. Reverted ${formatDateTime(data.reverted_at)}.`
                    : `${formatInteger(data.imported_rows)} rows became ${formatInteger(data.created_order_count)} fills and ${formatInteger(data.created_trade_count)} trades on ${formatDateTime(data.committed_at)}.`}
                </p>
                {data.status !== "reverted" ? (
                  <div className="mt-3 flex gap-2">
                    <Link href="/journal">
                      <Button variant="primary">Open the journal</Button>
                    </Link>
                    <Link href="/dashboard">
                      <Button variant="outline">See the dashboard</Button>
                    </Link>
                  </div>
                ) : null}
              </div>
            </div>
          </Card>

          {data.duplicate_rows > 0 ? (
            <p className="rounded border border-line bg-raised p-3 text-xs text-muted">
              {formatInteger(data.duplicate_rows)} rows were skipped because their execution IDs had
              already been imported. Re-uploading the same file is safe.
            </p>
          ) : null}
        </div>
      ) : null}

      <ConfirmDialog
        open={confirmRevert}
        onClose={() => setConfirmRevert(false)}
        onConfirm={() => revert.mutate()}
        loading={revert.isPending}
        destructive
        confirmLabel="Revert import"
        title="Revert this import?"
        message="Every order and trade created by this import is deleted, and account balances are recalculated. Trades you recorded by hand are untouched."
      />
    </>
  );
}

function Stepper({ current }: { current: number }) {
  return (
    <ol className="flex items-center gap-2 text-xs">
      {STEPS.map((step, index) => {
        const done = index < current;
        const active = index === current;
        return (
          <li key={step.key} className="flex items-center gap-2">
            <span
              className={cn(
                "flex h-5 w-5 items-center justify-center rounded-full text-2xs font-semibold",
                done && "bg-profit/15 text-profit",
                active && "bg-accent text-accent-ink",
                !done && !active && "bg-raised text-faint",
              )}
            >
              {done ? <Check className="h-3 w-3" aria-hidden /> : index + 1}
            </span>
            <span className={cn(active ? "font-medium text-ink" : "text-muted")}>{step.label}</span>
            {index < STEPS.length - 1 ? <span className="mx-1 h-px w-6 bg-line" /> : null}
          </li>
        );
      })}
    </ol>
  );
}

function Tally({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number;
  tone?: "neutral" | "profit" | "loss" | "warn";
}) {
  const colours = {
    neutral: "text-ink",
    profit: "text-profit",
    loss: "text-loss",
    warn: "text-warn",
  };
  return (
    <div className="rounded border border-line bg-surface p-3.5">
      <p className="text-2xs font-medium uppercase tracking-wide text-faint">{label}</p>
      <p className={cn("tnum mt-1.5 text-xl font-semibold", colours[tone])}>
        {formatInteger(value)}
      </p>
    </div>
  );
}

/** The raw first rows of the file, so the user can sanity-check the mapping against real values. */
function SamplePreview({ headers, rows }: { headers: string[]; rows: Record<string, string>[] }) {
  if (rows.length === 0) return null;
  return (
    <Card padded={false}>
      <div className="border-b border-line p-4">
        <h2 className="text-sm font-semibold text-ink">First rows of your file</h2>
        <p className="mt-0.5 text-xs text-muted">Shown exactly as they appear in the CSV.</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-line">
              {headers.map((header) => (
                <th key={header} className="whitespace-nowrap px-3 py-2 text-left font-medium text-muted">
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 5).map((row, index) => (
              <tr key={index} className="border-b border-line/60 last:border-0">
                {headers.map((header) => (
                  <td key={header} className="tnum whitespace-nowrap px-3 py-1.5 text-ink">
                    {row[header] ?? ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

const NORMALISED_COLUMNS = [
  "timestamp",
  "symbol",
  "side",
  "quantity",
  "price",
  "commission",
  "fees",
];

function RowTable({
  rows,
  loading,
  showErrors = false,
}: {
  rows: ImportRowPreview[];
  loading?: boolean;
  showErrors?: boolean;
}) {
  if (loading) return <Skeleton className="h-48 rounded" />;
  if (rows.length === 0) {
    return <p className="py-6 text-center text-xs text-faint">Nothing to show.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-line">
            <th className="px-2 py-2 text-left font-medium text-muted">#</th>
            {NORMALISED_COLUMNS.map((column) => (
              <th key={column} className="whitespace-nowrap px-2 py-2 text-left font-medium text-muted">
                {humanise(column)}
              </th>
            ))}
            <th className="px-2 py-2 text-left font-medium text-muted">
              {showErrors ? "Why it was rejected" : "Status"}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.row_number} className="border-b border-line/60 last:border-0">
              <td className="tnum px-2 py-1.5 text-faint">{row.row_number}</td>
              {NORMALISED_COLUMNS.map((column) => (
                <td key={column} className="tnum whitespace-nowrap px-2 py-1.5 text-ink">
                  {row.normalized?.[column] ?? "—"}
                </td>
              ))}
              <td className="px-2 py-1.5">
                {showErrors || row.errors.length > 0 ? (
                  <span className="flex items-start gap-1 text-loss">
                    <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
                    <span>
                      {row.errors.map((error) => error.message).join(" ") || "Rejected"}
                    </span>
                  </span>
                ) : (
                  <Badge tone={row.status === "duplicate" ? "warn" : "profit"}>
                    {humanise(row.status)}
                  </Badge>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
