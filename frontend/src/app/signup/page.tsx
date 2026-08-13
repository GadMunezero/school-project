"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { ApiError, api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import type { SessionInfo } from "@/lib/types";
import { Button, Field, Input } from "@/components/ui/primitives";
import { AuthShell } from "@/components/shell/auth-shell";

/**
 * Client-side rules mirror the server's policy so a user gets immediate feedback. The server
 * revalidates everything — this is a convenience, not the enforcement point.
 */
const schema = z.object({
  full_name: z.string().min(1, "Enter your name").max(160),
  email: z.string().min(1, "Enter your email address").email("Enter a valid email address"),
  password: z
    .string()
    .min(12, "Use at least 12 characters")
    .max(128)
    .refine((value) => {
      const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^\w\s]/].filter((pattern) => pattern.test(value));
      return classes.length >= 3;
    }, "Combine at least three of: lowercase, uppercase, digits, symbols")
    .refine((value) => new Set(value).size >= 6, "Use at least six different characters"),
  organization_name: z.string().max(120).optional(),
});

type FormValues = z.infer<typeof schema>;

export default function SignupPage() {
  const router = useRouter();
  const queryClient = useQueryClient();

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { full_name: "", email: "", password: "", organization_name: "" },
  });

  const signup = useMutation({
    mutationFn: (values: FormValues) =>
      api.post<SessionInfo>("/api/v1/auth/signup", {
        ...values,
        organization_name: values.organization_name || undefined,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
        accepted_terms: true,
      }),
    onSuccess: (session) => {
      queryClient.setQueryData(queryKeys.session, session);
      router.replace("/dashboard");
    },
    onError: (error) => {
      if (error instanceof ApiError) {
        // Attach field errors from the server to their inputs.
        for (const fieldError of error.fieldErrors) {
          if (fieldError.field in form.getValues()) {
            form.setError(fieldError.field as keyof FormValues, { message: fieldError.message });
          }
        }
        if (error.fieldErrors.length === 0) {
          form.setError(error.status === 409 ? "email" : "root", { message: error.message });
        }
        return;
      }
      form.setError("root", { message: "Could not reach the server. Check your connection." });
    },
  });

  return (
    <AuthShell
      title="Create your workspace"
      subtitle="Start journalling, analysing and backtesting."
      footer={
        <p className="text-xs text-muted">
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-accent hover:underline">
            Sign in
          </Link>
        </p>
      }
    >
      <form
        noValidate
        onSubmit={form.handleSubmit((values) => signup.mutate(values))}
        className="space-y-4"
      >
        {form.formState.errors.root ? (
          <p role="alert" className="rounded border border-loss/30 bg-loss/5 p-2.5 text-xs text-loss">
            {form.formState.errors.root.message}
          </p>
        ) : null}

        <Field label="Full name" htmlFor="full_name" error={form.formState.errors.full_name?.message} required>
          <Input id="full_name" autoComplete="name" autoFocus {...form.register("full_name")} />
        </Field>

        <Field label="Email" htmlFor="email" error={form.formState.errors.email?.message} required>
          <Input id="email" type="email" autoComplete="email" {...form.register("email")} />
        </Field>

        <Field
          label="Password"
          htmlFor="password"
          error={form.formState.errors.password?.message}
          hint="At least 12 characters."
          required
        >
          <Input id="password" type="password" autoComplete="new-password" {...form.register("password")} />
        </Field>

        <Field
          label="Workspace name"
          htmlFor="organization_name"
          error={form.formState.errors.organization_name?.message}
          hint="Optional — defaults to your name."
        >
          <Input id="organization_name" {...form.register("organization_name")} />
        </Field>

        <Button type="submit" variant="primary" size="lg" className="w-full justify-center" loading={signup.isPending}>
          Create account
        </Button>
      </form>
    </AuthShell>
  );
}
