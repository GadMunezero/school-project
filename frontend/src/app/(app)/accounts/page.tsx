"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Plus, Wallet } from "lucide-react";

import { api } from "@/lib/api";
import { formatMoney, humanise, pnlClass } from "@/lib/format";
import { queryKeys } from "@/lib/queries";
import type { Account } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Card } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import { Modal } from "@/components/ui/overlay";
import { Badge, Button, Checkbox, Field, Input, Select } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/shell/page-header";

const ACCOUNT_TYPES = [
  { value: "live", label: "Live" },
  { value: "paper", label: "Paper" },
  { value: "demo", label: "Demo" },
  { value: "prop_evaluation", label: "Prop evaluation" },
  { value: "prop_funded", label: "Prop funded" },
  { value: "backtest", label: "Backtest" },
];

const CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD"];

export default function AccountsPage() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [creating, setCreating] = useState(false);
  const [includeArchived, setIncludeArchived] = useState(false);

  const accounts = useQuery({
    queryKey: queryKeys.accounts({ include_archived: includeArchived }),
    queryFn: () =>
      api.list<Account>("/api/v1/accounts", {
        include_archived: includeArchived,
        page_size: 100,
      }),
  });

  const [form, setForm] = useState({
    name: "",
    broker: "",
    account_type: "live",
    currency: "USD",
    initial_balance: "0",
    leverage: "1",
    is_default: false,
  });

  const create = useMutation({
    mutationFn: () =>
      api.post<Account>("/api/v1/accounts", {
        name: form.name.trim(),
        broker: form.broker.trim() || null,
        account_type: form.account_type,
        currency: form.currency,
        initial_balance: form.initial_balance.trim() || "0",
        leverage: form.leverage.trim() || "1",
        is_default: form.is_default,
      }),
    onSuccess: (account) => {
      setCreating(false);
      setForm((current) => ({ ...current, name: "", broker: "", initial_balance: "0" }));
      void queryClient.invalidateQueries({ queryKey: ["accounts"] });
      toast.success("Account created", `${account.name} is ready for trades.`);
    },
    onError: (error) => toast.fromError(error, "Could not create the account"),
  });

  const rows = accounts.data?.data ?? [];

  return (
    <>
      <PageHeader
        title="Accounts"
        description="Every trade belongs to an account. Balances are rebuilt from the cash ledger and closed trades — they are never edited directly."
        action={
          <Button
            variant="primary"
            icon={<Plus className="h-3.5 w-3.5" />}
            onClick={() => setCreating(true)}
          >
            New account
          </Button>
        }
      />

      <label className="mb-3 inline-flex items-center gap-2 text-xs text-muted">
        <Checkbox
          checked={includeArchived}
          onChange={(event) => setIncludeArchived(event.target.checked)}
        />
        Show archived accounts
      </label>

      {accounts.isError ? (
        <ErrorState error={accounts.error} onRetry={() => void accounts.refetch()} />
      ) : accounts.isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((key) => (
            <Skeleton key={key} className="h-40 rounded" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<Wallet className="h-7 w-7" />}
          title="No accounts yet"
          description="Create one for each broker account or prop firm evaluation you trade. Keeping them separate keeps their statistics honest."
          action={
            <Button variant="primary" onClick={() => setCreating(true)}>
              Create an account
            </Button>
          }
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {rows.map((account) => (
            <Link key={account.id} href={`/accounts/${account.id}`}>
              <Card className="h-full transition-colors hover:border-accent/50">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h2 className="truncate text-sm font-semibold text-ink">{account.name}</h2>
                    <p className="mt-0.5 truncate text-xs text-muted">
                      {account.broker ?? "No broker recorded"} · {humanise(account.account_type)}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1">
                    {account.is_default ? <Badge tone="accent">Default</Badge> : null}
                    {account.status !== "active" ? (
                      <Badge tone="neutral">{humanise(account.status)}</Badge>
                    ) : null}
                  </div>
                </div>

                <p className="tnum mt-4 text-xl font-semibold text-ink">
                  {formatMoney(account.current_balance, account.currency)}
                </p>
                <p className="text-2xs text-faint">Current balance</p>

                <dl className="mt-3 grid grid-cols-2 gap-2 border-t border-line pt-3 text-xs">
                  <div>
                    <dt className="text-2xs text-faint">Realised P&amp;L</dt>
                    <dd className={cn("tnum font-medium", pnlClass(account.realized_pnl))}>
                      {formatMoney(account.realized_pnl, account.currency, { signed: true })}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-2xs text-faint">Started with</dt>
                    <dd className="tnum font-medium text-muted">
                      {formatMoney(account.initial_balance, account.currency)}
                    </dd>
                  </div>
                </dl>
              </Card>
            </Link>
          ))}
        </div>
      )}

      <Modal
        open={creating}
        onClose={() => setCreating(false)}
        title="New account"
        description="Currency and starting balance are fixed at creation because every later figure is derived from them."
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreating(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={create.isPending}
              disabled={form.name.trim() === ""}
              onClick={() => create.mutate()}
            >
              Create
            </Button>
          </>
        }
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name" htmlFor="a-name" className="sm:col-span-2" required>
            <Input
              id="a-name"
              value={form.name}
              placeholder="Main futures account"
              onChange={(event) => setForm((f) => ({ ...f, name: event.target.value }))}
            />
          </Field>
          <Field label="Broker" htmlFor="a-broker">
            <Input
              id="a-broker"
              value={form.broker}
              onChange={(event) => setForm((f) => ({ ...f, broker: event.target.value }))}
            />
          </Field>
          <Field label="Type" htmlFor="a-type">
            <Select
              id="a-type"
              value={form.account_type}
              onChange={(event) => setForm((f) => ({ ...f, account_type: event.target.value }))}
            >
              {ACCOUNT_TYPES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Currency" htmlFor="a-currency">
            <Select
              id="a-currency"
              value={form.currency}
              onChange={(event) => setForm((f) => ({ ...f, currency: event.target.value }))}
            >
              {CURRENCIES.map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Starting balance" htmlFor="a-balance">
            <Input
              id="a-balance"
              inputMode="decimal"
              value={form.initial_balance}
              onChange={(event) => setForm((f) => ({ ...f, initial_balance: event.target.value }))}
            />
          </Field>
          <Field
            label="Leverage"
            htmlFor="a-leverage"
            hint="Recorded for position sizing; it does not change P&L."
            className="sm:col-span-2"
          >
            <Input
              id="a-leverage"
              inputMode="decimal"
              value={form.leverage}
              onChange={(event) => setForm((f) => ({ ...f, leverage: event.target.value }))}
            />
          </Field>
          <label className="flex items-center gap-2 text-xs text-muted sm:col-span-2">
            <Checkbox
              checked={form.is_default}
              onChange={(event) => setForm((f) => ({ ...f, is_default: event.target.checked }))}
            />
            Make this the default account for new trades
          </label>
        </div>
      </Modal>
    </>
  );
}
