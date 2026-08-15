"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { api } from "@/lib/api";
import { describeError } from "@/components/ui/feedback";
import { Button, Field, Input } from "@/components/ui/primitives";
import { AuthShell } from "@/components/shell/auth-shell";

export default function ResetPasswordPage() {
  return (
    // useSearchParams needs a Suspense boundary for the static shell to prerender.
    <Suspense fallback={<AuthShell title="Choose a new password">{null}</AuthShell>}>
      <ResetPasswordForm />
    </Suspense>
  );
}

function ResetPasswordForm() {
  const router = useRouter();
  const params = useSearchParams();
  const token = params.get("token") ?? "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");

  const reset = useMutation({
    mutationFn: () =>
      api.action<{ message: string }>("/api/v1/auth/password-reset/confirm", {
        token,
        new_password: password,
      }),
    onSuccess: () => router.replace("/login?reset=1"),
  });

  const mismatch = confirm !== "" && password !== confirm;

  if (!token) {
    return (
      <AuthShell
        title="Link is incomplete"
        subtitle="This reset link is missing its token."
        footer={
          <Link href="/forgot-password" className="text-xs text-accent hover:underline">
            Request a new link
          </Link>
        }
      >
        <p className="text-sm text-muted">
          Open the link from your email exactly as it was sent — some mail clients trim the end of
          long URLs.
        </p>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title="Choose a new password"
      subtitle="Setting it signs you out of every other device."
      footer={
        <Link href="/login" className="text-xs text-muted hover:underline">
          Back to sign in
        </Link>
      }
    >
      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          reset.mutate();
        }}
        className="space-y-4"
      >
        {reset.isError ? (
          <p role="alert" className="rounded border border-loss/30 bg-loss/5 p-2.5 text-xs text-loss">
            {describeError(reset.error).message}
          </p>
        ) : null}

        <Field
          label="New password"
          htmlFor="r-password"
          hint="At least 12 characters."
          required
        >
          <Input
            id="r-password"
            type="password"
            autoComplete="new-password"
            autoFocus
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>

        <Field
          label="Confirm password"
          htmlFor="r-confirm"
          error={mismatch ? "The two passwords do not match." : undefined}
          required
        >
          <Input
            id="r-confirm"
            type="password"
            autoComplete="new-password"
            invalid={mismatch}
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
          />
        </Field>

        <Button
          type="submit"
          variant="primary"
          size="lg"
          className="w-full justify-center"
          loading={reset.isPending}
          disabled={password === "" || mismatch || password !== confirm}
        >
          Set new password
        </Button>
      </form>
    </AuthShell>
  );
}
