import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SignupStatus } from "../../api/types";
import { makeShippedSignupStatus, makeSignupStatus } from "../../test/adminSignup";
import { markdownResponse } from "../../test/adminTabs";
import { errorResponse, stubFetch, type Route } from "../../test/http";
import SignupCard from "./SignupCard";
import { mailDayResetIst } from "./signupChecks";

const PUBLISHED = {
  "GET /legal/privacy.md": () => markdownResponse("# Privacy policy\n\nWhat we keep."),
  "GET /legal/terms.md": () => markdownResponse("# Terms of service\n\nThe rules."),
};
const DRAFTS = {
  "GET /legal/privacy.md": () => markdownResponse("<!-- draft -->\n# Privacy policy"),
  "GET /legal/terms.md": () => markdownResponse("<!-- draft -->\n# Terms of service"),
};

function stubCard(status: SignupStatus | Route, extra: Record<string, Route> = {}) {
  return stubFetch({ "GET /admin/signup-settings": status, ...PUBLISHED, ...extra });
}

function renderCard() {
  const props = { onShowRecent: vi.fn(), onNavigate: vi.fn() };
  render(<SignupCard {...props} />);
  return props;
}

const card = () => within(screen.getByRole("region", { name: /^Sign-up/ }));

/** The tiles as term → text. */
async function tiles(): Promise<Record<string, string>> {
  const list = (await screen.findByText("Methods")).closest("dl") as HTMLElement;
  return Object.fromEntries(
    within(list)
      .getAllByRole("term")
      .map((term) => [term.textContent, term.nextElementSibling?.textContent ?? ""]),
  );
}

const notices = () =>
  [...document.querySelectorAll(".admin-signup .admin-card-footnote .ui-notice")].map((notice) => notice.textContent);

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-20T09:30:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("SignupCard", () => {
  it("shows sign-up open with its methods, the day's sign-ups and mail, and the checks", async () => {
    stubCard(makeSignupStatus());
    renderCard();

    expect(screen.getByRole("heading", { name: "Sign-up" })).toBeInTheDocument();
    expect(await tiles()).toEqual({
      Methods: "Email and Google",
      "Sign-ups": "3 today14 in 7 days · 2 paid",
      "Email today": "12 of 80About 20 sign-up emails left · resets 05:30 IST",
      Checks: "Done: EmailDone: Bounce reportsDone: TurnstileDone: Google",
    });
    expect(card().getByText("Open")).toHaveClass("ui-status-connected");
    expect(card().getByRole("button", { name: "Edit…" })).toBeEnabled();
    await waitFor(() => expect(notices()).toEqual([]));
  });

  it("shows the app as it ships: closed, nothing configured, and what isn't ready", async () => {
    stubCard(makeShippedSignupStatus(), DRAFTS);
    renderCard();

    expect(await tiles()).toEqual({
      Methods: "None",
      "Sign-ups": "0 today0 in 7 days · 0 paid",
      "Email today": "Not configuredSet MAIL_SMTP_HOST and MAIL_FROM in .env.",
      Checks: "Not set up: EmailNot set up: Bounce reportsNot set up: TurnstileNot set up: Google",
    });
    expect(card().getByText("Closed")).toHaveClass("ui-status-off");
    await waitFor(() =>
      expect(notices()).toEqual([
        "Online payments aren't live: new accounts can't buy a plan yet.",
        "The privacy policy and terms are still drafts.",
      ]),
    );
  });

  it("names the methods closed sign-up would offer, and an open one with none ready", async () => {
    stubCard(
      makeShippedSignupStatus({ settings: { open: false, email: true, google: true }, google_configured: true }),
    );
    renderCard();
    expect((await tiles()).Methods).toBe("Google onlyWhen sign-up opens");
  });

  it("warns while sign-up is open without a working mailer or Turnstile, linking to System", async () => {
    stubCard(
      makeShippedSignupStatus({
        settings: { open: true, email: true, google: true },
        effective: { open: false, email: false, google: false },
        warnings: ["real_ip", "payments_not_live", "mail_webhook_missing", "admin_password_weak"],
      }),
    );
    const props = renderCard();

    expect(await card().findByText("Open, but no method is ready")).toHaveClass("ui-status-attention");
    expect((await tiles()).Methods).toBe("None");
    expect(notices()).toEqual([
      "Visitor addresses aren't reaching the app: set NGINX_REAL_IP_FROM before opening sign-up.",
      "Online payments aren't live: new accounts can't buy a plan yet.",
      "Bounce reports aren't set up: add Resend's webhook and MAIL_WEBHOOK_SECRET.",
      "The admin password is short or common: change ADMIN_PASSWORD in .env.",
      "Sign-up is open, but the mailer and Turnstile aren't configured: email sign-up and password resets are off. See Admin → System",
    ]);
    await userEvent.setup().click(card().getByRole("button", { name: "See Admin → System" }));
    expect(props.onNavigate).toHaveBeenCalledWith("system");
  });

  it("names only Turnstile when the mailer works", async () => {
    stubCard(makeSignupStatus({ turnstile_configured: false, effective: { open: true, email: false, google: true } }));
    renderCard();
    expect((await tiles()).Methods).toBe("Google only");
    expect(notices()).toEqual([
      "Sign-up is open, but Turnstile isn't configured: email sign-up and password resets are off.",
    ]);
  });

  it("says when sign-up emails are paused or used up for the day", async () => {
    stubCard(makeSignupStatus({ public_paused: true, bounces_today: 4 }));
    renderCard();
    expect((await tiles())["Email today"]).toBe("12 of 80Sign-up emails paused: too many bounces");
    expect(document.querySelector(".admin-signup-facts .admin-check-warn")).not.toBeNull();
  });

  it("counts the sign-up emails left in the singular and at none", async () => {
    stubCard(makeSignupStatus({ public_mail_left: 1, mail_sent_today: 1_200, mail_daily_limit: 1_500 }));
    renderCard();
    expect((await tiles())["Email today"]).toBe("1,200 of 1,500About 1 sign-up email left · resets 05:30 IST");
  });

  it("says none are left", async () => {
    stubCard(makeSignupStatus({ public_mail_left: 0 }));
    renderCard();
    expect((await tiles())["Email today"]).toBe("12 of 80No sign-up emails left · resets 05:30 IST");
  });

  it("names each unfinished policy page", async () => {
    stubCard(makeSignupStatus(), {
      "GET /legal/privacy.md": () => markdownResponse("<!-- draft -->\n# Privacy policy"),
      "GET /legal/terms.md": () => new Response("Not found", { status: 404 }),
    });
    renderCard();
    await waitFor(() =>
      expect(notices()).toEqual(["The privacy policy is still a draft. The terms of service aren't published."]),
    );
  });

  it("says when a policy page couldn't be checked", async () => {
    stubCard(makeSignupStatus(), {
      "GET /legal/terms.md": () => markdownResponse("<!-- draft -->\n# Terms of service"),
      "GET /legal/privacy.md": () => new Response("", { status: 503 }),
    });
    renderCard();
    await waitFor(() =>
      expect(notices()).toEqual([
        "The privacy policy couldn't be checked. The terms of service are still a draft.",
      ]),
    );
  });

  it("opens the last 7 days' sign-ups from the Sign-ups tile", async () => {
    stubCard(makeSignupStatus());
    const props = renderCard();
    await userEvent.setup().click(await card().findByRole("button", { name: "14 in 7 days · 2 paid" }));
    expect(props.onShowRecent).toHaveBeenCalledTimes(1);
  });

  it("shows a failed load with a retry, and can't be edited until it loads", async () => {
    const replies: (Response | SignupStatus)[] = [errorResponse(503, "The database is starting."), makeSignupStatus()];
    stubCard(() => replies.shift());
    const user = userEvent.setup();
    renderCard();

    expect(await card().findByRole("alert")).toHaveTextContent("The database is starting.");
    expect(card().getByRole("button", { name: "Edit…" })).toBeDisabled();
    await user.click(card().getByRole("button", { name: "Try again" }));
    expect(await card().findByText("Open")).toBeInTheDocument();
    expect(card().getByRole("button", { name: "Edit…" })).toBeEnabled();
  });

  it("edits the settings in the dialog and shows the saved state", async () => {
    const closed = makeShippedSignupStatus({ settings: { open: false, email: true, google: true } });
    const calls = stubCard(makeSignupStatus(), { "PUT /admin/signup-settings": closed });
    const user = userEvent.setup();
    renderCard();

    await user.click(await card().findByRole("button", { name: "Edit…" }));
    const dialog = screen.getByRole("dialog", { name: "Sign-up" });
    await user.click(within(dialog).getByRole("switch", { name: "Open sign-up" }));
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(card().getByRole("status")).toHaveTextContent("Saved. Sign-up is closed.");
    expect(card().getByText("Closed")).toBeInTheDocument();
    expect(calls.find((call) => call.method === "PUT")?.body).toEqual({ open: false, email: true, google: true });
    expect(card().getByRole("button", { name: "Edit…" })).toHaveFocus();
  });

  it("says when saving opened sign-up, and closes the dialog from Cancel", async () => {
    stubCard(makeShippedSignupStatus({ warnings: [], mail_webhook_configured: true }), {
      "PUT /admin/signup-settings": makeSignupStatus(),
    });
    const user = userEvent.setup();
    renderCard();

    await user.click(await card().findByRole("button", { name: "Edit…" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(card().getByRole("button", { name: "Edit…" }));
    await waitFor(() => expect(screen.getByRole("switch", { name: "Open sign-up" })).toBeEnabled());
    await user.click(screen.getByRole("switch", { name: "Open sign-up" }));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await card().findByText("Saved. Sign-up is open.")).toBeInTheDocument();
  });
});

describe("mailDayResetIst", () => {
  it("is the next UTC midnight in IST", () => {
    expect(mailDayResetIst(Date.parse("2026-10-20T23:59:00Z"))).toBe("05:30");
    expect(mailDayResetIst(Date.parse("2026-10-21T00:00:00Z"))).toBe("05:30");
  });
});
