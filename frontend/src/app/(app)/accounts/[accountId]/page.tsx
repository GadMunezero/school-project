"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { Archive, ArrowLeft, Plus, RefreshCw, Trash2 } from "lucide-react";

import { api } from "@/lib/api";
import {
  formatDateTime,
  formatInteger,
  formatMoney,
  formatPercent,
  formatRatio,
  humanise,
} from "@/lib/format";
import { queryKeys } from "@/lib/queries";
import type { Account, AccountDetail, AccountSnapshot, CashTransaction } from "@/lib/types";
import { cn } from "@/lib/utils";
import { SeriesChart } from "@/components/charts/series-chart";
import { Card, CardHeader, MetricCard } from "@/components/ui/card";
import { ErrorState, MetricsSkeleton, Skeleton } from "@/components/ui/feedback";
import { ConfirmDialog, Modal } from "@/components/ui/overlay";
import { Button, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { Tabs } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/shell/page-header";

const CASH_KINDS = [
  { value: "deposit", label: "Deposit" },
  { value: "withdrawal", label: "Withdrawal" },
  { value: "fee", label: "Fee" },
  { value: "interest", label: "Interest" },
  { value: "adjustment", label: "Adjustment" },
  { value: "payout", label: "Payout" },
];

/** Kinds that reduce the balance. Used only to pick a colour — the server owns the sign. */
const OUTFLOWS = new Set(["withdrawal", "fee", "payout"]);

export default function AccountDetailPage() {
  const params = useParams<{ accountId: string }>();
  const accountId = params.accountId;
  const router = useRouter();
  const queryClient = useQueryClient();
  const toast = useToast();

  const [tab, setTab] = useState("overview");
  const [addingCash, setAddingCash] = useState(false);
  const [editing, setEditing] = useState(false);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [page, setPage] = useState(1);

  const detail = useQuery({
    queryKey: queryKeys.account(accountId),
    queryFn: () => api.get<AccountDetail>(`/api/v1/accounts/${accountId}`),
  });

  const transactions = useQuery({
    queryKey: queryKeys.accountTransactions(accountId, page),
    queryFn: () =>
      api.list<CashTransaction>(`/api/v1/accounts/${accountId}/transactions`, {
        page,
        page_size: 25,
      }),
    enabled: tab === "cash",
  });

  const snapshots = useQuery({
    queryKey: ["accounts", accountId, "snapshots"],
    queryFn: () => api.list<AccountSnapshot>(`/api/v1/accounts/${accountId}/snapshots`, { limit: 365 }),
    enabled: tab === "overview",
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["accounts"] });
  };

  const recalculate = useMutation({
    mutationFn: () => api.post<Account>(`/api/v1/accounts/${accountId}/recalculate`),
    onSuccess: () => {
      invalidate();
      toast.success("Balances rebuilt", "Recomputed from the cash ledger and every closed trade.");
    },
    onError: (error) => toast.fromError(error, "Could not rebuild the balances"),
  });

  const archive = useMutation({
    mutationFn: () => api.post<Account>(`/api/v1/accounts/${accountId}/archive`),
    onSuccess: () => {
      setConfirmArchive(false);
      invalidate();
    },
    onError: (error) => toast.fromError(error, "Could not archive the account"),
  });

  const remove = useMutation({
    mutationFn: () => api.delete(`/api/v1/accounts/${accountId}`),
    onSuccess: () => {
      invalidate();
      router.push("/accounts");
    },
    onError: (error) => toast.fromError(error, "Could not delete the account"),
  });

  const account = detail.data?.account;
  const stats = detail.data?.stats;

  if (detail.isError) {
    return (
      <>
        <PageHeader title="Account" />
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      </>
    );
  }

  if (detail.isLoading || !account || !stats) {
    return (
      <>
        <PageHeader title="Account" />
        <div className="space-y-4">
          <MetricsSkeleton />
          <Skeleton className="h-72 rounded" />
        </div>
      </>
    );
  }

  const currency = account.currency;

  return (
    <>
      <PageHeader
        title={account.name}
        description={`${account.broker ?? "No broker recorded"} · ${humanise(account.account_type)} · ${currency}`}
        action={
          <>
            <Link href="/accounts">
              <Button variant="ghost" icon={<ArrowLeft className="h-3.5 w-3.5" />}>
                Back
              </Button>
            </Link>
            <Button
              variant="outline"
              icon={<RefreshCw className="h-3.5 w-3.5" />}
              loading={recalculate.isPending}
              onClick={() => recalculate.mutate()}
            >
              Rebuild balances
            </Button>
            <Button variant="outline" onClick={() => setEditing(true)}>
              Edit
            </Button>
            {account.status === "active" ? (
              <Button
                variant="ghost"
                icon={<Archive className="h-3.5 w-3.5" />}
                onClick={() => setConfirmArchive(true)}
              >
                Archive
              </Button>
            ) : null}
            <Button
              variant="ghost"
              icon={<Trash2 className="h-3.5 w-3.5" />}
              onClick={() => setConfirmDelete(true)}
            >
              Delete
            </Button>
          </>
        }
      />

      {account.status !== "active" ? (
        <p className="mb-4 rounded border border-warn/30 bg-warn/5 p-3 text-xs text-muted">
          This account is {humanise(account.status).toLowerCase()}. It stays in your analytics but no
          longer accepts new trades.
        </p>
      ) : null}

      <section className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          label="Equity"
          value={formatMoney(stats.equity, currency)}
          hint={
            stats.unrealized_pnl === null
              ? "No open marks available"
              : `Balance plus ${formatMoney(stats.unrealized_pnl, currency, { signed: true })} open`
          }
        />
        <MetricCard
          label="Balance"
          value={formatMoney(account.current_balance, currency)}
          hint={`Started at ${formatMoney(account.initial_balance, currency)}`}
        />
        <MetricCard
          label="Net P&L"
          tone="pnl"
          raw={stats.net_pnl}
          value={formatMoney(stats.net_pnl, currency, { signed: true })}
          hint={`${formatInteger(stats.closed_trade_count)} closed trades`}
        />
        <MetricCard
          label="Win rate"
          value={formatPercent(stats.win_rate)}
          hint={`Profit factor ${formatRatio(stats.profit_factor)}`}
        />
      </section>

      <Tabs
        active={tab}
        onChange={setTab}
        items={[
          { id: "overview", label: "Overview" },
          { id: "cash", label: "Cash ledger" },
          { id: "settings", label: "Details" },
        ]}
      />

      {tab === "overview" ? (
        <div className="mt-4 space-y-4">
          <Card>
            <CardHeader
              title="Daily equity"
              description="One snapshot per trading day, taken at the close."
            />
            {snapshots.isLoading ? (
              <Skeleton className="h-64 rounded" />
            ) : (
              <SeriesChart
                height={260}
                baseline={account.initial_balance}
                emptyMessage="No daily snapshots yet. They are written as trades close."
                points={(snapshots.data?.data ?? []).map((row) => ({
                  timestamp: `${row.as_of_date}T00:00:00Z`,
                  value: row.equity,
                }))}
              />
            )}
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Cash flow" />
              <dl className="space-y-2">
                <Row label="Deposits" value={formatMoney(account.total_deposits, currency)} />
                <Row label="Withdrawals" value={formatMoney(account.total_withdrawals, currency)} />
                <Row label="Commission paid" value={formatMoney(account.total_commission, currency)} />
                <Row label="Other fees" value={formatMoney(account.total_fees, currency)} />
              </dl>
            </Card>

            <Card>
              <CardHeader title="Activity" />
              <dl className="space-y-2">
                <Row label="Open trades" value={formatInteger(stats.open_trade_count)} />
                <Row label="Closed trades" value={formatInteger(stats.closed_trade_count)} />
                <Row label="First trade" value={formatDateTime(stats.first_trade_at)} />
                <Row label="Last trade" value={formatDateTime(stats.last_trade_at)} />
                <Row
                  label="Balances last rebuilt"
                  value={formatDateTime(account.last_recalculated_at, "Never")}
                />
              </dl>
            </Card>
          </div>
        </div>
      ) : null}

      {tab === "cash" ? (
        <div className="mt-4">
          <Card padded={false}>
            <div className="flex items-center justify-between border-b border-line p-4">
              <div>
                <h2 className="text-sm font-semibold text-ink">Cash ledger</h2>
                <p className="mt-0.5 text-xs text-muted">
                  Deposits, withdrawals and adjustments. Amounts are entered positive — the sign
                  comes from the kind, so a withdrawal can never be recorded as a credit.
                </p>
              </div>
              <Button
                variant="primary"
                icon={<Plus className="h-3.5 w-3.5" />}
                onClick={() => setAddingCash(true)}
              >
                Record
              </Button>
            </div>

            {transactions.isLoading ? (
              <div className="p-4">
                <Skeleton className="h-40 rounded" />
              </div>
            ) : (transactions.data?.data.length ?? 0) === 0 ? (
              <p className="p-8 text-center text-xs text-faint">
                No cash movements recorded on this account.
              </p>
            ) : (
              <ul className="divide-y divide-line">
                {transactions.data?.data.map((transaction) => (
                  <li key={transaction.id} className="flex items-center justify-between gap-3 px-4 py-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-ink">{humanise(transaction.kind)}</p>
                      <p className="truncate text-xs text-muted">
                        {formatDateTime(transaction.occurred_at)}
                        {transaction.description ? ` · ${transaction.description}` : ""}
                      </p>
                    </div>
                    <span className="flex items-center gap-3">
                      <span
                        className={cn(
                          "tnum text-sm font-medium",
                          OUTFLOWS.has(transaction.kind) ? "text-loss" : "text-profit",
                        )}
                      >
                        {OUTFLOWS.has(transaction.kind) ? "−" : "+"}
                        {formatMoney(transaction.amount, transaction.currency)}
                      </span>
                      <DeleteTransaction accountId={accountId} transactionId={transaction.id} />
                    </span>
                  </li>
                ))}
              </ul>
            )}

            {transactions.data?.meta && transactions.data.meta.total_pages > 1 ? (
              <div className="flex items-center justify-between border-t border-line px-4 py-2 text-xs text-muted">
                <span>
                  Page {transactions.data.meta.page} of {transactions.data.meta.total_pages}
                </span>
                <span className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page <= 1}
                    onClick={() => setPage((current) => current - 1)}
                  >
                    Previous
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!transactions.data.meta.has_next}
                    onClick={() => setPage((current) => current + 1)}
                  >
                    Next
                  </Button>
                </span>
              </div>
            ) : null}
          </Card>
        </div>
      ) : null}

      {tab === "settings" ? (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader title="Configuration" />
            <dl className="space-y-2">
              <Row label="Type" value={humanise(account.account_type)} />
              <Row label="Currency" value={currency} />
              <Row label="Leverage" value={`${account.leverage ?? "1"}×`} />
              <Row label="Timezone" value={account.timezone} />
              <Row label="Commission model" value={humanise(account.commission_model)} />
              <Row
                label="Default risk"
                value={
                  account.default_risk_percent === null
                    ? "Not set"
                    : formatPercent(account.default_risk_percent)
                }
              />
              <Row label="Broker reference" value={account.external_reference ?? "—"} />
              <Row label="Created" value={formatDateTime(account.created_at)} />
            </dl>
          </Card>
          <Card>
            <CardHeader title="Notes" />
            <p className="whitespace-pre-wrap text-sm text-muted">
              {account.notes?.trim() ? account.notes : "No notes on this account."}
            </p>
          </Card>
        </div>
      ) : null}

      <CashModal
        accountId={accountId}
        currency={currency}
        open={addingCash}
        onClose={() => setAddingCash(false)}
      />

      <EditModal account={account} open={editing} onClose={() => setEditing(false)} />

      <ConfirmDialog
        open={confirmArchive}
        onClose={() => setConfirmArchive(false)}
        onConfirm={() => archive.mutate()}
        loading={archive.isPending}
        confirmLabel="Archive"
        title="Archive this account?"
        message="Its trades stay in your journal and analytics, but the account stops appearing when you record something new. You can reactivate it later."
      />

      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => remove.mutate()}
        loading={remove.isPending}
        destructive
        confirmLabel="Delete account"
        title="Delete this account?"
        message="This is permanent. If the account has trades the server will refuse — archive it instead."
      />
    </>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="tnum text-sm font-medium text-ink">{value}</dd>
    </div>
  );
}

function DeleteTransaction({ accountId, transactionId }: { accountId: string; transactionId: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [confirming, setConfirming] = useState(false);

  const remove = useMutation({
    mutationFn: () => api.delete(`/api/v1/accounts/${accountId}/transactions/${transactionId}`),
    onSuccess: () => {
      setConfirming(false);
      void queryClient.invalidateQueries({ queryKey: ["accounts"] });
    },
    onError: (error) => toast.fromError(error, "Could not delete the transaction"),
  });

  return (
    <>
      <button
        type="button"
        aria-label="Delete transaction"
        onClick={() => setConfirming(true)}
        className="rounded p-1 text-faint transition-colors hover:bg-raised hover:text-loss"
      >
        <Trash2 className="h-3.5 w-3.5" aria-hidden />
      </button>
      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={() => remove.mutate()}
        loading={remove.isPending}
        destructive
        confirmLabel="Delete"
        title="Delete this transaction?"
        message="The account balance is recalculated from the remaining ledger entries."
      />
    </>
  );
}

function CashModal({
  accountId,
  currency,
  open,
  onClose,
}: {
  accountId: string;
  currency: string;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [kind, setKind] = useState("deposit");
  const [amount, setAmount] = useState("");
  const [occurredAt, setOccurredAt] = useState(() => new Date().toISOString().slice(0, 16));
  const [description, setDescription] = useState("");

  const create = useMutation({
    mutationFn: () =>
      api.post<CashTransaction>(`/api/v1/accounts/${accountId}/transactions`, {
        kind,
        amount: amount.trim(),
        occurred_at: new Date(occurredAt).toISOString(),
        description: description.trim() || null,
      }),
    onSuccess: () => {
      setAmount("");
      setDescription("");
      void queryClient.invalidateQueries({ queryKey: ["accounts"] });
      onClose();
    },
    onError: (error) => toast.fromError(error, "Could not record the transaction"),
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Record a cash movement"
      description={`Amounts are in ${currency} and always entered as a positive number.`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={create.isPending}
            disabled={amount.trim() === "" || occurredAt === ""}
            onClick={() => create.mutate()}
          >
            Record
          </Button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Kind" htmlFor="c-kind">
          <Select id="c-kind" value={kind} onChange={(event) => setKind(event.target.value)}>
            {CASH_KINDS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={`Amount (${currency})`} htmlFor="c-amount" required>
          <Input
            id="c-amount"
            inputMode="decimal"
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
          />
        </Field>
        <Field label="Occurred at" htmlFor="c-when" className="sm:col-span-2" required>
          <Input
            id="c-when"
            type="datetime-local"
            value={occurredAt}
            onChange={(event) => setOccurredAt(event.target.value)}
          />
        </Field>
        <Field label="Description" htmlFor="c-note" className="sm:col-span-2">
          <Input
            id="c-note"
            value={description}
            placeholder="Monthly funding"
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
      </div>
    </Modal>
  );
}

function EditModal({
  account,
  open,
  onClose,
}: {
  account: Account;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState({
    name: account.name,
    broker: account.broker ?? "",
    default_risk_percent: account.default_risk_percent ?? "",
    external_reference: account.external_reference ?? "",
    notes: account.notes ?? "",
    status: account.status,
  });

  const save = useMutation({
    mutationFn: () =>
      api.patch<Account>(`/api/v1/accounts/${account.id}`, {
        name: form.name.trim(),
        broker: form.broker.trim() || null,
        default_risk_percent: form.default_risk_percent.trim() || null,
        external_reference: form.external_reference.trim() || null,
        notes: form.notes.trim() || null,
        status: form.status,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["accounts"] });
      onClose();
    },
    onError: (error) => toast.fromError(error, "Could not save the account"),
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Edit account"
      description="Currency and starting balance are not editable here — changing them would rewrite every derived figure."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={save.isPending}
            disabled={form.name.trim() === ""}
            onClick={() => save.mutate()}
          >
            Save
          </Button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name" htmlFor="e-name" className="sm:col-span-2" required>
          <Input
            id="e-name"
            value={form.name}
            onChange={(event) => setForm((f) => ({ ...f, name: event.target.value }))}
          />
        </Field>
        <Field label="Broker" htmlFor="e-broker">
          <Input
            id="e-broker"
            value={form.broker}
            onChange={(event) => setForm((f) => ({ ...f, broker: event.target.value }))}
          />
        </Field>
        <Field label="Status" htmlFor="e-status">
          <Select
            id="e-status"
            value={form.status}
            onChange={(event) =>
              setForm((f) => ({ ...f, status: event.target.value as Account["status"] }))
            }
          >
            <option value="active">Active</option>
            <option value="archived">Archived</option>
            <option value="closed">Closed</option>
          </Select>
        </Field>
        <Field
          label="Default risk per trade (%)"
          htmlFor="e-risk"
          hint="Used to pre-fill position sizing."
        >
          <Input
            id="e-risk"
            inputMode="decimal"
            value={form.default_risk_percent}
            onChange={(event) =>
              setForm((f) => ({ ...f, default_risk_percent: event.target.value }))
            }
          />
        </Field>
        <Field label="Broker reference" htmlFor="e-ref">
          <Input
            id="e-ref"
            value={form.external_reference}
            onChange={(event) => setForm((f) => ({ ...f, external_reference: event.target.value }))}
          />
        </Field>
        <Field label="Notes" htmlFor="e-notes" className="sm:col-span-2">
          <Textarea
            id="e-notes"
            rows={4}
            value={form.notes}
            onChange={(event) => setForm((f) => ({ ...f, notes: event.target.value }))}
          />
        </Field>
      </div>
    </Modal>
  );
}
