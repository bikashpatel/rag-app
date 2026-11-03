import type { Page, Request } from "@playwright/test";
import type { AccountInfo, AuthConfig } from "../../frontend/src/api/types";
import { identifierField } from "../support/app";
import {
  ACCEPTED,
  TURNSTILE_URL,
  fakeAuthConfig,
  fakeCatalog,
  fakeEmailTokenInfo,
  fakeGooglePending,
  fakeNewMe,
  fakePasswordTokenInfo,
  fakeSignupCheck,
  fakeUndoInfo,
  mockApi,
  serveJson,
  stubTurnstile,
} from "../support/fakes";
import { expect, signIn, test } from "../support/fixtures";

// Sign-up, the emailed links, password reset, Google's finish and the sign-in challenge (docs/briefs/signup-auth.md
// 15.3). Every endpoint that would send mail or call Cloudflare or Google is route-mocked here (the fixtures block any
// other call to them), Cloudflare's script is a stub, and nothing leaves for challenges.cloudflare.com,
// accounts.google.com or the mail provider. The nginx headers are the stack's own.

const TOKEN = "q3Zx8LmP0aB2cD4eF6gHq3Zx8LmP0aB2cD4eF6gH123";
const EMAIL = "e2e-maya@example.com";
const NAME = "E2E Maya";
const SENT_KEY = "rag.auth.signupSent";

/** The configuration the signed-out pages read, mocked (the stack ships with sign-up closed). */
async function useConfig(page: Page, config: AuthConfig = fakeAuthConfig()): Promise<void> {
  await serveJson(page, "/auth/config", config);
}

/** Requests that left for Cloudflare's script host. */
function cloudflareRequests(page: Page): Request[] {
  const seen: Request[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).host === "challenges.cloudflare.com") seen.push(request);
  });
  return seen;
}

/** After a full page load into the app: a mocked new account without a plan, on the plan page. */
async function signedInAsNewAccount(page: Page, signedIn: () => boolean): Promise<void> {
  await page.route("**/api/auth/me", (route) =>
    signedIn()
      ? route.fulfill({ json: fakeNewMe({ username: "user-e2emaya1", email: EMAIL, display_name: NAME }) })
      : route.fulfill({ status: 401, json: { detail: "Not authenticated" } }),
  );
  await serveJson(page, "/billing/plans", fakeCatalog());
}

test.describe("sign-up", () => {
  test("Google first, then the form; sending it shows Check your inbox", async ({ page }) => {
    await useConfig(page);
    await stubTurnstile(page);
    const signups = await mockApi(page, "POST", "/auth/signup", { status: 202, body: ACCEPTED });
    await page.goto("/signup?next=/plans");

    await expect(page.getByRole("heading", { level: 1, name: "Create your account" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Continue with Google" })).toBeVisible();
    await expect(page.getByRole("separator", { name: "or sign up with email" })).toBeVisible();
    const create = page.getByRole("button", { name: "Create account" });
    await expect(create).toBeEnabled();
    await create.click();
    const name = page.getByRole("textbox", { name: "Your name" });
    await expect(name).toBeFocused();
    await expect(name).toHaveAttribute("autocomplete", "name");
    await expect(page.getByText("Enter your name.")).toBeVisible();
    await expect(page.getByText("Enter your email.")).toBeVisible();
    await expect(page.getByText("Tick the box to accept the terms.")).toBeVisible();

    await name.fill(`  E2E   Maya `);
    await page.getByRole("textbox", { name: "Email" }).fill(EMAIL);
    await page.getByLabel("Password", { exact: true }).fill("correct horse battery");
    await page.getByRole("checkbox", { name: /I agree to the Terms/ }).check();
    await create.click();

    await expect(page.getByRole("heading", { name: "Check your inbox" })).toBeFocused();
    await expect(page.getByText(`We sent an email to ${EMAIL}.`)).toBeVisible();
    await expect(page.getByRole("button", { name: /^Resend in / })).toHaveAttribute("aria-disabled", "true");
    expect(signups).toEqual([
      {
        name: NAME,
        email: EMAIL,
        password: "correct horse battery",
        accept_terms: true,
        turnstile_token: "XXXX.DUMMY.TOKEN.XXXX",
        next: "/plans",
      },
    ]);
    // A reload keeps the state.
    await page.reload();
    await expect(page.getByRole("heading", { name: "Check your inbox" })).toBeVisible();
  });

  test("Resend sends the link again once the minute is up", async ({ page }) => {
    await useConfig(page);
    await stubTurnstile(page);
    await page.addInitScript(
      ([key, value]) => sessionStorage.setItem(key, value),
      [SENT_KEY, JSON.stringify({ address: EMAIL, sends: [Date.now() - 5 * 60_000] })],
    );
    const resends = await mockApi(page, "POST", "/auth/signup/resend", { status: 202, body: ACCEPTED });
    await page.goto("/signup");

    await page.getByRole("button", { name: "Resend email" }).click();
    await expect(page.getByText("Sent again")).toBeVisible();
    expect(resends).toEqual([{ email: EMAIL, turnstile_token: "XXXX.DUMMY.TOKEN.XXXX" }]);
  });

  test("without Cloudflare's script, Create account keeps the warning and moves to Try again", async ({ page }) => {
    await useConfig(page);
    await page.route((url) => url.href === TURNSTILE_URL, (route) => route.abort());
    const signups = await mockApi(page, "POST", "/auth/signup", { status: 202, body: ACCEPTED });
    await page.goto("/signup");

    await expect(page.getByText("The security check couldn't load.", { exact: false })).toBeVisible();
    await page.getByRole("textbox", { name: "Your name" }).fill(NAME);
    await page.getByRole("textbox", { name: "Email" }).fill(EMAIL);
    await page.getByLabel("Password", { exact: true }).fill("correct horse battery");
    await page.getByRole("checkbox", { name: /I agree to the Terms/ }).check();
    await page.getByRole("button", { name: "Create account" }).click();

    await expect(page.getByRole("button", { name: "Try again" })).toBeFocused();
    await expect(page.getByText("Checking you're not a robot…")).toHaveCount(0);
    expect(signups).toEqual([]);
  });

  test("closed, the page says so and offers Sign in", async ({ page }) => {
    await useConfig(page, fakeAuthConfig({ signup: { open: false, email: false, google: false } }));
    await page.goto("/signup");
    await expect(page.getByText("New sign-ups are closed right now.")).toBeVisible();
    await expect(page.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });
});

test.describe("the sign-up link", () => {
  test("in the browser that signed up, one click creates the account and opens the plans", async ({ page }) => {
    let created = false;
    await signedInAsNewAccount(page, () => created);
    await mockApi(page, "POST", "/auth/signup/check", { body: fakeSignupCheck({ email: EMAIL }) });
    const completions = await mockApi(page, "POST", "/auth/signup/complete", () => {
      created = true;
      return { body: fakeNewMe({ username: "user-e2emaya1", email: EMAIL }) };
    });
    await page.goto(`/verify-email#t=${TOKEN}`);

    await expect(page.getByRole("heading", { name: "Finish creating your account" })).toBeVisible();
    await expect(page).toHaveURL(/\/verify-email$/); // the token left the address bar
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page).toHaveURL(/\/plans$/);
    await expect(page.getByRole("heading", { level: 1, name: /Choose a/ })).toBeVisible();
    expect(completions).toEqual([{ token: TOKEN, password: null }]);
  });

  test("on another device, the chosen password is the proof", async ({ page }) => {
    let created = false;
    await signedInAsNewAccount(page, () => created);
    await mockApi(page, "POST", "/auth/signup/check", { body: fakeSignupCheck({ email: EMAIL, needs_password: true }) });
    const completions = await mockApi(page, "POST", "/auth/signup/complete", (body) => {
      if ((body as { password?: string }).password !== "correct horse battery") {
        return { status: 400, body: { detail: "That isn't the password you chose when you signed up.", code: "wrong_password" } };
      }
      created = true;
      return { body: fakeNewMe({ username: "user-e2emaya1", email: EMAIL }) };
    });
    await page.goto(`/verify-email#t=${TOKEN}`);

    const password = page.getByLabel("The password you chose");
    await password.fill("not it");
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page.getByText("That isn't the password you chose when you signed up.")).toBeVisible();
    await password.fill("correct horse battery");
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page).toHaveURL(/\/plans$/);
    expect(completions).toHaveLength(2);
  });

  test("an expired link says so, and a link without its token is incomplete", async ({ page }) => {
    await mockApi(page, "POST", "/auth/signup/check", {
      status: 400,
      body: { detail: "This link has expired or was already used.", code: "link_invalid" },
    });
    await page.goto(`/verify-email#t=${TOKEN}`);
    await expect(page.getByRole("heading", { name: "This link has expired" })).toBeVisible();
    await page.goto("/verify-email");
    await expect(page.getByRole("heading", { name: "This link is incomplete" })).toBeVisible();
  });
});

test.describe("password reset", () => {
  test("Forgot password sends the link after the robot check", async ({ page }) => {
    await useConfig(page);
    await stubTurnstile(page);
    const requests = await mockApi(page, "POST", "/auth/password/forgot", { status: 202, body: ACCEPTED });
    await page.goto("/forgot-password");

    await page.getByRole("textbox", { name: "Email" }).fill(EMAIL);
    await page.getByRole("button", { name: "Send reset link" }).click();
    await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();
    expect(requests).toEqual([{ email: EMAIL, turnstile_token: "XXXX.DUMMY.TOKEN.XXXX" }]);
  });

  test("the reset link sets the new password and offers to disconnect Google", async ({ page }) => {
    await useConfig(page);
    await mockApi(page, "POST", "/auth/password/check", {
      body: fakePasswordTokenInfo({
        email: EMAIL,
        username: "user-e2emaya1",
        google: { email: "e***@gmail.com", suggest_disconnect: true },
      }),
    });
    const resets = await mockApi(page, "POST", "/auth/password/reset", { status: 204 });
    await page.goto(`/reset-password#t=${TOKEN}`);

    await expect(page.getByRole("heading", { name: "Choose a new password" })).toBeVisible();
    await expect(page.getByRole("checkbox", { name: /Also disconnect Google sign-in/ })).toBeChecked();
    await page.getByRole("textbox", { name: "New password" }).fill("a much longer passphrase");
    await page.getByRole("textbox", { name: "Repeat it" }).fill("a much longer passphrase");
    await page.getByRole("button", { name: "Save password" }).click();
    await expect(page.getByRole("heading", { name: "Password changed" })).toBeVisible();
    expect(resets).toEqual([{ token: TOKEN, new_password: "a much longer passphrase", disconnect_google: true }]);
  });
});

test.describe("email confirmation and undo", () => {
  test("signed in as the account that asked, Confirm this email confirms it", async ({ page, context, user }) => {
    await signIn(context, user);
    await mockApi(page, "POST", "/auth/email/check", { body: fakeEmailTokenInfo({ email: EMAIL }) });
    const confirmations = await mockApi(page, "POST", "/auth/email/verify", { body: { email: EMAIL } });
    await page.goto(`/confirm-email#t=${TOKEN}`);

    await page.getByRole("button", { name: "Confirm this email" }).click();
    await expect(page.getByRole("heading", { name: "Your email is confirmed" })).toBeVisible();
    expect(confirmations).toEqual([{ token: TOKEN }]);
  });

  test("signed out, the page sends the user to sign in", async ({ page }) => {
    await mockApi(page, "POST", "/auth/email/check", { body: fakeEmailTokenInfo({ email: EMAIL, signed_in: false }) });
    await page.goto(`/confirm-email#t=${TOKEN}`);
    await expect(page.getByRole("heading", { name: "Sign in to confirm" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Sign in", exact: true })).toHaveAttribute("href", "/login?next=/confirm-email");
  });

  test("the undo link puts the old address back", async ({ page }) => {
    await mockApi(page, "POST", "/auth/email/undo-check", { body: fakeUndoInfo({ old_email: EMAIL }) });
    const undos = await mockApi(page, "POST", "/auth/email/undo", { status: 204 });
    await page.goto(`/undo-email#t=${TOKEN}`);
    await page.getByRole("button", { name: "Undo the change" }).click();
    await expect(page.getByRole("heading", { name: "Your email is back" })).toBeVisible();
    expect(undos).toEqual([{ token: TOKEN }]);
  });
});

test.describe("Google sign-up", () => {
  test("from the sign-up page: the name and the terms, then the account and the plans", async ({ page }) => {
    let created = false;
    await signedInAsNewAccount(page, () => created);
    await mockApi(page, "GET", "/auth/google/pending", { body: fakeGooglePending({ email: "e2e.maya@gmail.com" }) });
    const finishes = await mockApi(page, "POST", "/auth/google/finish", () => {
      created = true;
      return { body: fakeNewMe({ username: "user-e2emaya1", email: "e2e.maya@gmail.com", display_name: NAME }) };
    });
    await page.goto("/signup/google");

    await expect(page.getByRole("heading", { name: "Create your AskNuvexusDoc account" })).toBeVisible();
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page.getByText("Enter your name.")).toBeVisible();
    await expect(page.getByText("Tick the box to accept the terms.")).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Your name" })).toBeFocused();
    await page.getByRole("textbox", { name: "Your name" }).fill(NAME);
    await page.getByRole("checkbox", { name: /I agree to the Terms/ }).check();
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page).toHaveURL(/\/plans$/);
    expect(finishes).toEqual([{ name: NAME, accept_terms: true }]);
  });

  test("from the sign-in page: I already have an account goes back to sign in", async ({ page }) => {
    await useConfig(page);
    await mockApi(page, "GET", "/auth/google/pending", { body: fakeGooglePending({ email: "e2e.maya@gmail.com", from: "login" }) });
    const cancels = await mockApi(page, "POST", "/auth/google/cancel", { status: 204 });
    await page.goto("/signup/google");

    await expect(page.getByRole("heading", { name: "No AskNuvexusDoc account uses this address yet" })).toBeVisible();
    await page.getByRole("button", { name: "I already have an account" }).click();
    await expect(page).toHaveURL(/\/login$/);
    await expect(
      page.getByText("Sign in with your email or username, then connect Google in Settings → Account."),
    ).toBeVisible();
    expect(cancels).toHaveLength(1);
  });
});

test.describe("sign-in", () => {
  test("after challenge_required, the robot check's token goes with the second try", async ({ page }) => {
    await useConfig(page);
    await stubTurnstile(page);
    const logins = await mockApi(page, "POST", "/auth/login", (body) =>
      (body as { turnstile_token?: string }).turnstile_token
        ? { status: 401, body: { detail: "Incorrect email, username or password." } }
        : { status: 401, body: { detail: "Confirm you're not a robot to continue.", code: "challenge_required" } },
    );
    await page.goto("/login");

    await identifierField(page).fill(EMAIL);
    await page.getByLabel("Password", { exact: true }).fill("wrong password");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText("Confirm you're not a robot, then sign in again.")).toBeVisible();
    await expect(page.locator(".auth-turnstile-caption")).toBeFocused();
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("alert")).toContainText("Incorrect email, username or password.");
    expect(logins).toEqual([
      { identifier: EMAIL, password: "wrong password" },
      { identifier: EMAIL, password: "wrong password", turnstile_token: "XXXX.DUMMY.TOKEN.XXXX" },
    ]);
  });

  test("no request leaves for Cloudflare while a session exists", async ({ page, context, user }) => {
    const cloudflare = cloudflareRequests(page);
    await signIn(context, user);
    await page.goto("/login");
    await expect(page).toHaveURL(/\/$/);
    await page.goto("/signup");
    await expect(page).toHaveURL(/\/$/);
    expect(cloudflare).toEqual([]);
  });
});

test.describe("nginx headers", () => {
  const CLOUDFLARE = "https://challenges.cloudflare.com";

  test("only the three signed-out pages may load Cloudflare's script", async ({ page }) => {
    for (const path of ["/login", "/signup", "/forgot-password"]) {
      const csp = (await page.request.get(path)).headers()["content-security-policy"] ?? "";
      expect(csp, path).toMatch(new RegExp(`script-src[^;]*${CLOUDFLARE}`));
      expect(csp, path).toMatch(new RegExp(`frame-src[^;]*${CLOUDFLARE}`));
    }
    for (const path of ["/", "/plans", "/verify-email", "/signup/google"]) {
      const csp = (await page.request.get(path)).headers()["content-security-policy"] ?? "";
      expect(csp, path).toContain("script-src");
      expect(csp, path).not.toContain(CLOUDFLARE);
    }
  });

  test("the emailed links' pages send no referrer, and every response carries HSTS", async ({ page }) => {
    for (const path of ["/verify-email", "/confirm-email", "/reset-password", "/undo-email"]) {
      expect((await page.request.get(path)).headers()["referrer-policy"], path).toBe("no-referrer");
    }
    for (const path of ["/", "/login", "/api/health"]) {
      expect((await page.request.get(path)).headers()["strict-transport-security"], path).toBe("max-age=31536000");
    }
  });

  test("the stub is the only Cloudflare script the pages ask for", async ({ page }) => {
    const cloudflare = cloudflareRequests(page);
    await useConfig(page);
    await stubTurnstile(page);
    await page.goto("/signup");
    await expect(page.getByText("Robot check (stub)")).toBeVisible();
    expect(cloudflare.map((request) => request.url())).toEqual([TURNSTILE_URL]);
  });
});

test.describe("Settings → Account", () => {
  test("shows the email, the sign-in methods and how each sign-in happened", async ({ page, context, user }) => {
    await signIn(context, user);
    const account: AccountInfo = {
      id: user.id,
      username: user.username,
      display_name: null,
      is_admin: false,
      password_managed_by_env: false,
      created_at: new Date().toISOString(),
      last_login_at: new Date().toISOString(),
      conversation_count: 0,
      recent_logins: [
        {
          id: 1,
          created_at: new Date().toISOString(),
          ip: "203.0.113.x",
          ip_private: false,
          browser: "Chrome 141",
          os: "macOS",
          device: "desktop",
          current: true,
          method: "google",
        },
      ],
      email: EMAIL,
      email_verified_at: new Date().toISOString(),
      pending_email: null,
      has_password: true,
      google: { email: "e2e.maya@gmail.com", linked_at: new Date().toISOString(), last_used_at: null },
      signup_source: "email",
      terms_accepted_at: new Date().toISOString(),
      email_change_needs: "password",
      stepup_until: null,
    };
    await useConfig(page);
    await serveJson(page, "/me/account", account);
    await page.goto("/?settings=account");

    const dialog = page.getByRole("dialog", { name: "Settings" });
    await expect(dialog.getByText(EMAIL)).toBeVisible();
    await expect(dialog.getByText("Confirmed")).toBeVisible();
    const methods = dialog.getByRole("list", { name: "Sign-in methods" });
    await expect(methods.getByText("Connected as e2e.maya@gmail.com", { exact: false })).toBeVisible();
    await expect(dialog.getByRole("list", { name: "Recent sign-ins" })).toContainText("with Google · 203.0.113.x");
    await expect(dialog.getByRole("button", { name: "Change email…" })).toBeVisible();
  });
});
