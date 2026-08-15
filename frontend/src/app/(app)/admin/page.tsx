"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ShieldAlert } from "lucide-react";

import { api } from "@/lib/api";
import { formatDate, formatDateTime, formatInteger, formatRelative, humanise } from "@/lib/format";
import { queryKeys } from "@/lib/queries";
import { useSession } from "@/lib/session";
import type {
  AdminInvite,
  FeedbackReport,
  AdminJobRow,
  AdminOrganizationRow,
  AdminOverview,
  AdminUserRow,
  AuditLogEntry,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { Card, CardHeader, MetricCard } from "@/components/ui/card";
import { EmptyState, ErrorState, MetricsSkeleton, Skeleton } from "@/components/ui/feedback";
import { Modal } from "@/components/ui/overlay";
import { Badge, Button, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { Tabs } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/shell/page-header";

export default function AdminPage() {
  const { session } = useSession();
  const [tab, setTab] = useState("overview");

  // The server rejects these routes for non-admins regardless; this is only so a non-admin who
  // guesses the URL sees an explanation instead of a wall of failed requests.
  if (session && session.user.role !== "admin" && session.user.role !== "support") {
    return (
      <>
        <PageHeader title="Administration" />
        <EmptyState
          icon={<ShieldAlert className="h-7 w-7" />}
          title="Not available to your account"
          description="Platform administration is restricted to staff accounts. If you think you should have access, ask an administrator."
        />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Administration"
        description="Platform-wide, across every workspace. Actions here are recorded in the audit log."
      />

      <Tabs
        active={tab}
        onChange={setTab}
        items={[
          { id: "overview", label: "Overview" },
          { id: "users", label: "Users" },
          { id: "workspaces", label: "Workspaces" },
          { id: "feedback", label: "Feedback" },
          { id: "invites", label: "Invites" },
          { id: "jobs", label: "Jobs" },
          { id: "audit", label: "Audit log" },
        ]}
      />

      <div className="mt-4">
        {tab === "overview" ? <OverviewTab /> : null}
        {tab === "users" ? <UsersTab /> : null}
        {tab === "workspaces" ? <WorkspacesTab /> : null}
        {tab === "feedback" ? <FeedbackTab /> : null}
        {tab === "invites" ? <InvitesTab /> : null}
        {tab === "jobs" ? <JobsTab /> : null}
        {tab === "audit" ? <AuditTab /> : null}
      </div>
    </>
  );
}

function OverviewTab() {
  const overview = useQuery({
    queryKey: queryKeys.adminOverview,
    queryFn: () => api.get<AdminOverview>("/api/v1/admin/overview"),
    refetchInterval: 30_000,
  });

  if (overview.isError) {
    return <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />;
  }
  if (overview.isLoading || !overview.data) return <MetricsSkeleton />;

  const data = overview.data;
  const failedJobs = data.jobs.failed ?? 0;

  return (
    <div className="space-y-4">
      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          label="Users"
          value={formatInteger(data.users.total)}
          hint={`${formatInteger(data.users.active)} active · ${formatInteger(data.users.suspended)} suspended`}
        />
        <MetricCard label="Workspaces" value={formatInteger(data.organizations)} />
        <MetricCard label="Trades recorded" value={formatInteger(data.trades)} />
        <MetricCard
          label="Failed jobs"
          value={formatInteger(failedJobs)}
          hint={failedJobs > 0 ? "Investigate below" : "Nothing failing"}
        />
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Job queue" description="Durable job records, not the broker's view." />
          {Object.keys(data.jobs).length === 0 ? (
            <p className="py-6 text-center text-xs text-faint">No jobs recorded.</p>
          ) : (
            <dl className="space-y-2">
              {Object.entries(data.jobs).map(([status, count]) => (
                <div key={status} className="flex items-baseline justify-between gap-3">
                  <dt className="text-xs text-muted">{humanise(status)}</dt>
                  <dd className="tnum text-sm font-medium text-ink">{formatInteger(count)}</dd>
                </div>
              ))}
            </dl>
          )}
        </Card>

        <Card>
          <CardHeader title="Needs attention" />
          <dl className="space-y-2">
            <Attention label="Failed imports" value={data.failed_imports} />
            <Attention label="Failed logins today" value={data.failed_logins_24h} />
            <Attention label="Pending verification" value={data.users.pending} />
            <Attention label="Deletion requested" value={data.users.deletion_requested} />
          </dl>

          {Object.keys(data.failed_jobs_by_kind).length > 0 ? (
            <div className="mt-3 border-t border-line pt-3">
              <p className="mb-2 text-2xs font-medium uppercase tracking-wide text-faint">
                Failures by job kind
              </p>
              <dl className="space-y-1">
                {Object.entries(data.failed_jobs_by_kind).map(([kind, count]) => (
                  <div key={kind} className="flex items-baseline justify-between gap-3 text-xs">
                    <dt className="font-mono text-muted">{kind}</dt>
                    <dd className="tnum font-medium text-loss">{formatInteger(count)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ) : null}
        </Card>
      </div>

      <p className="text-2xs text-faint">
        Generated {formatDateTime(data.generated_at)} · refreshes every 30 seconds.
      </p>
    </div>
  );
}

function Attention({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className={cn("tnum text-sm font-medium", value > 0 ? "text-warn" : "text-ink")}>
        {formatInteger(value)}
      </dd>
    </div>
  );
}

function UsersTab() {
  const [search, setSearch] = useState("");
  const [target, setTarget] = useState<AdminUserRow | null>(null);

  const users = useQuery({
    queryKey: queryKeys.adminUsers({ search }),
    queryFn: () =>
      api.get<AdminUserRow[]>("/api/v1/admin/users", {
        page_size: 50,
        ...(search ? { search } : {}),
      }),
  });

  return (
    <>
      <div className="mb-3 max-w-sm">
        <Input
          placeholder="Search by email or name…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>

      {users.isError ? (
        <ErrorState error={users.error} onRetry={() => void users.refetch()} />
      ) : users.isLoading ? (
        <Skeleton className="h-64 rounded" />
      ) : (
        <Card padded={false}>
          <ul className="divide-y divide-line">
            {(users.data ?? []).map((user) => (
              <li key={user.id} className="flex items-center justify-between gap-3 px-4 py-3">
                <div className="min-w-0">
                  <p className="flex items-center gap-2 text-sm font-medium text-ink">
                    <span className="truncate">{user.full_name ?? user.email}</span>
                    {user.role !== "user" ? <Badge tone="accent">{humanise(user.role)}</Badge> : null}
                    {user.deletion_requested_at ? <Badge tone="loss">Deletion requested</Badge> : null}
                  </p>
                  <p className="truncate text-xs text-muted">
                    {user.email} · joined {formatRelative(user.created_at)} ·{" "}
                    {user.last_login_at
                      ? `last seen ${formatRelative(user.last_login_at)}`
                      : "never signed in"}
                    {user.email_verified ? "" : " · email unverified"}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge tone={user.status === "active" ? "profit" : "warn"}>
                    {humanise(user.status)}
                  </Badge>
                  <Button variant="ghost" size="sm" onClick={() => setTarget(user)}>
                    Change status
                  </Button>
                </div>
              </li>
            ))}
          </ul>
          {(users.data?.length ?? 0) === 0 ? (
            <p className="p-8 text-center text-xs text-faint">No users match that search.</p>
          ) : null}
        </Card>
      )}

      <StatusModal user={target} onClose={() => setTarget(null)} />
    </>
  );
}

function StatusModal({ user, onClose }: { user: AdminUserRow | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [status, setStatus] = useState("active");
  const [reason, setReason] = useState("");

  const save = useMutation({
    mutationFn: () =>
      api.patch(`/api/v1/admin/users/${user?.id}/status`, { status, reason: reason.trim() }),
    onSuccess: () => {
      setReason("");
      void queryClient.invalidateQueries({ queryKey: ["admin"] });
      onClose();
    },
    onError: (error) => toast.fromError(error, "Could not change that user's status"),
  });

  return (
    <Modal
      open={user !== null}
      onClose={onClose}
      title="Change user status"
      description={
        user
          ? `${user.email} is currently ${humanise(user.status).toLowerCase()}. Suspending signs out every one of their sessions immediately.`
          : ""
      }
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={save.isPending}
            disabled={reason.trim().length < 3}
            onClick={() => save.mutate()}
          >
            Apply
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="New status" htmlFor="a-status">
          <Select id="a-status" value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="active">Active</option>
            <option value="suspended">Suspended</option>
            <option value="pending">Pending verification</option>
          </Select>
        </Field>
        <Field
          label="Reason"
          htmlFor="a-reason"
          hint="Recorded in the audit log against your account."
          required
        >
          <Textarea
            id="a-reason"
            rows={3}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </Field>
      </div>
    </Modal>
  );
}

function WorkspacesTab() {
  const [target, setTarget] = useState<AdminOrganizationRow | null>(null);

  const organizations = useQuery({
    queryKey: queryKeys.adminOrganizations(),
    queryFn: () => api.get<AdminOrganizationRow[]>("/api/v1/admin/organizations", { page_size: 50 }),
  });

  if (organizations.isError) {
    return <ErrorState error={organizations.error} onRetry={() => void organizations.refetch()} />;
  }
  if (organizations.isLoading) return <Skeleton className="h-64 rounded" />;

  return (
    <>
      <Card padded={false}>
        <ul className="divide-y divide-line">
          {(organizations.data ?? []).map((organization) => (
            <li key={organization.id} className="flex items-center justify-between gap-3 px-4 py-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-ink">{organization.name}</p>
                <p className="truncate text-xs text-muted">
                  {organization.is_personal ? "Personal" : "Shared"} ·{" "}
                  {formatInteger(organization.member_count)} members ·{" "}
                  {formatInteger(organization.trade_count)} trades · created{" "}
                  {formatRelative(organization.created_at)}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Badge tone={organization.plan === "free" ? "neutral" : "accent"}>
                  {humanise(organization.plan)}
                </Badge>
                {organization.subscription_status ? (
                  <span className="text-2xs text-faint">
                    {humanise(organization.subscription_status)}
                  </span>
                ) : null}
                <Button variant="ghost" size="sm" onClick={() => setTarget(organization)}>
                  Set plan
                </Button>
              </div>
            </li>
          ))}
        </ul>
      </Card>

      <PlanModal organization={target} onClose={() => setTarget(null)} />
    </>
  );
}

function PlanModal({
  organization,
  onClose,
}: {
  organization: AdminOrganizationRow | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [plan, setPlan] = useState("pro");
  const [reason, setReason] = useState("");

  const save = useMutation({
    mutationFn: () =>
      api.post(`/api/v1/admin/organizations/${organization?.id}/plan`, {
        plan,
        reason: reason.trim(),
      }),
    onSuccess: () => {
      setReason("");
      void queryClient.invalidateQueries({ queryKey: ["admin"] });
      onClose();
    },
    onError: (error) => toast.fromError(error, "Could not change that plan"),
  });

  return (
    <Modal
      open={organization !== null}
      onClose={onClose}
      title="Override the plan"
      description="This changes entitlements directly without going through Stripe. Use it for support cases and internal workspaces."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={save.isPending}
            disabled={reason.trim().length < 3}
            onClick={() => save.mutate()}
          >
            Set plan
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="Plan" htmlFor="ap-plan">
          <Select id="ap-plan" value={plan} onChange={(event) => setPlan(event.target.value)}>
            <option value="free">Free</option>
            <option value="pro">Pro</option>
            <option value="enterprise">Enterprise</option>
          </Select>
        </Field>
        <Field label="Reason" htmlFor="ap-reason" hint="Recorded in the audit log." required>
          <Textarea
            id="ap-reason"
            rows={3}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </Field>
      </div>
    </Modal>
  );
}

const JOB_STATUSES = ["", "queued", "running", "completed", "failed", "cancelled"];

const FEEDBACK_TONE: Record<string, "loss" | "accent" | "info" | "neutral"> = {
  bug: "loss",
  idea: "accent",
  question: "info",
  other: "neutral",
};

const FEEDBACK_STATUSES = ["new", "reviewed", "closed"] as const;

/** What users are telling us, newest first. */
function FeedbackTab() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [statusFilter, setStatusFilter] = useState("new");

  const reports = useQuery({
    queryKey: queryKeys.adminFeedback({ status: statusFilter }),
    queryFn: () =>
      api.list<FeedbackReport>("/api/v1/admin/feedback", {
        page_size: 50,
        ...(statusFilter ? { status: statusFilter } : {}),
      }),
    refetchInterval: 60_000,
  });

  const triage = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api.post(`/api/v1/admin/feedback/${id}/status`, { status }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "feedback"] });
    },
    onError: (error) => toast.fromError(error, "Could not update that report"),
  });

  const rows = reports.data?.data ?? [];

  return (
    <>
      <div className="mb-3 max-w-xs">
        <Select
          aria-label="Filter by status"
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value)}
        >
          <option value="">Everything</option>
          {FEEDBACK_STATUSES.map((option) => (
            <option key={option} value={option}>
              {humanise(option)}
            </option>
          ))}
        </Select>
      </div>

      {reports.isError ? (
        <ErrorState error={reports.error} onRetry={() => void reports.refetch()} />
      ) : reports.isLoading ? (
        <Skeleton className="h-40 rounded" />
      ) : rows.length === 0 ? (
        <EmptyState
          title={statusFilter === "new" ? "Nothing waiting" : "No reports"}
          description="Feedback sent from inside the app lands here."
        />
      ) : (
        <div className="space-y-3">
          {rows.map((report) => (
            <Card key={report.id}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <span className="flex flex-wrap items-center gap-2">
                    <Badge tone={FEEDBACK_TONE[report.kind] ?? "neutral"}>
                      {humanise(report.kind)}
                    </Badge>
                    <span className="text-xs text-muted">
                      {report.reporter_email ?? "unknown"} · {formatRelative(report.created_at)}
                      {report.page ? ` · ${report.page}` : ""}
                    </span>
                  </span>
                  {/* Rendered as text. This is whatever a user typed. */}
                  <p className="mt-2 whitespace-pre-wrap text-sm text-ink">{report.message}</p>
                  {Object.keys(report.context).length > 0 ? (
                    <p className="mt-2 text-2xs text-faint">
                      {Object.entries(report.context)
                        .map(([key, value]) => `${key}: ${value}`)
                        .join(" · ")}
                    </p>
                  ) : null}
                </div>
                <div className="flex shrink-0 gap-2">
                  {report.status !== "reviewed" ? (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => triage.mutate({ id: report.id, status: "reviewed" })}
                    >
                      Reviewed
                    </Button>
                  ) : null}
                  {report.status !== "closed" ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => triage.mutate({ id: report.id, status: "closed" })}
                    >
                      Close
                    </Button>
                  ) : null}
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}

const INVITE_TONE: Record<string, "profit" | "neutral" | "warn" | "loss"> = {
  active: "profit",
  used: "neutral",
  expired: "warn",
  revoked: "loss",
};

/**
 * Issue and revoke the codes that admit people to a closed signup.
 *
 * The code is shown in full because an administrator has to send it to someone. It grants nothing
 * beyond the right to register, and it stops working the moment it is spent, expired or revoked.
 */
function InvitesTab() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [note, setNote] = useState("");
  const [maxUses, setMaxUses] = useState("1");
  const [copied, setCopied] = useState<string | null>(null);

  const invites = useQuery({
    queryKey: queryKeys.adminInvites,
    queryFn: () => api.get<AdminInvite[]>("/api/v1/admin/invites"),
  });

  const create = useMutation({
    mutationFn: () =>
      api.post<AdminInvite>("/api/v1/admin/invites", {
        note: note.trim() || undefined,
        max_uses: Number(maxUses) || 1,
      }),
    onSuccess: (invite) => {
      setNote("");
      setMaxUses("1");
      void queryClient.invalidateQueries({ queryKey: queryKeys.adminInvites });
      toast.success("Invite issued", `Send ${invite.code} to ${invite.note ?? "your tester"}.`);
    },
    onError: (error) => toast.fromError(error, "Could not issue an invite"),
  });

  const revoke = useMutation({
    mutationFn: (id: string) => api.post<AdminInvite>(`/api/v1/admin/invites/${id}/revoke`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.adminInvites });
      toast.success("Invite revoked", "It will no longer admit anyone.");
    },
    onError: (error) => toast.fromError(error, "Could not revoke that invite"),
  });

  async function copy(code: string) {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(code);
      window.setTimeout(() => setCopied(null), 2000);
    } catch {
      // Clipboard access can be refused; the code is on screen either way.
      toast.info("Copy it manually", code);
    }
  }

  const rows = invites.data ?? [];

  return (
    <>
      <Card className="mb-4">
        <CardHeader
          title="Issue an invite"
          description="Single-use by default, and it expires in 30 days. Raise the uses for a cohort."
        />
        <div className="grid gap-3 sm:grid-cols-[1fr_140px_auto] sm:items-end">
          <Field label="Who is it for?" htmlFor="i-note">
            <Input
              id="i-note"
              value={note}
              placeholder="Jamie, from the futures forum"
              onChange={(event) => setNote(event.target.value)}
            />
          </Field>
          <Field label="Uses" htmlFor="i-uses">
            <Input
              id="i-uses"
              inputMode="numeric"
              value={maxUses}
              onChange={(event) => setMaxUses(event.target.value)}
            />
          </Field>
          <Button variant="primary" loading={create.isPending} onClick={() => create.mutate()}>
            Issue invite
          </Button>
        </div>
      </Card>

      {invites.isError ? (
        <ErrorState error={invites.error} onRetry={() => void invites.refetch()} />
      ) : invites.isLoading ? (
        <Skeleton className="h-40 rounded" />
      ) : rows.length === 0 ? (
        <EmptyState
          title="No invites yet"
          description="Issue one above, then send the code to your first tester."
        />
      ) : (
        <Card padded={false}>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-2xs uppercase tracking-wide text-faint">
                <tr className="border-b border-line">
                  <th className="px-4 py-2 text-left font-semibold">Code</th>
                  <th className="px-4 py-2 text-left font-semibold">For</th>
                  <th className="px-4 py-2 text-right font-semibold">Uses</th>
                  <th className="px-4 py-2 text-left font-semibold">Redeemed by</th>
                  <th className="px-4 py-2 text-left font-semibold">Expires</th>
                  <th className="px-4 py-2 text-left font-semibold">State</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((invite) => (
                  <tr key={invite.id}>
                    <td className="px-4 py-2">
                      <button
                        type="button"
                        onClick={() => void copy(invite.code)}
                        className="tnum rounded font-medium tracking-wider text-ink underline decoration-dotted underline-offset-4 hover:text-accent"
                        title="Copy to clipboard"
                      >
                        {invite.code}
                      </button>
                      {copied === invite.code ? (
                        <span className="ml-2 text-2xs text-profit">Copied</span>
                      ) : null}
                    </td>
                    <td className="px-4 py-2 text-muted">{invite.note ?? "—"}</td>
                    <td className="tnum px-4 py-2 text-right text-ink">
                      {invite.used_count} / {invite.max_uses}
                    </td>
                    <td className="px-4 py-2 text-muted">
                      {invite.redeemed_by.length > 0 ? invite.redeemed_by.join(", ") : "—"}
                    </td>
                    <td className="px-4 py-2 text-muted">
                      {invite.expires_at ? formatDate(invite.expires_at) : "never"}
                    </td>
                    <td className="px-4 py-2">
                      <Badge tone={INVITE_TONE[invite.state] ?? "neutral"}>
                        {humanise(invite.state)}
                      </Badge>
                    </td>
                    <td className="px-4 py-2 text-right">
                      {invite.state === "active" ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          loading={revoke.isPending && revoke.variables === invite.id}
                          onClick={() => revoke.mutate(invite.id)}
                        >
                          Revoke
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  );
}

function JobsTab() {
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);

  const jobs = useQuery({
    queryKey: queryKeys.adminJobs({ status, page }),
    queryFn: () =>
      api.list<AdminJobRow>("/api/v1/admin/jobs", {
        page,
        page_size: 25,
        ...(status ? { status } : {}),
      }),
    refetchInterval: 15_000,
  });

  return (
    <>
      <div className="mb-3 max-w-xs">
        <Select
          aria-label="Filter by status"
          value={status}
          onChange={(event) => {
            setStatus(event.target.value);
            setPage(1);
          }}
        >
          {JOB_STATUSES.map((option) => (
            <option key={option || "all"} value={option}>
              {option ? humanise(option) : "All statuses"}
            </option>
          ))}
        </Select>
      </div>

      {jobs.isError ? (
        <ErrorState error={jobs.error} onRetry={() => void jobs.refetch()} />
      ) : jobs.isLoading ? (
        <Skeleton className="h-64 rounded" />
      ) : (
        <Card padded={false}>
          <ul className="divide-y divide-line">
            {(jobs.data?.data ?? []).map((job) => (
              <li key={job.id} className="px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="truncate font-mono text-xs text-ink">{job.kind}</span>
                    <Badge
                      tone={
                        job.status === "completed"
                          ? "profit"
                          : job.status === "failed"
                            ? "loss"
                            : job.status === "running"
                              ? "accent"
                              : "neutral"
                      }
                    >
                      {humanise(job.status)}
                    </Badge>
                  </span>
                  <span className="shrink-0 text-2xs text-faint">
                    {job.duration_ms === null
                      ? formatRelative(job.queued_at ?? job.started_at)
                      : `${formatInteger(job.duration_ms)} ms`}
                  </span>
                </div>
                <p className="mt-0.5 text-xs text-muted">
                  queue {job.queue} · attempt {job.attempts} of {job.max_attempts}
                  {job.progress_message ? ` · ${job.progress_message}` : ""}
                </p>
                {job.error_message ? (
                  <p className="mt-1 rounded border border-loss/30 bg-loss/5 p-2 font-mono text-2xs text-loss">
                    {job.error_code ? `${job.error_code}: ` : ""}
                    {job.error_message}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>

          {jobs.data?.meta && jobs.data.meta.total_pages > 1 ? (
            <div className="flex items-center justify-between border-t border-line px-4 py-2 text-xs text-muted">
              <span>
                Page {jobs.data.meta.page} of {jobs.data.meta.total_pages}
              </span>
              <span className="flex gap-2">
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!jobs.data.meta.has_next}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next
                </Button>
              </span>
            </div>
          ) : null}
        </Card>
      )}
    </>
  );
}

function AuditTab() {
  const [page, setPage] = useState(1);

  const logs = useQuery({
    queryKey: queryKeys.adminAuditLogs({ page }),
    queryFn: () => api.list<AuditLogEntry>("/api/v1/admin/audit-logs", { page, page_size: 50 }),
  });

  if (logs.isError) return <ErrorState error={logs.error} onRetry={() => void logs.refetch()} />;
  if (logs.isLoading) return <Skeleton className="h-64 rounded" />;

  return (
    <Card padded={false}>
      <ul className="divide-y divide-line">
        {(logs.data?.data ?? []).map((entry) => (
          <li key={entry.id} className="px-4 py-3">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-sm text-ink">
                <span className="font-medium">{humanise(entry.action)}</span>
                {entry.entity_type ? (
                  <span className="text-muted"> · {humanise(entry.entity_type)}</span>
                ) : null}
              </span>
              <span className="shrink-0 text-2xs text-faint">
                {formatDateTime(entry.created_at)}
              </span>
            </div>
            <p className="mt-0.5 text-xs text-muted">
              {entry.actor_email ?? "system"}
              {entry.ip_address ? ` · ${entry.ip_address}` : ""}
              {entry.summary ? ` · ${entry.summary}` : ""}
            </p>
            {entry.request_id ? (
              <p className="mt-0.5 font-mono text-2xs text-faint">ref {entry.request_id}</p>
            ) : null}
          </li>
        ))}
      </ul>

      {logs.data?.meta && logs.data.meta.total_pages > 1 ? (
        <div className="flex items-center justify-between border-t border-line px-4 py-2 text-xs text-muted">
          <span>
            Page {logs.data.meta.page} of {logs.data.meta.total_pages}
          </span>
          <span className="flex gap-2">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!logs.data.meta.has_next}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </span>
        </div>
      ) : null}
    </Card>
  );
}
