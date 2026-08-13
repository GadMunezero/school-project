"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Bell, Building2, Check, LogOut, Menu, Monitor, Moon, Search, Sun, User } from "lucide-react";

import { api } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { queryKeys } from "@/lib/queries";
import { useSession } from "@/lib/session";
import { useTheme } from "@/lib/theme";
import type { Notification } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/primitives";
import { Dropdown, DropdownItem } from "@/components/ui/overlay";
import { useToast } from "@/components/ui/toast";
import { CommandMenu } from "./command-menu";

export function Topbar({ onOpenSidebar }: { onOpenSidebar?: () => void }) {
  const { session, clear } = useSession();
  const router = useRouter();
  const queryClient = useQueryClient();
  const toast = useToast();

  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [commandOpen, setCommandOpen] = useState(false);

  // Cmd/Ctrl-K opens search from anywhere, the convention users already expect.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandOpen(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const unread = useQuery({
    queryKey: queryKeys.unreadCount,
    queryFn: () => api.get<{ unread: number }>("/api/v1/notifications/unread-count"),
    refetchInterval: 60_000,
    enabled: Boolean(session),
  });

  const switchWorkspace = useMutation({
    mutationFn: (organizationId: string) =>
      api.post("/api/v1/auth/switch-organization", { organization_id: organizationId }),
    onSuccess: () => {
      // Every cached query is scoped to the previous workspace, so drop all of it.
      queryClient.clear();
      setWorkspaceOpen(false);
      router.refresh();
    },
    onError: (error) => toast.fromError(error, "Could not switch workspace"),
  });

  const logout = useMutation({
    mutationFn: () => api.action("/api/v1/auth/logout"),
    onSuccess: () => {
      clear();
      router.push("/login");
    },
    onError: (error) => toast.fromError(error, "Could not sign out"),
  });

  const active = session?.active_organization;

  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b border-line bg-surface px-3 sm:px-4">
      <Button
        variant="ghost"
        size="icon"
        className="lg:hidden"
        aria-label="Open navigation"
        onClick={onOpenSidebar}
        icon={<Menu className="h-4 w-4" />}
      />

      {/* Workspace switcher */}
      <div className="relative">
        <button
          type="button"
          onClick={() => setWorkspaceOpen((open) => !open)}
          aria-expanded={workspaceOpen}
          aria-haspopup="menu"
          className="flex items-center gap-2 rounded px-2 py-1.5 text-sm transition-colors hover:bg-raised"
        >
          <Building2 className="h-4 w-4 text-muted" aria-hidden />
          <span className="max-w-[10rem] truncate font-medium">{active?.name ?? "Workspace"}</span>
          {active ? (
            <span className="hidden rounded bg-raised px-1.5 py-0.5 text-2xs uppercase text-muted sm:inline">
              {active.plan}
            </span>
          ) : null}
        </button>
        <Dropdown open={workspaceOpen} onClose={() => setWorkspaceOpen(false)} align="left">
          {session?.organizations.map((organization) => (
            <DropdownItem
              key={organization.id}
              active={organization.id === active?.id}
              onClick={() => switchWorkspace.mutate(organization.id)}
            >
              <span className="flex-1 truncate">{organization.name}</span>
              <span className="text-2xs text-faint">{organization.role}</span>
              {organization.id === active?.id ? <Check className="h-3.5 w-3.5" aria-hidden /> : null}
            </DropdownItem>
          ))}
        </Dropdown>
      </div>

      <button
        type="button"
        onClick={() => setCommandOpen(true)}
        className={cn(
          "ml-auto flex items-center gap-2 rounded border border-line px-2.5 py-1.5",
          "text-xs text-faint transition-colors hover:bg-raised sm:w-64",
        )}
      >
        <Search className="h-3.5 w-3.5" aria-hidden />
        <span className="hidden flex-1 text-left sm:block">Search trades, strategies…</span>
        <kbd className="hidden rounded bg-raised px-1 py-0.5 font-mono text-2xs sm:block">⌘K</kbd>
      </button>

      <ThemeToggle />

      {/* Notifications */}
      <div className="relative">
        <Button
          variant="ghost"
          size="icon"
          aria-label={`Notifications${unread.data?.unread ? `, ${unread.data.unread} unread` : ""}`}
          onClick={() => setNotificationsOpen((open) => !open)}
          icon={<Bell className="h-4 w-4" />}
        >
          {unread.data && unread.data.unread > 0 ? (
            <span className="absolute right-1 top-1 flex h-2 w-2 rounded-full bg-loss" aria-hidden />
          ) : null}
        </Button>
        <NotificationsPanel open={notificationsOpen} onClose={() => setNotificationsOpen(false)} />
      </div>

      {/* Profile */}
      <div className="relative">
        <button
          type="button"
          onClick={() => setProfileOpen((open) => !open)}
          aria-haspopup="menu"
          aria-expanded={profileOpen}
          aria-label="Account menu"
          className="flex h-8 w-8 items-center justify-center rounded-full bg-raised text-xs font-semibold text-ink transition-colors hover:bg-line"
        >
          {(session?.user.display_name ?? session?.user.email ?? "?").charAt(0).toUpperCase()}
        </button>
        <Dropdown open={profileOpen} onClose={() => setProfileOpen(false)}>
          <div className="border-b border-line px-2.5 py-2">
            <p className="truncate text-sm font-medium">{session?.user.full_name ?? "Account"}</p>
            <p className="truncate text-xs text-muted">{session?.user.email}</p>
          </div>
          <Link href="/settings" onClick={() => setProfileOpen(false)}>
            <DropdownItem>
              <User className="h-3.5 w-3.5" aria-hidden /> Settings
            </DropdownItem>
          </Link>
          <DropdownItem destructive onClick={() => logout.mutate()}>
            <LogOut className="h-3.5 w-3.5" aria-hidden /> Sign out
          </DropdownItem>
        </Dropdown>
      </div>

      <CommandMenu open={commandOpen} onClose={() => setCommandOpen(false)} />
    </header>
  );
}

function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const Icon = theme === "dark" ? Moon : theme === "light" ? Sun : Monitor;

  return (
    <div className="relative">
      <Button
        variant="ghost"
        size="icon"
        aria-label="Change theme"
        onClick={() => setOpen((value) => !value)}
        icon={<Icon className="h-4 w-4" />}
      />
      <Dropdown open={open} onClose={() => setOpen(false)}>
        {(["light", "dark", "system"] as const).map((option) => (
          <DropdownItem
            key={option}
            active={theme === option}
            onClick={() => {
              setTheme(option);
              setOpen(false);
            }}
          >
            <span className="capitalize">{option}</span>
          </DropdownItem>
        ))}
      </Dropdown>
    </div>
  );
}

function NotificationsPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();

  const notifications = useQuery({
    queryKey: queryKeys.notifications({ page_size: 10 }),
    queryFn: () => api.list<Notification>("/api/v1/notifications", { page_size: 10 }),
    enabled: open,
  });

  const markAll = useMutation({
    mutationFn: () => api.action("/api/v1/notifications/read-all"),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["notifications"] });
    },
  });

  return (
    <Dropdown open={open} onClose={onClose} className="w-80">
      <div className="flex items-center justify-between border-b border-line px-2.5 py-2">
        <p className="text-sm font-medium">Notifications</p>
        <button
          type="button"
          onClick={() => markAll.mutate()}
          className="text-2xs text-accent hover:underline"
        >
          Mark all read
        </button>
      </div>
      <div className="max-h-80 overflow-y-auto">
        {notifications.isLoading ? (
          <p className="px-2.5 py-6 text-center text-xs text-faint">Loading…</p>
        ) : (notifications.data?.data.length ?? 0) === 0 ? (
          <p className="px-2.5 py-6 text-center text-xs text-faint">Nothing yet.</p>
        ) : (
          notifications.data?.data.map((notification) => {
            const body = (
              <div
                className={cn(
                  "border-b border-line px-2.5 py-2 last:border-0",
                  !notification.read_at && "bg-accent/5",
                )}
              >
                <p className="text-xs font-medium text-ink">{notification.title}</p>
                {notification.body ? (
                  <p className="mt-0.5 text-2xs text-muted">{notification.body}</p>
                ) : null}
                <p className="mt-1 text-2xs text-faint">{formatRelative(notification.created_at)}</p>
              </div>
            );
            return notification.link ? (
              <Link key={notification.id} href={notification.link} onClick={onClose} className="block hover:bg-raised">
                {body}
              </Link>
            ) : (
              <div key={notification.id}>{body}</div>
            );
          })
        )}
      </div>
    </Dropdown>
  );
}
