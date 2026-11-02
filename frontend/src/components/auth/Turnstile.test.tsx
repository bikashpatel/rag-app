import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../../auth/AuthContext";
import { reloadForTurnstile, turnstileAllowed } from "../../lib/authDocument";
import { makeMe } from "../../test/fixtures";
import { deferred, errorResponse, stubFetch } from "../../test/http";
import { applyAppearance, DEFAULT_PREFERENCES } from "../../theme/appearance";
import Turnstile, { TURNSTILE_SCRIPT_URL, TurnstileWait, type TurnstileApi } from "./Turnstile";

vi.mock("../../lib/authDocument", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/authDocument")>()),
  turnstileAllowed: vi.fn(),
  reloadForTurnstile: vi.fn(),
}));

type Options = Parameters<TurnstileApi["render"]>[1];

const TOKEN = "XXXX.DUMMY.TOKEN.XXXX";
const COULD_NOT_LOAD =
  "The security check couldn't load. Check your connection or turn off blockers for this site, then try again.";

/** Cloudflare's window.turnstile, rendering an iframe per widget; the test drives the callbacks. */
function fakeTurnstile() {
  const options = new Map<string, Options>();
  let count = 0;
  const api = {
    render: vi.fn((container: HTMLElement, widget: Options) => {
      const id = `widget-${++count}`;
      options.set(id, widget);
      const frame = document.createElement("iframe");
      frame.title = "Widget containing a Cloudflare security challenge";
      container.append(frame);
      return id;
    }),
    reset: vi.fn(),
    remove: vi.fn((id: string) => {
      options.delete(id);
    }),
    /** The live widget's options (the last one rendered). */
    widget(): Options {
      const last = [...options.values()].at(-1);
      if (!last) throw new Error("No widget");
      return last;
    },
  };
  return api;
}

function scripts(): HTMLScriptElement[] {
  return [...document.head.querySelectorAll<HTMLScriptElement>("script")].filter(
    (script) => script.src === TURNSTILE_SCRIPT_URL,
  );
}

/** Cloudflare's script arrives: window.turnstile exists, then its load event fires. */
function loadScript(api: ReturnType<typeof fakeTurnstile>) {
  vi.stubGlobal("turnstile", api);
  act(() => {
    scripts().forEach((script) => script.dispatchEvent(new Event("load")));
  });
}

function signedOut() {
  return stubFetch({ "GET /auth/me": () => errorResponse(401, "Not authenticated") });
}

function renderWidget(ui?: ReactNode) {
  const widget = ui ?? <Turnstile siteKey="1x00000000000000000000AA" action="signup" onToken={() => {}} />;
  return render(<AuthProvider>{widget}</AuthProvider>);
}

const loadingSlot = () => screen.queryByText("Loading the security check…");

beforeEach(() => {
  vi.mocked(turnstileAllowed).mockReturnValue(true);
  vi.mocked(reloadForTurnstile).mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  // A script still loading would be shared with the next test: fail it, which forgets it.
  scripts().forEach((script) => {
    script.dispatchEvent(new Event("error"));
    script.remove();
  });
  applyAppearance(DEFAULT_PREFERENCES);
  vi.useRealTimers();
});

describe("Turnstile", () => {
  it("loads Cloudflare's script once, and only after the session check answered 401", async () => {
    const me = deferred<Response>();
    stubFetch({ "GET /auth/me": () => me.promise });
    const tokens: (string | null)[] = [];
    const { container } = renderWidget(
      <>
        <Turnstile siteKey="1x00000000000000000000AA" action="signup" onToken={(token) => tokens.push(token)} />
        <Turnstile siteKey="1x00000000000000000000AA" action="resend" onToken={() => {}} />
      </>,
    );
    expect(scripts()).toHaveLength(0);
    expect(container.querySelectorAll(".auth-turnstile")).toHaveLength(2); // the slots hold their place

    me.resolve(errorResponse(401, "Not authenticated"));
    await waitFor(() => expect(scripts()).toHaveLength(1));
    const [script] = scripts();
    expect(script.src).toBe("https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit");
    expect(script.async).toBe(true);
    expect(script.hasAttribute("integrity")).toBe(false);
    expect(screen.getAllByText("Loading the security check…")).toHaveLength(2);

    const api = fakeTurnstile();
    loadScript(api);
    await waitFor(() => expect(api.render).toHaveBeenCalledTimes(2));
    expect(loadingSlot()).not.toBeInTheDocument();
    expect(container.querySelectorAll(".auth-turnstile-widget iframe")).toHaveLength(2);
    expect(scripts()).toHaveLength(1);
  });

  it("never loads for a signed-in session", async () => {
    stubFetch({ "GET /auth/me": makeMe() });
    const { container } = renderWidget();
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
    });
    expect(scripts()).toHaveLength(0);
    expect(loadingSlot()).not.toBeInTheDocument();
    expect(container.querySelector(".auth-turnstile")).toBeInTheDocument();
  });

  it("renders the Managed widget in the app's theme and passes its tokens on", async () => {
    signedOut();
    const onToken = vi.fn();
    const { container } = renderWidget(<Turnstile siteKey="1x00000000000000000000AA" action="reset" onToken={onToken} />);
    await waitFor(() => expect(scripts()).toHaveLength(1));
    const api = fakeTurnstile();
    loadScript(api);
    await waitFor(() => expect(api.render).toHaveBeenCalledTimes(1));

    const [target, options] = api.render.mock.calls[0];
    expect(target).toBe(container.querySelector(".auth-turnstile-widget"));
    expect(options).toEqual({
      sitekey: "1x00000000000000000000AA",
      action: "reset",
      theme: "light",
      size: "flexible",
      appearance: "always",
      "refresh-expired": "auto",
      "response-field": false,
      language: "auto",
      callback: expect.any(Function),
      "expired-callback": expect.any(Function),
      "error-callback": expect.any(Function),
    });

    act(() => options.callback(TOKEN));
    expect(onToken).toHaveBeenLastCalledWith(TOKEN);
    act(() => options["expired-callback"]());
    expect(onToken).toHaveBeenLastCalledWith(null);
  });

  it("renders again in the new theme when the app's theme changes", async () => {
    applyAppearance({ ...DEFAULT_PREFERENCES, theme: "dark" });
    signedOut();
    const onToken = vi.fn();
    renderWidget(<Turnstile siteKey="1x00000000000000000000AA" action="login" onToken={onToken} />);
    await waitFor(() => expect(scripts()).toHaveLength(1));
    const api = fakeTurnstile();
    loadScript(api);
    await waitFor(() => expect(api.render).toHaveBeenCalledTimes(1));
    expect(api.widget().theme).toBe("dark");
    act(() => api.widget().callback(TOKEN));

    act(() => applyAppearance({ ...DEFAULT_PREFERENCES, theme: "light" }));
    await waitFor(() => expect(api.render).toHaveBeenCalledTimes(2));
    expect(api.remove).toHaveBeenCalledWith("widget-1");
    expect(api.widget().theme).toBe("light");
    expect(onToken).toHaveBeenLastCalledWith(null); // the old widget's token went with it
  });

  it("resets after each submit (tokens are single use) and removes the widget when it goes", async () => {
    signedOut();
    const onToken = vi.fn();
    const widget = (resetSignal: number) => (
      <Turnstile siteKey="1x00000000000000000000AA" action="signup" onToken={onToken} resetSignal={resetSignal} />
    );
    const { rerender, unmount } = renderWidget(widget(0));
    await waitFor(() => expect(scripts()).toHaveLength(1));
    const api = fakeTurnstile();
    loadScript(api);
    await waitFor(() => expect(api.render).toHaveBeenCalledTimes(1));
    act(() => api.widget().callback(TOKEN));
    expect(api.reset).not.toHaveBeenCalled();

    rerender(<AuthProvider>{widget(1)}</AuthProvider>);
    expect(api.reset).toHaveBeenCalledWith("widget-1");
    expect(onToken).toHaveBeenLastCalledWith(null);
    rerender(<AuthProvider>{widget(1)}</AuthProvider>);
    expect(api.reset).toHaveBeenCalledTimes(1);
    expect(api.render).toHaveBeenCalledTimes(1);

    unmount();
    expect(api.remove).toHaveBeenCalledWith("widget-1");
  });

  it("says so when the script doesn't load within 15 seconds, and tries again", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"], shouldAdvanceTime: true });
    signedOut();
    const onToken = vi.fn();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderWidget(<Turnstile siteKey="1x00000000000000000000AA" action="signup" onToken={onToken} />);
    await waitFor(() => expect(scripts()).toHaveLength(1));

    act(() => vi.advanceTimersByTime(14_000));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1_000));
    expect(screen.getByRole("alert")).toHaveTextContent(COULD_NOT_LOAD);
    expect(loadingSlot()).not.toBeInTheDocument();
    expect(onToken).toHaveBeenLastCalledWith(null);

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(loadingSlot()).toBeInTheDocument();
    expect(scripts()).toHaveLength(1); // the same load, still on its way
    const api = fakeTurnstile();
    loadScript(api);
    await waitFor(() => expect(api.render).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("loads the script afresh after it failed", async () => {
    signedOut();
    const user = userEvent.setup();
    renderWidget();
    await waitFor(() => expect(scripts()).toHaveLength(1));
    act(() => scripts()[0].dispatchEvent(new Event("error")));
    expect(await screen.findByRole("alert")).toHaveTextContent(COULD_NOT_LOAD);
    expect(scripts()).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(scripts()).toHaveLength(1));
  });

  it("treats a script that loaded without starting as failed", async () => {
    signedOut();
    renderWidget();
    await waitFor(() => expect(scripts()).toHaveLength(1));
    act(() => scripts()[0].dispatchEvent(new Event("load")));
    expect(await screen.findByRole("alert")).toHaveTextContent(COULD_NOT_LOAD);
  });

  it("shows the widget's error, recovers when Cloudflare retries, and renders again on Try again", async () => {
    signedOut();
    const onToken = vi.fn();
    const user = userEvent.setup();
    renderWidget(<Turnstile siteKey="1x00000000000000000000AA" action="signup" onToken={onToken} />);
    await waitFor(() => expect(scripts()).toHaveLength(1));
    const api = fakeTurnstile();
    loadScript(api);
    await waitFor(() => expect(api.render).toHaveBeenCalledTimes(1));

    act(() => api.widget()["error-callback"]());
    expect(screen.getByRole("alert")).toHaveTextContent(COULD_NOT_LOAD);
    expect(onToken).toHaveBeenLastCalledWith(null);
    act(() => api.widget().callback(TOKEN)); // Cloudflare's own retry succeeded
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(onToken).toHaveBeenLastCalledWith(TOKEN);

    act(() => api.widget()["error-callback"]());
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(api.render).toHaveBeenCalledTimes(2));
    expect(api.remove).toHaveBeenCalledWith("widget-1");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps an error Cloudflare reports while it renders", async () => {
    signedOut();
    renderWidget();
    await waitFor(() => expect(scripts()).toHaveLength(1));
    const api = fakeTurnstile();
    api.render.mockImplementation((_, options: Options) => {
      options["error-callback"]();
      return "widget-1";
    });
    loadScript(api);
    expect(await screen.findByRole("alert")).toHaveTextContent(COULD_NOT_LOAD);
  });

  it("treats a widget that won't render as failed", async () => {
    signedOut();
    renderWidget();
    await waitFor(() => expect(scripts()).toHaveLength(1));
    const api = fakeTurnstile();
    api.render.mockImplementation(() => {
      throw new Error("Invalid sitekey");
    });
    loadScript(api);
    expect(await screen.findByRole("alert")).toHaveTextContent(COULD_NOT_LOAD);
  });

  it("points to the page's Google button when the check can't load", async () => {
    signedOut();
    const user = userEvent.setup();
    renderWidget(
      <>
        <button type="button" className="auth-google">
          Continue with Google
        </button>
        <Turnstile siteKey="1x00000000000000000000AA" action="signup" onToken={() => {}} />
      </>,
    );
    await waitFor(() => expect(scripts()).toHaveLength(1));
    act(() => scripts()[0].dispatchEvent(new Event("error")));

    await user.click(await screen.findByRole("button", { name: "or continue with Google" }));
    expect(screen.getByRole("button", { name: "Continue with Google" })).toHaveFocus();
  });

  it("tells its form when the warning is up, and hands focus to Try again when the form asks", async () => {
    signedOut();
    const onFailed = vi.fn();
    const user = userEvent.setup();
    const widget = (focusSignal: number) => (
      <Turnstile
        siteKey="1x00000000000000000000AA"
        action="signup"
        onToken={() => {}}
        onFailed={onFailed}
        focusSignal={focusSignal}
      />
    );
    const { rerender, unmount } = render(<AuthProvider>{widget(0)}</AuthProvider>);
    await waitFor(() => expect(scripts()).toHaveLength(1));
    expect(onFailed).toHaveBeenLastCalledWith(false);
    act(() => scripts()[0].dispatchEvent(new Event("error")));
    await screen.findByRole("alert");
    await waitFor(() => expect(onFailed).toHaveBeenLastCalledWith(true));

    const retry = screen.getByRole("button", { name: "Try again" });
    expect(retry).toHaveAccessibleDescription(COULD_NOT_LOAD);
    expect(retry).not.toHaveFocus();
    rerender(<AuthProvider>{widget(1)}</AuthProvider>);
    expect(retry).toHaveFocus();

    await user.click(retry);
    await waitFor(() => expect(onFailed).toHaveBeenLastCalledWith(false));
    onFailed.mockClear();
    unmount();
    expect(onFailed).toHaveBeenCalledWith(false);
  });

  it("offers no Google link on a page without the button", async () => {
    signedOut();
    renderWidget();
    await waitFor(() => expect(scripts()).toHaveLength(1));
    act(() => scripts()[0].dispatchEvent(new Event("error")));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "or continue with Google" })).not.toBeInTheDocument();
  });

  it("in a document whose CSP doesn't allow it, reloads the page instead of loading the script", async () => {
    vi.mocked(turnstileAllowed).mockReturnValue(false);
    signedOut();
    renderWidget();
    await waitFor(() => expect(reloadForTurnstile).toHaveBeenCalled());
    expect(scripts()).toHaveLength(0);
    expect(loadingSlot()).toBeInTheDocument();
  });

  it("shows the couldn't-load state once the page already reloaded for it", async () => {
    vi.mocked(turnstileAllowed).mockReturnValue(false);
    vi.mocked(reloadForTurnstile).mockReturnValue(false);
    signedOut();
    renderWidget();
    expect(await screen.findByRole("alert")).toHaveTextContent(COULD_NOT_LOAD);
    expect(scripts()).toHaveLength(0);
  });

  it("when the session couldn't be checked, offers to check it again", async () => {
    const replies = [errorResponse(503, "Service unavailable"), errorResponse(401, "Not authenticated")];
    const calls = stubFetch({ "GET /auth/me": () => replies.shift() });
    const user = userEvent.setup();
    renderWidget();
    expect(await screen.findByRole("alert")).toHaveTextContent(COULD_NOT_LOAD);
    expect(scripts()).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(scripts()).toHaveLength(1));
    expect(calls.filter((call) => call.path === "/auth/me")).toHaveLength(2);
  });
});

describe("TurnstileWait", () => {
  it("says that the form waits for the check, in a polite live region that is always there", () => {
    const { container, rerender } = render(<TurnstileWait waiting={false} />);
    const line = container.querySelector(".auth-turnstile-wait");
    expect(line).toHaveAttribute("aria-live", "polite");
    expect(line).toBeEmptyDOMElement();
    rerender(<TurnstileWait waiting />);
    expect(line).toHaveTextContent("Checking you're not a robot…");
  });
});
