import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { deferred, errorResponse, jsonResponse, stubFetch } from "../../test/http";
import GoogleButton, { GoogleMark, OrRule } from "./GoogleButton";

const GOOGLE_URL = "https://accounts.google.com/o/oauth2/v2/auth?client_id=c&state=s&scope=openid+email";
const INSTAGRAM =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/22G86 " +
  "Instagram 401.0.0.27.79 (iPhone15,3; iOS 18_6; en_IN; en; scale=3.00; 1290x2796; 789123456)";

/** window.location at `href`, with assign recorded (jsdom can't navigate). */
function stubLocation(href = "https://asknuvexusdoc.com/login?next=%2Fplans") {
  const url = new URL(href);
  const assign = vi.fn();
  vi.stubGlobal("location", {
    href: url.href,
    origin: url.origin,
    hostname: url.hostname,
    pathname: url.pathname,
    search: url.search,
    hash: url.hash,
    assign,
  });
  return assign;
}

function startReturns(url: string) {
  return stubFetch({ "POST /auth/google/start": { authorize_url: url } });
}

const button = () => screen.getByRole("button", { name: "Continue with Google" });

afterEach(() => {
  vi.useRealTimers();
});

describe("GoogleButton", () => {
  it.each([
    { intent: "signin", from: "login", next: "/plans" },
    { intent: "signin", from: "signup" },
    { intent: "link", password: "correct horse" },
    { intent: "reauth", next: "/?settings=account&change=email" },
  ] as const)("starts the flow (%j) and sends the tab to Google", async (props) => {
    const assign = stubLocation();
    const calls = startReturns(GOOGLE_URL);
    const onError = vi.fn();
    const user = userEvent.setup();
    render(<GoogleButton {...props} onError={onError} />);

    await user.click(button());
    await waitFor(() => expect(assign).toHaveBeenCalledWith(GOOGLE_URL));
    expect(calls).toMatchObject([{ method: "POST", path: "/auth/google/start", body: props }]);
    expect(Object.keys(calls[0].body as object).sort()).toEqual(Object.keys(props).sort());
    expect(onError).not.toHaveBeenCalled();
    expect(button()).toHaveAttribute("aria-busy", "true"); // the page is leaving
  });

  it("is Google's button: the G, the one label, both sizes", () => {
    const { container, rerender } = render(<GoogleButton intent="signin" onError={() => {}} />);
    expect(button()).toHaveClass("auth-google");
    expect(button()).not.toHaveClass("auth-google-sm");
    expect(button()).toHaveAttribute("type", "button");
    const mark = container.querySelector(".auth-google-g");
    expect(mark).toHaveAttribute("aria-hidden", "true");
    expect(mark).toHaveStyle({ width: "18px", height: "18px" });
    expect(mark?.querySelectorAll("svg path")).toHaveLength(5);

    rerender(<GoogleButton intent="reauth" size="sm" onError={() => {}} />);
    expect(button()).toHaveClass("auth-google auth-google-sm");
  });

  it("stays enabled, and starts only when canStart says so", async () => {
    const assign = stubLocation();
    const calls = startReturns(GOOGLE_URL);
    const canStart = vi.fn().mockReturnValueOnce(false).mockReturnValue(true);
    const user = userEvent.setup();
    render(<GoogleButton intent="link" password="correct horse" size="sm" canStart={canStart} onError={() => {}} />);
    expect(button()).toBeEnabled();

    await user.click(button());
    expect(calls).toEqual([]);
    expect(button()).not.toHaveAttribute("aria-busy");
    await user.click(button());
    await waitFor(() => expect(assign).toHaveBeenCalledWith(GOOGLE_URL));
    expect(canStart).toHaveBeenCalledTimes(2);
  });

  it("shows that it is busy while the flow starts, and takes one click", async () => {
    stubLocation();
    const reply = deferred<Response>();
    const calls = stubFetch({ "POST /auth/google/start": () => reply.promise });
    const user = userEvent.setup();
    const { container } = render(<GoogleButton intent="signin" onError={() => {}} />);

    await user.click(button());
    expect(button()).toHaveAttribute("aria-busy", "true");
    expect(button()).toHaveAccessibleName("Continue with Google");
    expect(container.querySelector(".spinner")).not.toBeNull();
    await user.click(button());
    expect(calls).toHaveLength(1);
    reply.resolve(errorResponse(503, "Google sign-in isn't set up."));
    await waitFor(() => expect(button()).not.toHaveAttribute("aria-busy"));
  });

  it.each([
    ["an http page", "http://accounts.google.com/o/oauth2/v2/auth"],
    ["another host", "https://accounts.google.com.evil.example/o/oauth2/v2/auth"],
    ["a look-alike", "https://evil.example/accounts.google.com"],
    ["a port", "https://accounts.google.com:8443/o/oauth2/v2/auth"],
    ["credentials", "https://user@accounts.google.com/o/oauth2/v2/auth"],
    ["a script", "javascript:alert(1)"],
    ["a local fake on the public site", "http://localhost:18091/authorize"],
    ["nonsense", "not a url"],
  ])("refuses to leave for %s", async (_, url) => {
    const assign = stubLocation();
    startReturns(url);
    const onError = vi.fn();
    const user = userEvent.setup();
    render(<GoogleButton intent="signin" onError={onError} />);

    await user.click(button());
    await waitFor(() => expect(onError).toHaveBeenCalledWith("We couldn't sign you in with Google. Try again."));
    expect(assign).not.toHaveBeenCalled();
    expect(button()).not.toHaveAttribute("aria-busy");
  });

  it("follows a local fake Google when the app itself runs locally", async () => {
    const assign = stubLocation("http://localhost:18090/login");
    startReturns("http://127.0.0.1:18091/authorize?state=s");
    const user = userEvent.setup();
    render(<GoogleButton intent="signin" onError={() => {}} />);
    await user.click(button());
    await waitFor(() => expect(assign).toHaveBeenCalledWith("http://127.0.0.1:18091/authorize?state=s"));
  });

  it("hands the server's refusal to the page and can be pressed again", async () => {
    const assign = stubLocation();
    const replies = [
      jsonResponse({ detail: "Your password is incorrect.", code: "wrong_password" }, { status: 400 }),
      jsonResponse({ authorize_url: GOOGLE_URL }),
    ];
    stubFetch({ "POST /auth/google/start": () => replies.shift() });
    const onError = vi.fn();
    const user = userEvent.setup();
    render(<GoogleButton intent="link" password="wrong" onError={onError} />);

    await user.click(button());
    await waitFor(() => expect(onError).toHaveBeenCalledWith("Your password is incorrect."));
    expect(button()).toBeEnabled();
    await user.click(button());
    await waitFor(() => expect(assign).toHaveBeenCalledWith(GOOGLE_URL));
  });

  it("explains a network failure", async () => {
    stubLocation();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const onError = vi.fn();
    const user = userEvent.setup();
    render(<GoogleButton intent="signin" onError={onError} />);
    await user.click(button());
    await waitFor(() => expect(onError).toHaveBeenCalledWith("Cannot reach the server. Check that the app is running."));
  });

  it("is ready again when the browser brings the page back from its cache", async () => {
    stubLocation();
    startReturns(GOOGLE_URL);
    const user = userEvent.setup();
    render(<GoogleButton intent="signin" onError={() => {}} />);
    await user.click(button());
    await waitFor(() => expect(button()).toHaveAttribute("aria-busy", "true"));

    act(() => window.dispatchEvent(Object.assign(new Event("pageshow"), { persisted: false })));
    expect(button()).toHaveAttribute("aria-busy", "true");
    act(() => window.dispatchEvent(Object.assign(new Event("pageshow"), { persisted: true })));
    expect(button()).not.toHaveAttribute("aria-busy");
  });

  describe("in an in-app browser", () => {
    it("offers to copy the link instead, since Google refuses to sign in there", async () => {
      vi.spyOn(navigator, "userAgent", "get").mockReturnValue(INSTAGRAM);
      vi.useFakeTimers({ shouldAdvanceTime: true });
      stubLocation("https://asknuvexusdoc.com/signup?next=%2Fplans");
      const calls = stubFetch({});
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<GoogleButton intent="signin" from="signup" onError={() => {}} />);

      expect(screen.queryByRole("button", { name: "Continue with Google" })).not.toBeInTheDocument();
      expect(
        screen.getByText("Google sign-in doesn't work inside this app. Open asknuvexusdoc.com in Chrome or Safari."),
      ).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Copy link" }));
      expect(await navigator.clipboard.readText()).toBe("https://asknuvexusdoc.com/signup?next=%2Fplans");
      expect(screen.getByRole("button", { name: "Link copied" })).toBeInTheDocument();
      expect(screen.getByRole("status")).toHaveTextContent("Link copied");

      act(() => vi.advanceTimersByTime(3_000));
      expect(screen.getByRole("button", { name: "Copy link" })).toBeInTheDocument();
      expect(screen.getByRole("status")).toBeEmptyDOMElement();
      expect(calls).toHaveLength(0);
    });

    it("shows the link to copy by hand when the clipboard is out of reach", async () => {
      vi.spyOn(navigator, "userAgent", "get").mockReturnValue(INSTAGRAM);
      stubLocation("https://asknuvexusdoc.com/login");
      const user = userEvent.setup();
      vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new DOMException("Denied", "NotAllowedError"));
      render(<GoogleButton intent="signin" from="login" onError={() => {}} />);

      await user.click(screen.getByRole("button", { name: "Copy link" }));
      expect(await screen.findByText("https://asknuvexusdoc.com/login")).toHaveClass("auth-inapp-url");
    });
  });
});

describe("GoogleMark", () => {
  it("draws the G at the size asked for", () => {
    const { container } = render(<GoogleMark size={14} />);
    expect(container.firstElementChild).toHaveStyle({ width: "14px", height: "14px" });
  });
});

describe("OrRule", () => {
  it("is a separator named by its word", () => {
    render(<OrRule label="or sign up with email" />);
    expect(screen.getByRole("separator", { name: "or sign up with email" })).toHaveTextContent("or sign up with email");
  });
});
