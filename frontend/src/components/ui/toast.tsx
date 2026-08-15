"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { CheckCircle2, Info, X, XCircle, AlertTriangle } from "lucide-react";

import { cn } from "@/lib/utils";
import { describeError } from "./feedback";

type ToastTone = "success" | "error" | "info" | "warning";

interface Toast {
  id: number;
  tone: ToastTone;
  title: string;
  description?: string;
}

interface ToastApi {
  success: (title: string, description?: string) => void;
  error: (title: string, description?: string) => void;
  info: (title: string, description?: string) => void;
  warning: (title: string, description?: string) => void;
  /** Surface a thrown error using the server's own message. */
  fromError: (error: unknown, fallback?: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

const ICONS: Record<ToastTone, ReactNode> = {
  success: <CheckCircle2 className="h-4 w-4 text-profit" aria-hidden />,
  error: <XCircle className="h-4 w-4 text-loss" aria-hidden />,
  info: <Info className="h-4 w-4 text-info" aria-hidden />,
  warning: <AlertTriangle className="h-4 w-4 text-warn" aria-hidden />,
};

let nextId = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const push = useCallback(
    (tone: ToastTone, title: string, description?: string) => {
      const id = (nextId += 1);
      const toast: Toast = description ? { id, tone, title, description } : { id, tone, title };
      setToasts((current) => [...current, toast]);
      // Errors linger; confirmations get out of the way.
      window.setTimeout(() => dismiss(id), tone === "error" ? 8000 : 4000);
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(
    () => ({
      success: (title, description) => push("success", title, description),
      error: (title, description) => push("error", title, description),
      info: (title, description) => push("info", title, description),
      warning: (title, description) => push("warning", title, description),
      fromError: (error, fallback) => {
        const { message, requestId } = describeError(error);
        push("error", fallback ?? "That didn't work", requestId ? `${message} (ref ${requestId})` : message);
      },
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      {/* aria-live so a screen reader announces results of an action it did not navigate to. */}
      <div
        aria-live="polite"
        aria-atomic="false"
        className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-full max-w-sm flex-col gap-2"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={cn(
              "pointer-events-auto flex animate-fade-in items-start gap-3 rounded border border-line",
              "bg-surface p-3 shadow-pop",
            )}
          >
            <div className="mt-0.5">{ICONS[toast.tone]}</div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-ink">{toast.title}</p>
              {toast.description ? (
                <p className="mt-0.5 break-words text-xs text-muted">{toast.description}</p>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() => dismiss(toast.id)}
              aria-label="Dismiss notification"
              className="rounded p-0.5 text-faint transition-colors hover:text-ink"
            >
              <X className="h-3.5 w-3.5" aria-hidden />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside <ToastProvider>");
  return context;
}
