"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { useSession } from "@/lib/session";
import { Sidebar } from "@/components/shell/sidebar";
import { Topbar } from "@/components/shell/topbar";
import { Spinner } from "@/components/ui/feedback";
import { cn } from "@/lib/utils";

/**
 * Authenticated shell.
 *
 * The redirect here is a **convenience**, not a security control: every endpoint authorises
 * independently server-side. A user who defeats this check reaches pages that render nothing but
 * 401s.
 */
export default function AppLayout({ children }: { children: ReactNode }) {
  const { isAuthenticated, isLoading } = useSession();
  const router = useRouter();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  useEffect(() => {
    if (!isLoading && !isAuthenticated) router.replace("/login");
  }, [isLoading, isAuthenticated, router]);

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner className="h-5 w-5" />
        <span className="sr-only">Loading your workspace</span>
      </div>
    );
  }

  if (!isAuthenticated) return null;

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar className="hidden lg:flex" />

      {mobileNavOpen ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={() => setMobileNavOpen(false)} aria-hidden />
          <div className="relative h-full w-60" onClick={() => setMobileNavOpen(false)}>
            <Sidebar />
          </div>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar onOpenSidebar={() => setMobileNavOpen(true)} />
        <main className={cn("flex-1 overflow-y-auto bg-canvas p-4 sm:p-6")}>{children}</main>
      </div>
    </div>
  );
}
