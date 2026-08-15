import type { ReactNode } from "react";

/** Centred card used by every unauthenticated page. */
export function AuthShell({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas p-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-2">
          <span
            aria-hidden
            className="flex h-8 w-8 items-center justify-center rounded bg-accent text-sm font-bold text-accent-ink"
          >
            T
          </span>
          <span className="text-base font-semibold tracking-tight">Tradeloom</span>
        </div>

        <div className="rounded border border-line bg-surface p-6 shadow-card">
          <h1 className="text-base font-semibold text-ink">{title}</h1>
          {subtitle ? <p className="mt-1 text-sm text-muted">{subtitle}</p> : null}
          <div className="mt-5">{children}</div>
        </div>

        {footer ? <div className="mt-4 text-center">{footer}</div> : null}
      </div>
    </div>
  );
}
