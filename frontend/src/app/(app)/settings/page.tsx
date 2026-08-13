"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Download, LogOut, Monitor, Trash2, UserPlus } from "lucide-react";

import { ApiError, api } from "@/lib/api";
import { formatDateTime, formatRelative, humanise } from "@/lib/format";
import { queryKeys } from "@/lib/queries";
import { useSession } from "@/lib/session";
import type { ActiveSession, Organization, OrganizationMember, UserProfile } from "@/lib/types";
import { Card, CardHeader } from "@/components/ui/card";
import { ErrorState, Skeleton } from "@/components/ui/feedback";
import { ConfirmDialog, Modal } from "@/components/ui/overlay";
import { Badge, Button, Field, Input, Select } from "@/components/ui/primitives";
import { Tabs } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/shell/page-header";

const TIMEZONES = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "Europe/London",
  "Europe/Berlin",
  "Europe/Zurich",
  "Asia/Tokyo",
  "Asia/Hong_Kong",
  "Asia/Singapore",
  "Australia/Sydney",
];

const CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD"];

export default function SettingsPage() {
  const [tab, setTab] = useState("profile");
  const { session } = useSession();
  const role = session?.active_organization?.role;
  const canManage = role === "owner" || role === "manager";

  return (
    <>
      <PageHeader title="Settings" description="Your profile, security and workspace." />

      <Tabs
        active={tab}
        onChange={setTab}
        items={[
          { id: "profile", label: "Profile" },
          { id: "security", label: "Security" },
          { id: "workspace", label: "Workspace" },
          { id: "data", label: "Your data" },
        ]}
      />

      <div className="mt-4 space-y-4">
        {tab === "profile" ? <ProfileSection /> : null}
        {tab === "security" ? <SecuritySection /> : null}
        {tab === "workspace" ? <WorkspaceSection canManage={canManage} /> : null}
        {tab === "data" ? <DataSection /> : null}
      </div>
    </>
  );
}

function ProfileSection() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const { session } = useSession();
  const user = session?.user;

  const [form, setForm] = useState({
    full_name: user?.full_name ?? "",
    display_name: user?.display_name ?? "",
    timezone: user?.timezone ?? "UTC",
    theme: user?.theme ?? "system",
  });

  const save = useMutation({
    mutationFn: () =>
      api.patch<UserProfile>("/api/v1/users/me", {
        full_name: form.full_name.trim() || null,
        display_name: form.display_name.trim() || null,
        timezone: form.timezone,
        theme: form.theme,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.session });
      toast.success("Profile saved");
    },
    onError: (error) => toast.fromError(error, "Could not save your profile"),
  });

  if (!user) return <Skeleton className="h-64 rounded" />;

  return (
    <Card>
      <CardHeader
        title="Your profile"
        description="Your timezone decides how trade times are displayed. Stored values are always UTC."
      />
      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          label="Email"
          htmlFor="p-email"
          hint={user.email_verified ? "Verified" : "Not verified yet"}
        >
          <Input id="p-email" value={user.email} disabled readOnly />
        </Field>
        <Field label="Full name" htmlFor="p-name">
          <Input
            id="p-name"
            value={form.full_name}
            onChange={(event) => setForm((f) => ({ ...f, full_name: event.target.value }))}
          />
        </Field>
        <Field label="Display name" htmlFor="p-display">
          <Input
            id="p-display"
            value={form.display_name}
            onChange={(event) => setForm((f) => ({ ...f, display_name: event.target.value }))}
          />
        </Field>
        <Field label="Timezone" htmlFor="p-tz">
          <Select
            id="p-tz"
            value={form.timezone}
            onChange={(event) => setForm((f) => ({ ...f, timezone: event.target.value }))}
          >
            {TIMEZONES.map((zone) => (
              <option key={zone} value={zone}>
                {zone}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Theme" htmlFor="p-theme">
          <Select
            id="p-theme"
            value={form.theme}
            onChange={(event) => setForm((f) => ({ ...f, theme: event.target.value }))}
          >
            <option value="system">Match my system</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </Select>
        </Field>
      </div>
      <div className="mt-4 flex justify-end">
        <Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>
          Save profile
        </Button>
      </div>
    </Card>
  );
}

function SecuritySection() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [confirmRevokeAll, setConfirmRevokeAll] = useState(false);

  const sessions = useQuery({
    queryKey: queryKeys.activeSessions,
    queryFn: () => api.get<ActiveSession[]>("/api/v1/auth/sessions"),
  });

  const changePassword = useMutation({
    mutationFn: () =>
      api.action("/api/v1/auth/password", { current_password: current, new_password: next }),
    onSuccess: () => {
      setCurrent("");
      setNext("");
      setConfirm("");
      void queryClient.invalidateQueries({ queryKey: queryKeys.activeSessions });
      toast.success("Password changed", "Every other session was signed out.");
    },
    onError: (error) => toast.fromError(error, "Could not change your password"),
  });

  const revoke = useMutation({
    mutationFn: (sessionId: string) => api.delete(`/api/v1/auth/sessions/${sessionId}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.activeSessions });
    },
    onError: (error) => toast.fromError(error, "Could not revoke that session"),
  });

  const revokeAll = useMutation({
    mutationFn: () => api.action("/api/v1/auth/sessions/revoke-all"),
    onSuccess: () => {
      setConfirmRevokeAll(false);
      void queryClient.invalidateQueries({ queryKey: queryKeys.activeSessions });
      toast.success("Signed out everywhere else");
    },
    onError: (error) => toast.fromError(error, "Could not sign out the other sessions"),
  });

  const mismatch = confirm !== "" && next !== confirm;

  return (
    <>
      <Card>
        <CardHeader
          title="Change password"
          description="Changing your password signs out every other session immediately."
        />
        <div className="grid max-w-lg gap-3">
          <Field label="Current password" htmlFor="s-current" required>
            <Input
              id="s-current"
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(event) => setCurrent(event.target.value)}
            />
          </Field>
          <Field
            label="New password"
            htmlFor="s-new"
            hint="At least 12 characters. The server checks strength too."
            required
          >
            <Input
              id="s-new"
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(event) => setNext(event.target.value)}
            />
          </Field>
          <Field
            label="Confirm new password"
            htmlFor="s-confirm"
            error={mismatch ? "The two passwords do not match." : undefined}
            required
          >
            <Input
              id="s-confirm"
              type="password"
              autoComplete="new-password"
              invalid={mismatch}
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
            />
          </Field>
        </div>
        <div className="mt-4 flex justify-end">
          <Button
            variant="primary"
            loading={changePassword.isPending}
            disabled={!current || !next || mismatch || next !== confirm}
            onClick={() => changePassword.mutate()}
          >
            Change password
          </Button>
        </div>
      </Card>

      <Card padded={false}>
        <div className="flex items-center justify-between border-b border-line p-4">
          <div>
            <h2 className="text-sm font-semibold text-ink">Active sessions</h2>
            <p className="mt-0.5 text-xs text-muted">
              Every browser currently signed in to your account.
            </p>
          </div>
          <Button
            variant="outline"
            icon={<LogOut className="h-3.5 w-3.5" />}
            onClick={() => setConfirmRevokeAll(true)}
          >
            Sign out everywhere else
          </Button>
        </div>

        {sessions.isError ? (
          <div className="p-4">
            <ErrorState error={sessions.error} onRetry={() => void sessions.refetch()} />
          </div>
        ) : sessions.isLoading ? (
          <div className="p-4">
            <Skeleton className="h-32 rounded" />
          </div>
        ) : (
          <ul className="divide-y divide-line">
            {(sessions.data ?? []).map((item) => (
              <li key={item.id} className="flex items-center justify-between gap-3 px-4 py-3">
                <span className="flex min-w-0 items-start gap-3">
                  <Monitor className="mt-0.5 h-4 w-4 shrink-0 text-faint" aria-hidden />
                  <span className="min-w-0">
                    <span className="flex items-center gap-2">
                      <span className="truncate text-sm text-ink">
                        {item.user_agent ?? "Unknown browser"}
                      </span>
                      {item.is_current ? <Badge tone="accent">This device</Badge> : null}
                    </span>
                    <span className="mt-0.5 block text-xs text-muted">
                      {item.ip_address ?? "IP not recorded"} · last seen{" "}
                      {formatRelative(item.last_seen_at)} · expires {formatDateTime(item.expires_at)}
                    </span>
                  </span>
                </span>
                {item.is_current ? null : (
                  <Button
                    variant="ghost"
                    size="sm"
                    loading={revoke.isPending && revoke.variables === item.id}
                    onClick={() => revoke.mutate(item.id)}
                  >
                    Revoke
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <ConfirmDialog
        open={confirmRevokeAll}
        onClose={() => setConfirmRevokeAll(false)}
        onConfirm={() => revokeAll.mutate()}
        loading={revokeAll.isPending}
        confirmLabel="Sign out everywhere else"
        title="Sign out of every other session?"
        message="You stay signed in here. Every other browser will need to sign in again."
      />
    </>
  );
}

function WorkspaceSection({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [inviting, setInviting] = useState(false);
  const [removing, setRemoving] = useState<OrganizationMember | null>(null);

  const organization = useQuery({
    queryKey: queryKeys.organization,
    queryFn: () => api.get<Organization>("/api/v1/organizations/current"),
  });

  const members = useQuery({
    queryKey: queryKeys.members,
    queryFn: () => api.get<OrganizationMember[]>("/api/v1/organizations/current/members"),
  });

  const [form, setForm] = useState<{ name: string; base_currency: string; timezone: string } | null>(
    null,
  );
  const values = form ?? {
    name: organization.data?.name ?? "",
    base_currency: organization.data?.base_currency ?? "USD",
    timezone: organization.data?.timezone ?? "UTC",
  };

  const save = useMutation({
    mutationFn: () =>
      api.patch<Organization>("/api/v1/organizations/current", {
        name: values.name.trim(),
        base_currency: values.base_currency,
        timezone: values.timezone,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.organization });
      void queryClient.invalidateQueries({ queryKey: queryKeys.session });
      toast.success("Workspace saved");
    },
    onError: (error) => toast.fromError(error, "Could not save the workspace"),
  });

  const changeRole = useMutation({
    mutationFn: ({ memberId, role }: { memberId: string; role: string }) =>
      api.patch(`/api/v1/organizations/current/members/${memberId}`, { role }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.members });
    },
    onError: (error) => toast.fromError(error, "Could not change that role"),
  });

  const removeMember = useMutation({
    mutationFn: (memberId: string) =>
      api.delete(`/api/v1/organizations/current/members/${memberId}`),
    onSuccess: () => {
      setRemoving(null);
      void queryClient.invalidateQueries({ queryKey: queryKeys.members });
    },
    onError: (error) => toast.fromError(error, "Could not remove that member"),
  });

  if (organization.isError) {
    return <ErrorState error={organization.error} onRetry={() => void organization.refetch()} />;
  }
  if (organization.isLoading || !organization.data) {
    return <Skeleton className="h-64 rounded" />;
  }

  return (
    <>
      <Card>
        <CardHeader
          title="Workspace"
          description={
            organization.data.is_personal
              ? "Your personal workspace. Everything you record lives here."
              : "A shared workspace. Members see the same accounts and trades."
          }
        />
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name" htmlFor="w-name" className="sm:col-span-2">
            <Input
              id="w-name"
              value={values.name}
              disabled={!canManage}
              onChange={(event) => setForm({ ...values, name: event.target.value })}
            />
          </Field>
          <Field
            label="Reporting currency"
            htmlFor="w-currency"
            hint="Used for figures that combine accounts."
          >
            <Select
              id="w-currency"
              value={values.base_currency}
              disabled={!canManage}
              onChange={(event) => setForm({ ...values, base_currency: event.target.value })}
            >
              {CURRENCIES.map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Timezone" htmlFor="w-tz" hint="Defines where a trading day starts and ends.">
            <Select
              id="w-tz"
              value={values.timezone}
              disabled={!canManage}
              onChange={(event) => setForm({ ...values, timezone: event.target.value })}
            >
              {TIMEZONES.map((zone) => (
                <option key={zone} value={zone}>
                  {zone}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        {canManage ? (
          <div className="mt-4 flex justify-end">
            <Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>
              Save workspace
            </Button>
          </div>
        ) : (
          <p className="mt-3 text-xs text-faint">
            Only a manager or the owner can change these. Your role is {humanise(organization.data.your_role)}.
          </p>
        )}
      </Card>

      <Card padded={false}>
        <div className="flex items-center justify-between border-b border-line p-4">
          <div>
            <h2 className="text-sm font-semibold text-ink">Members</h2>
            <p className="mt-0.5 text-xs text-muted">
              {organization.data.member_count} in this workspace.
            </p>
          </div>
          {canManage ? (
            <Button
              variant="primary"
              icon={<UserPlus className="h-3.5 w-3.5" />}
              onClick={() => setInviting(true)}
            >
              Invite
            </Button>
          ) : null}
        </div>

        {members.isLoading ? (
          <div className="p-4">
            <Skeleton className="h-32 rounded" />
          </div>
        ) : (
          <ul className="divide-y divide-line">
            {(members.data ?? []).map((member) => (
              <li key={member.id} className="flex items-center justify-between gap-3 px-4 py-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-ink">
                    {member.full_name ?? member.email}
                  </p>
                  <p className="truncate text-xs text-muted">
                    {member.email} ·{" "}
                    {member.joined_at ? `joined ${formatRelative(member.joined_at)}` : "invited"}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {member.status !== "active" ? (
                    <Badge tone="warn">{humanise(member.status)}</Badge>
                  ) : null}
                  {canManage && member.role !== "owner" ? (
                    <>
                      <Select
                        aria-label={`Role for ${member.email}`}
                        className="h-8 w-32 text-xs"
                        value={member.role}
                        onChange={(event) =>
                          changeRole.mutate({ memberId: member.id, role: event.target.value })
                        }
                      >
                        <option value="viewer">Viewer</option>
                        <option value="member">Member</option>
                        <option value="manager">Manager</option>
                      </Select>
                      <button
                        type="button"
                        aria-label={`Remove ${member.email}`}
                        onClick={() => setRemoving(member)}
                        className="rounded p-1 text-faint transition-colors hover:bg-raised hover:text-loss"
                      >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden />
                      </button>
                    </>
                  ) : (
                    <Badge tone={member.role === "owner" ? "accent" : "neutral"}>
                      {humanise(member.role)}
                    </Badge>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <InviteModal open={inviting} onClose={() => setInviting(false)} />

      <ConfirmDialog
        open={removing !== null}
        onClose={() => setRemoving(null)}
        onConfirm={() => removing && removeMember.mutate(removing.id)}
        loading={removeMember.isPending}
        destructive
        confirmLabel="Remove member"
        title="Remove this member?"
        message={`${removing?.email ?? "They"} will lose access to this workspace immediately. Trades they recorded stay where they are.`}
      />
    </>
  );
}

function InviteModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");

  const invite = useMutation({
    mutationFn: () =>
      api.post<OrganizationMember>("/api/v1/organizations/current/members", {
        email: email.trim(),
        role,
      }),
    onSuccess: () => {
      setEmail("");
      void queryClient.invalidateQueries({ queryKey: queryKeys.members });
      void queryClient.invalidateQueries({ queryKey: queryKeys.organization });
      onClose();
    },
    onError: (error) => {
      if (error instanceof ApiError && error.isEntitlement) {
        toast.error("Member limit reached", "Your plan does not allow another member.");
        return;
      }
      toast.fromError(error, "Could not invite that person");
    },
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Invite someone"
      description="They join this workspace and see the same accounts, trades and analytics."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={invite.isPending}
            disabled={email.trim() === ""}
            onClick={() => invite.mutate()}
          >
            Send invite
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="Email" htmlFor="inv-email" required>
          <Input
            id="inv-email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </Field>
        <Field
          label="Role"
          htmlFor="inv-role"
          hint="Viewers cannot record or edit anything. Managers can change workspace settings."
        >
          <Select id="inv-role" value={role} onChange={(event) => setRole(event.target.value)}>
            <option value="viewer">Viewer</option>
            <option value="member">Member</option>
            <option value="manager">Manager</option>
          </Select>
        </Field>
      </div>
    </Modal>
  );
}

function DataSection() {
  const toast = useToast();
  const [deleting, setDeleting] = useState(false);

  return (
    <>
      <Card>
        <CardHeader
          title="Export your data"
          description="Every account, trade, order, note and backtest in this workspace, as JSON. Nothing is summarised or omitted."
        />
        <Button
          variant="outline"
          icon={<Download className="h-3.5 w-3.5" />}
          onClick={() => {
            // A plain navigation: the endpoint streams a file with a Content-Disposition header,
            // and the session cookie travels with it.
            window.location.href = "/api/v1/users/me/export";
            toast.info("Preparing your export", "The download starts in a moment.");
          }}
        >
          Download export
        </Button>
      </Card>

      <Card className="border-loss/30">
        <CardHeader
          title="Delete your account"
          description="This is permanent. Workspaces you solely own, and every trade in them, are removed within seven days. Signing in before then cancels it."
        />
        <Button variant="danger" icon={<Trash2 className="h-3.5 w-3.5" />} onClick={() => setDeleting(true)}>
          Delete my account
        </Button>
      </Card>

      <DeleteAccountModal open={deleting} onClose={() => setDeleting(false)} />
    </>
  );
}

function DeleteAccountModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast();
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");

  const request = useMutation({
    mutationFn: () => api.action("/api/v1/users/me/delete", { password, confirmation }),
    onSuccess: () => {
      // Every session is revoked server-side, so there is nothing to return to.
      window.location.href = "/login";
    },
    onError: (error) => toast.fromError(error, "Could not schedule the deletion"),
  });

  const phrase = "DELETE MY ACCOUNT";

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Delete your account"
      description="You will be signed out of every device. There is no undo after seven days."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Keep my account
          </Button>
          <Button
            variant="danger"
            loading={request.isPending}
            disabled={password === "" || confirmation.trim().toUpperCase() !== phrase}
            onClick={() => request.mutate()}
          >
            Delete permanently
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="Your password" htmlFor="d-password" required>
          <Input
            id="d-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>
        <Field label={`Type "${phrase}" to confirm`} htmlFor="d-confirm" required>
          <Input
            id="d-confirm"
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
          />
        </Field>
      </div>
    </Modal>
  );
}
