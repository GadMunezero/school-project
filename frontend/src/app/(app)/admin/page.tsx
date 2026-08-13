"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ShieldAlert } from "lucide-react";

import { api } from "@/lib/api";
import { formatDateTime, formatInteger, formatRelative, humanise } from "@/lib/format";
import { queryKeys } from "@/lib/queries";
import { useSession } from "@/lib/session";
import type {
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
          { id: "jobs", label: "Jobs" },
          { id: "audit", label: "Audit log" },
        ]}
      />

      <div className="mt-4">
        {tab === "overview" ? <OverviewTab /> : null}
        {tab === "users" ? <UsersTab /> : null}
        {tab === "workspaces" ? <WorkspacesTab /> : null}
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
