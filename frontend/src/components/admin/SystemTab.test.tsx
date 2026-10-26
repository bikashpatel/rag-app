import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SystemCheck, SystemStatus } from "../../api/types";
import { makeSystemStatus } from "../../test/adminTabs";
import { deferred, errorResponse, jsonResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import SystemTab from "./SystemTab";

function stubSystem(route: Route = makeSystemStatus(), extra: Record<string, Route> = {}) {
  return stubFetch({ "GET /admin/system": route, ...extra });
}

function region(name: string | RegExp) {
  return within(screen.getByRole("region", { name }));
}

/** The checks as "label: status · detail". */
function checks(): string[] {
  return region("Checks")
    .getAllByRole("listitem")
    .map((item) => {
      const [label, status, detail] = Array.from(item.querySelectorAll("span:not([aria-hidden])")).map((part) => part.textContent);
      return `${label}: ${status} · ${detail}`;
    });
}

/** The detail tiles as label → value · detail. */
function tiles(): Record<string, string> {
  const list = screen.getByLabelText("System details");
  return Object.fromEntries(
    within(list)
      .getAllByRole("term")
      .map((term) => {
        const values = within(term.parentElement as HTMLElement).getAllByRole("definition");
        return [term.textContent, values.map((value) => value.textContent).join(" · ")];
      }),
  );
}

function withCheck(status: SystemStatus, id: SystemCheck["id"], change: Partial<SystemCheck>): SystemCheck[] {
  return status.checks.map((item) => (item.id === id ? { ...item, ...change } : item));
}

const requests = (calls: FetchCall[], path: string) => calls.filter((call) => call.path === path).length;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-20T09:30:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("SystemTab", () => {
  it("shows every check, the details, backups and recent alerts", async () => {
    stubSystem();
    render(<SystemTab />);

    expect(screen.getByRole("heading", { level: 2, name: "System status" })).toBeInTheDocument();
    await screen.findByRole("region", { name: "Checks" });
    expect(checks()).toEqual([
      "Database (Postgres): OK · Reachable; the database holds 48 MB",
      "Redis: OK · Reachable",
      "Vector store (Qdrant): OK · Reachable; 1 collection",
      "Ingestion worker: OK · Heartbeat 4 s ago; 0 jobs running",
      "Ingestion queue: OK · Idle",
      "Backups: OK · Last backup 7.0 h ago (512 MB)",
      "Disk space for uploads: OK · 40 GB free of 100 GB (40 %)",
      "Disk space for backups: OK · 40 GB free of 100 GB (40 %)",
      "Web server (nginx): OK · The app answers through nginx",
      "Monitor and alerts: OK · Ran 20 s ago; alerts go to webhook, ntfy",
    ]);
    expect(screen.queryByText(/need attention|needs attention/)).not.toBeInTheDocument();
    expect(tiles()).toEqual({
      "Worker heartbeat": "4 s ago · 0 running",
      "Waiting to index": "0 · Idle",
      "Failed in 24 h": "0",
      Database: "48 MB",
      "Free for uploads": "40 GB · of 100 GB (40 %)",
      "Free for backups": "40 GB · of 100 GB (40 %)",
      "App version": "v1.4.0-3-gabc1234",
    });

    const backups = region("Backups");
    expect(backups.getByText("20261020T023000Z-scheduled")).toBeInTheDocument();
    expect(backups.getByText(/Daily at 02:30 UTC; next/)).toBeInTheDocument();
    expect(backups.getByText(/9 backups, 4.0 GB in all/)).toBeInTheDocument();
    expect(backups.queryByText("Last failure")).not.toBeInTheDocument();
    expect(backups.getByText(/Backups don't include/)).toHaveTextContent("TOKEN_ENCRYPTION_KEY");

    const alerts = region("Alerts");
    expect(alerts.getByText("Alerts go to")).toBeInTheDocument();
    expect(alerts.getByText("Webhook")).toBeInTheDocument();
    const items = within(alerts.getByRole("list", { name: "Recent alerts" })).getAllByRole("listitem", {
      name: (_, element) => element.classList.contains("admin-system-alert"),
    });
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Resolved[RAG Assistant] Resolved: Vector store (Qdrant)");
    expect(items[0]).toHaveTextContent("RESOLVED Vector store (Qdrant): OK again after 4 min");
    expect(items[0]).not.toHaveTextContent("Details:");
    expect(items[0]).toHaveTextContent("Sent to Webhook and ntfy");
    expect(items[1]).toHaveTextContent("Sent to Webhook; not delivered to ntfy");
  });

  it("calls out what needs attention", async () => {
    const base = makeSystemStatus();
    let checksNow = withCheck(base, "qdrant", { status: "critical", detail: "Unreachable: connection refused" });
    checksNow = checksNow.map((item) =>
      item.id === "backup" ? { ...item, status: "warning", detail: "The last successful backup is 30.0 h old" } : item,
    );
    stubSystem(
      makeSystemStatus({
        status: "critical",
        checks: checksNow,
        backup: {
          ...base.backup,
          failing: true,
          last_failure_at: "2026-10-20T02:31:00Z",
          last_failure_error: "pg_dump failed",
        },
        gmail_reconnect_needed: 2,
        disks: [],
        app_version: null,
      }),
    );
    render(<SystemTab />);

    const notice = await screen.findByText(/checks need attention/);
    expect(notice.closest(".ui-notice")).toHaveClass("ui-notice-danger");
    expect(notice.closest(".ui-notice")).toHaveTextContent("2 checks need attention: Vector store (Qdrant) and Backups.");
    expect(region("Checks").getAllByText("Critical")).toHaveLength(2); // the check and the card's badge
    expect(region("Backups").getByText(/pg_dump failed/)).toHaveClass("admin-system-failing");
    expect(screen.getByText(/2 Gmail connections need/)).toHaveTextContent("reconnecting");
    expect(tiles()["App version"]).toBe("Not set");
    expect(tiles()["Free for uploads"]).toBeUndefined();
  });

  it("says when the backup service hasn't reported and the monitor isn't running", async () => {
    const base = makeSystemStatus();
    stubSystem(
      makeSystemStatus({
        status: "warning",
        checks: withCheck(base, "backup", {
          status: "warning",
          detail: "No backup has run yet: the backup service writes its status after its first run",
        }),
        backup: { status_seen: false, max_age_hours: 26 } as SystemStatus["backup"],
        monitor: { running: false, last_run_at: null, interval_seconds: null, channels: [], test_pending: false },
        alerts: [],
        worker: { heartbeat_age_seconds: null, running_jobs: null },
        queue: { queued: null, pending: null, processing: null, oldest_pending_age_seconds: null },
        failed_documents_24h: null,
        database_size_bytes: null,
      }),
    );
    render(<SystemTab />);

    const backups = await screen.findByRole("region", { name: "Backups" });
    expect(within(backups).getByText(/No backup has run yet/)).toBeInTheDocument();
    const alerts = region("Alerts");
    expect(alerts.getByText(/The monitor service isn't running/)).toBeInTheDocument();
    expect(alerts.getByText("No alerts yet.")).toBeInTheDocument();
    expect(alerts.getByRole("button", { name: "Send a test alert" })).toBeDisabled();
    expect(tiles()).toMatchObject({
      "Worker heartbeat": "None",
      "Waiting to index": "— · Idle",
      "Failed in 24 h": "—",
      Database: "—",
    });
  });

  it("shows a schedule-less backup service, a running backup and alerts that were only logged", async () => {
    const base = makeSystemStatus();
    stubSystem(
      makeSystemStatus({
        backup: { ...base.backup, schedule: null, next_run_at: null, running_since: "2026-10-20T09:29:00Z", backups_kept: 1, total_size_bytes: null },
        monitor: { ...base.monitor, channels: [] },
        queue: { queued: 3, pending: 3, processing: 1, oldest_pending_age_seconds: 600 },
        worker: { heartbeat_age_seconds: 200, running_jobs: 1 },
        alerts: [{ ...base.alerts[1], delivered: [], failed: [] }],
      }),
    );
    render(<SystemTab />);

    const backups = await screen.findByRole("region", { name: "Backups" });
    expect(within(backups).getByText("The backup service isn't running its schedule")).toBeInTheDocument();
    expect(within(backups).getByText(/^Since/)).toBeInTheDocument();
    expect(within(backups).getByText("1 backup")).toBeInTheDocument();
    const alerts = region("Alerts");
    expect(alerts.getByText(/No alert channel is configured/)).toBeInTheDocument();
    expect(alerts.getByText("Logged only (no alert channel)")).toBeInTheDocument();
    expect(alerts.getByRole("button", { name: "Send a test alert" })).toHaveAttribute("title", expect.stringContaining("ALERT_WEBHOOK_URL"));
    expect(tiles()).toMatchObject({ "Worker heartbeat": "3 min ago · 1 running", "Waiting to index": "3 · Oldest 10 min" });
  });

  it("sends a test alert, then waits for it", async () => {
    const user = userEvent.setup();
    let pending = false;
    const calls = stubSystem(() => makeSystemStatus({ monitor: { ...makeSystemStatus().monitor, test_pending: pending } }), {
      "POST /admin/system/test-alert": () => {
        pending = true;
        return jsonResponse({ queued_at: "2026-10-20T09:30:00Z" }, { status: 202 });
      },
    });
    render(<SystemTab />);

    const button = await screen.findByRole("button", { name: "Send a test alert" });
    expect(button).toBeEnabled();
    await user.click(button);
    expect(await screen.findByText(/Test alert queued/)).toBeInTheDocument();
    await waitFor(() => expect(requests(calls, "/admin/system")).toBe(2));
    await waitFor(() => expect(button).toBeDisabled());
    expect(button).toHaveAttribute("title", "A test alert is on its way.");
  });

  it("reports a refused test alert", async () => {
    const user = userEvent.setup();
    stubSystem(makeSystemStatus(), {
      "POST /admin/system/test-alert": errorResponse(409, "A test alert is already waiting for the monitor."),
    });
    render(<SystemTab />);

    await user.click(await screen.findByRole("button", { name: "Send a test alert" }));
    expect(await screen.findByText("A test alert is already waiting for the monitor.")).toBeInTheDocument();
  });

  it("refreshes on request and every 15 seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true, now: new Date("2026-10-20T09:30:00Z") });
    const slow = deferred<Response>();
    let count = 0;
    const calls = stubSystem(() => {
      count += 1;
      return count === 2 ? slow.promise : makeSystemStatus();
    });
    render(<SystemTab />);
    await screen.findByRole("region", { name: "Checks" });

    const refresh = screen.getByRole("button", { name: "Refresh" });
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(refresh);
    expect(refresh).toBeDisabled();
    expect(refresh).toHaveAttribute("aria-busy", "true");
    slow.resolve(jsonResponse(makeSystemStatus()));
    await waitFor(() => expect(refresh).toBeEnabled());

    await act(() => vi.advanceTimersByTimeAsync(15_000));
    await waitFor(() => expect(requests(calls, "/admin/system")).toBe(3));
  });

  it("shows an error with a retry", async () => {
    const user = userEvent.setup();
    let fail = true;
    stubSystem(() => (fail ? errorResponse(503, "A required service is temporarily unavailable.") : makeSystemStatus()));
    render(<SystemTab />);

    expect(await screen.findByRole("alert")).toHaveTextContent("A required service is temporarily unavailable.");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("region", { name: "Checks" })).toBeInTheDocument();
  });
});
