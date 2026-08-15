"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { ApiError } from "@/lib/api";
import { SessionProvider } from "@/lib/session";
import { ThemeProvider } from "@/lib/theme";
import { ToastProvider } from "@/components/ui/toast";

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Trading data changes when the user acts, not on a timer, so refetching on every
            // window focus is noise. Mutations invalidate what they touched instead.
            refetchOnWindowFocus: false,
            staleTime: 30_000,
            retry: (failureCount, error) => {
              // A 4xx will not become a 2xx by asking again.
              if (error instanceof ApiError && error.status < 500) return false;
              return failureCount < 2;
            },
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <SessionProvider>
          <ToastProvider>{children}</ToastProvider>
        </SessionProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
