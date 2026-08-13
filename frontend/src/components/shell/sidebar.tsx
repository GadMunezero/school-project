"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  BookOpen,
  FlaskConical,
  Gauge,
  Landmark,
  LineChart,
  PlayCircle,
  Settings,
  Shield,
  Upload,
  Wallet,
} from "lucide-react";
import type { ComponentType } from "react";

import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  /** Feature key from the plan's entitlements; the link is shown but marked when unavailable. */
  feature?: string;
  adminOnly?: boolean;
}

const PRIMARY: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: Gauge },
  { href: "/journal", label: "Journal", icon: BookOpen },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
];

const RESEARCH: NavItem[] = [
  { href: "/strategies", label: "Strategies", icon: LineChart },
  { href: "/backtester", label: "Backtester", icon: FlaskConical, feature: "backtesting" },
  { href: "/replay", label: "Replay", icon: PlayCircle, feature: "replay" },
];

const MANAGE: NavItem[] = [
  { href: "/accounts", label: "Accounts", icon: Wallet },
  { href: "/imports", label: "Imports", icon: Upload },
  { href: "/billing", label: "Billing", icon: Landmark },
  { href: "/settings", label: "Settings", icon: Settings },
];

function NavSection({ title, items }: { title: string; items: NavItem[] }) {
  const pathname = usePathname();
  const { hasFeature } = useSession();

  return (
    <div className="space-y-1">
      <p className="px-3 pb-1 pt-4 text-2xs font-semibold uppercase tracking-wide text-faint">
        {title}
      </p>
      {items.map((item) => {
        const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
        const locked = item.feature ? !hasFeature(item.feature) : false;
        const Icon = item.icon;

        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-2.5 rounded px-3 py-1.5 text-sm transition-colors",
              active ? "bg-accent/10 font-medium text-accent" : "text-muted hover:bg-raised hover:text-ink",
            )}
          >
            <Icon className="h-4 w-4 shrink-0" />
            <span className="flex-1 truncate">{item.label}</span>
            {/* The link still works: the server returns 402 with an upgrade path, which the page
                renders. Hiding it entirely would make the feature undiscoverable. */}
            {locked ? <span className="text-2xs text-faint">Pro</span> : null}
          </Link>
        );
      })}
    </div>
  );
}

export function Sidebar({ className }: { className?: string }) {
  const { session } = useSession();
  const isAdmin = session?.user.role === "admin";

  return (
    <nav
      aria-label="Main navigation"
      className={cn("flex h-full w-60 shrink-0 flex-col border-r border-line bg-surface", className)}
    >
      <div className="flex h-14 items-center gap-2 border-b border-line px-4">
        <span
          aria-hidden
          className="flex h-7 w-7 items-center justify-center rounded bg-accent text-sm font-bold text-accent-ink"
        >
          T
        </span>
        <span className="text-sm font-semibold tracking-tight">Tradeloom</span>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-4">
        <NavSection title="Trade" items={PRIMARY} />
        <NavSection title="Research" items={RESEARCH} />
        <NavSection title="Manage" items={MANAGE} />
        {isAdmin ? (
          <NavSection
            title="Platform"
            items={[{ href: "/admin", label: "Admin", icon: Shield, adminOnly: true }]}
          />
        ) : null}
      </div>
    </nav>
  );
}
