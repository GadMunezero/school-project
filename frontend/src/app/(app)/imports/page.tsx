"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { FileUp, Upload } from "lucide-react";

import { api } from "@/lib/api";
import { formatInteger, formatRelative, humanise } from "@/lib/format";
import { queryKeys } from "@/lib/queries";
import type { Account, ImportRecord } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import { Modal } from "@/components/ui/overlay";
import { Badge, Button, Field, Select } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/shell/page-header";

/** Status → badge tone. Committed imports are the only "done" state. */
const STATUS_TONE: Record<string, "neutral" | "accent" | "profit" | "loss" | "warn"> = {
  uploaded: "neutral",
  mapping: "accent",
  validating: "accent",
  preview: "warn",
  importing: "accent",
  completed: "profit",
  failed: "loss",
  reverted: "neutral",
};

export default function ImportsPage() {
  const [uploading, setUploading] = useState(false);

  const imports = useQuery({
    queryKey: queryKeys.imports(),
    queryFn: () => api.list<ImportRecord>("/api/v1/imports", { page_size: 50 }),
  });

  const rows = imports.data?.data ?? [];

  return (
    <>
      <PageHeader
        title="Imports"
        description="Bring in your broker's execution history. Nothing is written to your journal until you review the parsed rows and commit."
        action={
          <Button
            variant="primary"
            icon={<Upload className="h-3.5 w-3.5" />}
            onClick={() => setUploading(true)}
          >
            Upload CSV
          </Button>
        }
      />

      {imports.isError ? (
        <ErrorState error={imports.error} onRetry={() => void imports.refetch()} />
      ) : imports.isLoading ? (
        <Skeleton className="h-40 rounded" />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<FileUp className="h-7 w-7" />}
          title="No imports yet"
          description="Export your fills from your broker as CSV. We detect the columns, show you exactly what will be created, and let you undo the whole import afterwards."
          action={
            <Button variant="primary" onClick={() => setUploading(true)}>
              Upload a CSV
            </Button>
          }
        />
      ) : (
        <Card padded={false}>
          <ul className="divide-y divide-line">
            {rows.map((record) => (
              <li key={record.id}>
                <Link
                  href={`/imports/${record.id}`}
                  className="flex items-center justify-between gap-4 px-4 py-3 transition-colors hover:bg-raised"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-ink">{record.filename}</p>
                    <p className="mt-0.5 text-xs text-muted">
                      {formatInteger(record.total_rows)} rows · uploaded{" "}
                      {formatRelative(record.created_at)}
                      {record.status === "completed"
                        ? ` · created ${formatInteger(record.created_trade_count)} trades`
                        : ""}
                    </p>
                  </div>
                  <span className="flex shrink-0 items-center gap-3">
                    {record.invalid_rows > 0 ? (
                      <span className="text-xs text-warn">
                        {formatInteger(record.invalid_rows)} rejected
                      </span>
                    ) : null}
                    <Badge tone={STATUS_TONE[record.status] ?? "neutral"}>
                      {humanise(record.status)}
                    </Badge>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <UploadModal open={uploading} onClose={() => setUploading(false)} />
    </>
  );
}

function UploadModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const toast = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [accountId, setAccountId] = useState("");
  const [file, setFile] = useState<File | null>(null);

  const accounts = useQuery({
    queryKey: queryKeys.accounts(),
    queryFn: () => api.list<Account>("/api/v1/accounts", { page_size: 100 }),
    enabled: open,
  });

  const upload = useMutation({
    mutationFn: () => {
      const body = new FormData();
      body.append("file", file as File);
      body.append("account_id", accountId);
      return api.upload<ImportRecord>("/api/v1/imports", body);
    },
    onSuccess: (record) => {
      void queryClient.invalidateQueries({ queryKey: ["imports"] });
      onClose();
      router.push(`/imports/${record.id}`);
    },
    onError: (error) => toast.fromError(error, "Could not read that file"),
  });

  // Default to the account marked default, so the common case needs one click.
  const defaultAccount = accounts.data?.data.find((account) => account.is_default);
  const resolvedAccount = accountId || defaultAccount?.id || "";

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Upload a CSV"
      description="We read the header row and suggest a mapping. You confirm it on the next screen."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={upload.isPending}
            disabled={!file || !resolvedAccount}
            onClick={() => upload.mutate()}
          >
            Upload
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="Import into" htmlFor="i-account" required>
          <Select
            id="i-account"
            value={resolvedAccount}
            onChange={(event) => setAccountId(event.target.value)}
          >
            <option value="">Choose an account…</option>
            {(accounts.data?.data ?? []).map((account) => (
              <option key={account.id} value={account.id}>
                {account.name} ({account.currency})
              </option>
            ))}
          </Select>
        </Field>

        <Field label="File" htmlFor="i-file" hint="CSV exported from your broker." required>
          <div className="flex items-center gap-2">
            <input
              ref={inputRef}
              id="i-file"
              type="file"
              accept=".csv,text/csv"
              className="sr-only"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
            <Button variant="outline" onClick={() => inputRef.current?.click()}>
              Choose file
            </Button>
            <span className="truncate text-xs text-muted">
              {file ? file.name : "No file selected"}
            </span>
          </div>
        </Field>
      </div>
    </Modal>
  );
}
