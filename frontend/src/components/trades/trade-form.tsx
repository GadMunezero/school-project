"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { ApiError, api } from "@/lib/api";
import type { Account, Setup, Strategy, Tag, Trade } from "@/lib/types";
import { Button, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { Modal } from "@/components/ui/overlay";
import { useToast } from "@/components/ui/toast";

/**
 * Record a trade.
 *
 * Prices and quantities are kept as **strings** all the way to the API. Parsing them to a number
 * here — even briefly — would round a value like 0.1 before the backend ever sees it.
 */
const decimalString = (label: string) =>
  z
    .string()
    .min(1, `Enter a ${label}`)
    .refine((value) => /^\d+(\.\d+)?$/.test(value.trim()), `${label} must be a positive number`);

const optionalDecimal = z
  .string()
  .optional()
  .refine((value) => !value || /^\d+(\.\d+)?$/.test(value.trim()), "Must be a positive number");

const schema = z
  .object({
    account_id: z.string().min(1, "Choose an account"),
    symbol: z.string().min(1, "Enter a symbol").max(40),
    asset_type: z.string().min(1),
    direction: z.enum(["long", "short"]),
    entry_timestamp: z.string().min(1, "Enter the entry time"),
    entry_price: decimalString("entry price"),
    quantity: decimalString("quantity"),
    exit_timestamp: z.string().optional(),
    exit_price: optionalDecimal,
    stop_loss: optionalDecimal,
    take_profit: optionalDecimal,
    commission: optionalDecimal,
    fees: optionalDecimal,
    strategy_id: z.string().optional(),
    setup_id: z.string().optional(),
    notes: z.string().optional(),
  })
  .refine((values) => Boolean(values.exit_price) === Boolean(values.exit_timestamp), {
    message: "An exit needs both a price and a time",
    path: ["exit_price"],
  });

type FormValues = z.infer<typeof schema>;

function toIsoOrUndefined(value: string | undefined): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

export function TradeFormDialog({
  open,
  onClose,
  accounts,
  strategies,
  setups,
  tags,
}: {
  open: boolean;
  onClose: () => void;
  accounts: Account[];
  strategies: Strategy[];
  setups: Setup[];
  tags: Tag[];
}) {
  const queryClient = useQueryClient();
  const toast = useToast();

  const defaultAccount = accounts.find((account) => account.is_default) ?? accounts[0];

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      account_id: defaultAccount?.id ?? "",
      symbol: "",
      asset_type: "equity",
      direction: "long",
      entry_timestamp: "",
      entry_price: "",
      quantity: "",
      exit_timestamp: "",
      exit_price: "",
      stop_loss: "",
      take_profit: "",
      commission: "",
      fees: "",
      strategy_id: "",
      setup_id: "",
      notes: "",
    },
  });

  const create = useMutation({
    mutationFn: (values: FormValues) =>
      api.post<Trade[]>("/api/v1/trades", {
        account_id: values.account_id,
        symbol: values.symbol.trim().toUpperCase(),
        asset_type: values.asset_type,
        direction: values.direction,
        entry_timestamp: toIsoOrUndefined(values.entry_timestamp),
        entry_price: values.entry_price,
        quantity: values.quantity,
        exit_timestamp: toIsoOrUndefined(values.exit_timestamp),
        exit_price: values.exit_price || undefined,
        stop_loss: values.stop_loss || undefined,
        take_profit: values.take_profit || undefined,
        commission: values.commission || "0",
        fees: values.fees || "0",
        strategy_id: values.strategy_id || undefined,
        setup_id: values.setup_id || undefined,
        notes: values.notes || undefined,
      }),
    onSuccess: () => {
      toast.success("Trade recorded.");
      form.reset();
      onClose();
      void queryClient.invalidateQueries({ queryKey: ["trades"] });
      void queryClient.invalidateQueries({ queryKey: ["analytics"] });
      void queryClient.invalidateQueries({ queryKey: ["accounts"] });
    },
    onError: (error) => {
      if (error instanceof ApiError) {
        for (const fieldError of error.fieldErrors) {
          const field = fieldError.field as keyof FormValues;
          if (field in form.getValues()) form.setError(field, { message: fieldError.message });
        }
        if (error.fieldErrors.length === 0) toast.fromError(error, "Could not record the trade");
        return;
      }
      toast.fromError(error, "Could not record the trade");
    },
  });

  const errors = form.formState.errors;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Record a trade"
      description="Leave the exit blank to track an open position."
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={create.isPending}
            onClick={() => void form.handleSubmit((values) => create.mutate(values))()}
          >
            Save trade
          </Button>
        </>
      }
    >
      {accounts.length === 0 ? (
        <p className="rounded border border-warn/30 bg-warn/5 p-3 text-sm text-muted">
          Create a trading account first — a trade has to belong to one.
        </p>
      ) : (
        <form className="grid gap-3 sm:grid-cols-2" noValidate>
          <Field label="Account" htmlFor="account_id" error={errors.account_id?.message} required>
            <Select id="account_id" {...form.register("account_id")}>
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Symbol" htmlFor="symbol" error={errors.symbol?.message} required>
            <Input id="symbol" placeholder="NVLX" autoCapitalize="characters" {...form.register("symbol")} />
          </Field>

          <Field label="Asset type" htmlFor="asset_type">
            <Select id="asset_type" {...form.register("asset_type")}>
              {["equity", "etf", "futures", "option", "forex", "crypto", "cfd", "index"].map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Direction" htmlFor="direction" required>
            <Select id="direction" {...form.register("direction")}>
              <option value="long">Long</option>
              <option value="short">Short</option>
            </Select>
          </Field>

          <Field label="Entry time" htmlFor="entry_timestamp" error={errors.entry_timestamp?.message} required>
            <Input id="entry_timestamp" type="datetime-local" {...form.register("entry_timestamp")} />
          </Field>

          <Field label="Entry price" htmlFor="entry_price" error={errors.entry_price?.message} required>
            <Input id="entry_price" inputMode="decimal" placeholder="118.40" {...form.register("entry_price")} />
          </Field>

          <Field label="Quantity" htmlFor="quantity" error={errors.quantity?.message} required>
            <Input id="quantity" inputMode="decimal" placeholder="100" {...form.register("quantity")} />
          </Field>

          <Field label="Exit time" htmlFor="exit_timestamp" error={errors.exit_timestamp?.message}>
            <Input id="exit_timestamp" type="datetime-local" {...form.register("exit_timestamp")} />
          </Field>

          <Field label="Exit price" htmlFor="exit_price" error={errors.exit_price?.message}>
            <Input id="exit_price" inputMode="decimal" {...form.register("exit_price")} />
          </Field>

          <Field
            label="Stop loss"
            htmlFor="stop_loss"
            error={errors.stop_loss?.message}
            hint="Sets the risk used for R."
          >
            <Input id="stop_loss" inputMode="decimal" {...form.register("stop_loss")} />
          </Field>

          <Field label="Take profit" htmlFor="take_profit" error={errors.take_profit?.message}>
            <Input id="take_profit" inputMode="decimal" {...form.register("take_profit")} />
          </Field>

          <Field label="Commission" htmlFor="commission" error={errors.commission?.message}>
            <Input id="commission" inputMode="decimal" placeholder="0" {...form.register("commission")} />
          </Field>

          <Field label="Fees" htmlFor="fees" error={errors.fees?.message}>
            <Input id="fees" inputMode="decimal" placeholder="0" {...form.register("fees")} />
          </Field>

          <Field label="Strategy" htmlFor="strategy_id">
            <Select id="strategy_id" {...form.register("strategy_id")}>
              <option value="">None</option>
              {strategies.map((strategy) => (
                <option key={strategy.id} value={strategy.id}>
                  {strategy.name}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Setup" htmlFor="setup_id">
            <Select id="setup_id" {...form.register("setup_id")}>
              <option value="">None</option>
              {setups.map((setup) => (
                <option key={setup.id} value={setup.id}>
                  {setup.name}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Notes" htmlFor="notes" className="sm:col-span-2">
            <Textarea id="notes" rows={3} placeholder="What did you see? What did you do?" {...form.register("notes")} />
          </Field>
        </form>
      )}
    </Modal>
  );
}
