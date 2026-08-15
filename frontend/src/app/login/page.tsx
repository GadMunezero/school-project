"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { ApiError, api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import { useSession } from "@/lib/session";
import type { SessionInfo } from "@/lib/types";
import { Button, Field, Input } from "@/components/ui/primitives";
import { AuthShell } from "@/components/shell/auth-shell";

const schema = z.object({
  email: z.string().min(1, "Enter your email address").email("Enter a valid email address"),
  password: z.string().min(1, "Enter your password"),
});

type FormValues = z.infer<typeof schema>;

export default function LoginPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { isAuthenticated } = useSession();

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: "", password: "" },
  });

  useEffect(() => {
    if (isAuthenticated) router.replace("/dashboard");
  }, [isAuthenticated, router]);

  const login = useMutation({
    mutationFn: (values: FormValues) => api.post<SessionInfo>("/api/v1/auth/login", values),
    onSuccess: (session) => {
      queryClient.setQueryData(queryKeys.session, session);
      router.replace("/dashboard");
    },
    onError: (error) => {
      if (error instanceof ApiError) {
        // The API deliberately returns the same message for a wrong password and an unknown
        // account, so both land on the password field rather than revealing which was wrong.
        form.setError("password", { message: error.message });
        return;
      }
      form.setError("root", { message: "Could not reach the server. Check your connection." });
    },
  });

  return (
    <AuthShell
      title="Sign in"
      subtitle="Continue to your trading workspace."
      footer={
        <p className="text-xs text-muted">
          No account?{" "}
          <Link href="/signup" className="font-medium text-accent hover:underline">
            Create one
          </Link>
        </p>
      }
    >
      <form
        noValidate
        onSubmit={form.handleSubmit((values) => login.mutate(values))}
        className="space-y-4"
      >
        {form.formState.errors.root ? (
          <p role="alert" className="rounded border border-loss/30 bg-loss/5 p-2.5 text-xs text-loss">
            {form.formState.errors.root.message}
          </p>
        ) : null}

        <Field label="Email" htmlFor="email" error={form.formState.errors.email?.message} required>
          <Input
            id="email"
            type="email"
            autoComplete="email"
            autoFocus
            invalid={Boolean(form.formState.errors.email)}
            {...form.register("email")}
          />
        </Field>

        <Field
          label="Password"
          htmlFor="password"
          error={form.formState.errors.password?.message}
          required
        >
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            invalid={Boolean(form.formState.errors.password)}
            {...form.register("password")}
          />
        </Field>

        <Button type="submit" variant="primary" size="lg" className="w-full justify-center" loading={login.isPending}>
          Sign in
        </Button>

        <p className="text-center text-xs text-muted">
          <Link href="/forgot-password" className="hover:underline">
            Forgot your password?
          </Link>
        </p>
      </form>
    </AuthShell>
  );
}
