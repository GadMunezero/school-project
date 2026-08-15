"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef } from "react";
import { CheckCircle2, XCircle } from "lucide-react";

import { api } from "@/lib/api";
import { describeError, Spinner } from "@/components/ui/feedback";
import { Button } from "@/components/ui/primitives";
import { AuthShell } from "@/components/shell/auth-shell";

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={<AuthShell title="Confirming your email">{null}</AuthShell>}>
      <VerifyEmail />
    </Suspense>
  );
}

function VerifyEmail() {
  const params = useSearchParams();
  const token = params.get("token") ?? "";
  const attempted = useRef(false);

  const verify = useMutation({
    mutationFn: () => api.action<{ message: string }>("/api/v1/auth/verify-email", { token }),
  });

  // Tokens are single-use, so fire exactly once even under React's double-invoked effects.
  useEffect(() => {
    if (!token || attempted.current) return;
    attempted.current = true;
    verify.mutate();
  }, [token, verify]);

  if (!token) {
    return (
      <AuthShell title="Link is incomplete" subtitle="This verification link is missing its token.">
        <p className="text-sm text-muted">
          Open the link from your email exactly as it was sent, or sign in and request a new one.
        </p>
        <Link href="/login" className="mt-4 block">
          <Button variant="primary" className="w-full justify-center">
            Go to sign in
          </Button>
        </Link>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Confirming your email">
      {verify.isPending || verify.isIdle ? (
        <p className="flex items-center gap-2 text-sm text-muted">
          <Spinner className="h-4 w-4" />
          Checking your link…
        </p>
      ) : verify.isSuccess ? (
        <div className="space-y-4">
          <p className="flex items-start gap-2 text-sm text-ink">
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-profit" aria-hidden />
            {verify.data.message}
          </p>
          <Link href="/dashboard">
            <Button variant="primary" className="w-full justify-center">
              Continue to your dashboard
            </Button>
          </Link>
        </div>
      ) : (
        <div className="space-y-4">
          <p className="flex items-start gap-2 text-sm text-ink">
            <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-loss" aria-hidden />
            {describeError(verify.error).message}
          </p>
          <p className="text-xs text-muted">
            Verification links expire. Sign in and we will send you a fresh one.
          </p>
          <Link href="/login">
            <Button variant="outline" className="w-full justify-center">
              Go to sign in
            </Button>
          </Link>
        </div>
      )}
    </AuthShell>
  );
}
