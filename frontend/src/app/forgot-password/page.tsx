"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { api } from "@/lib/api";
import { Button, Field, Input } from "@/components/ui/primitives";
import { AuthShell } from "@/components/shell/auth-shell";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");

  const request = useMutation({
    mutationFn: () => api.action<{ message: string }>("/api/v1/auth/password-reset", { email }),
  });

  return (
    <AuthShell
      title="Reset your password"
      subtitle="We'll email you a link if an account exists."
      footer={
        <Link href="/login" className="text-xs text-muted hover:underline">
          Back to sign in
        </Link>
      }
    >
      {request.isSuccess ? (
        // The message is identical whether or not the address exists — the API is deliberately
        // not an account-enumeration oracle, and the UI must not become one either.
        <p className="rounded border border-line bg-raised p-3 text-sm text-muted">
          {request.data.message}
        </p>
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            request.mutate();
          }}
          className="space-y-4"
        >
          <Field label="Email" htmlFor="email" required>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              required
              autoFocus
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </Field>
          <Button
            type="submit"
            variant="primary"
            size="lg"
            className="w-full justify-center"
            loading={request.isPending}
          >
            Send reset link
          </Button>
        </form>
      )}
    </AuthShell>
  );
}
