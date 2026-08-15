"use client";

import { useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";

import { ApiError, api } from "./api";
import type { Entitlements, SessionInfo } from "./types";

/**
 * Session state.
 *
 * The single source of truth is `GET /auth/session` — the server decides who you are, which
 * workspace you are in, and what your plan includes. Nothing here is stored in localStorage,
 * because anything the client could write, the client could forge.
 */

interface SessionContextValue {
  session: SessionInfo | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  error: unknown;
  refetch: () => void;
  /** Server-resolved entitlements. Used only to disable controls, never to authorise. */
  entitlements: Partial<Entitlements>;
  hasFeature: (feature: string) => boolean;
  clear: () => void;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export const sessionQueryKey = ["session"] as const;

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();

  const query: UseQueryResult<SessionInfo | null> = useQuery({
    queryKey: sessionQueryKey,
    queryFn: async () => {
      try {
        return await api.get<SessionInfo>("/api/v1/auth/session");
      } catch (error) {
        // Not signed in is a normal state, not an error to surface.
        if (error instanceof ApiError && error.isUnauthenticated) return null;
        throw error;
      }
    },
    retry: (failureCount, error) => {
      if (error instanceof ApiError && error.status < 500) return false;
      return failureCount < 2;
    },
    staleTime: 60_000,
  });

  const clear = useCallback(() => {
    queryClient.setQueryData(sessionQueryKey, null);
    queryClient.clear();
  }, [queryClient]);

  const value = useMemo<SessionContextValue>(() => {
    const session = query.data ?? null;
    const entitlements = session?.entitlements ?? {};
    return {
      session,
      isLoading: query.isLoading,
      isAuthenticated: Boolean(session),
      error: query.error,
      refetch: () => void query.refetch(),
      entitlements,
      hasFeature: (feature: string) => entitlements.limits?.features?.includes(feature) ?? false,
      clear,
    };
  }, [query, clear]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSession must be used inside <SessionProvider>");
  return context;
}

/** The active workspace's currency, used for every money format on screen. */
export function useCurrency(): string {
  const { session } = useSession();
  return session?.active_organization?.base_currency ?? "USD";
}
