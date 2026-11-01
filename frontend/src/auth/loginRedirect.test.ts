import type { Location } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { loginRedirectState, postLoginPath } from "./loginRedirect";

describe("loginRedirectState", () => {
  it("keeps the path, query and hash of the page that was asked for", () => {
    const location: Location = { pathname: "/c/42", search: "?settings=usage", hash: "#m501", state: null, key: "k1" };
    expect(loginRedirectState(location)).toEqual({ from: { pathname: "/c/42", search: "?settings=usage", hash: "#m501" } });
  });
});

describe("postLoginPath", () => {
  it("returns to the in-app page from the redirect state", () => {
    expect(postLoginPath({ from: { pathname: "/c/42", search: "?settings=usage", hash: "#m501" } })).toBe(
      "/c/42?settings=usage#m501",
    );
    expect(postLoginPath({ from: { pathname: "/admin" } })).toBe("/admin");
  });

  it.each([
    ["no state", null],
    ["an unrelated state", { other: true }],
    ["a missing path", { from: {} }],
    ["a non-string path", { from: { pathname: 42 } }],
    ["a relative path", { from: { pathname: "admin" } }],
    ["a protocol-relative URL", { from: { pathname: "//evil.example/phish" } }],
    ["an absolute URL", { from: { pathname: "https://evil.example" } }],
    ["the login page itself", { from: { pathname: "/login", search: "?next=1" } }],
  ])("falls back to the chat for %s", (_, state) => {
    expect(postLoginPath(state)).toBe("/");
  });

  it("ignores a query or hash that isn't a string", () => {
    expect(postLoginPath({ from: { pathname: "/c/7", search: 1, hash: ["#x"] } })).toBe("/c/7");
  });

  it("goes to the page ?next= names when there is no redirect state (a link from another page)", () => {
    expect(postLoginPath(null, "?next=/confirm-email")).toBe("/confirm-email");
    expect(postLoginPath(undefined, "?next=%2Fplans%3Fsettings%3Daccount")).toBe("/plans?settings=account");
    // The redirect state wins.
    expect(postLoginPath({ from: { pathname: "/admin" } }, "?next=/plans")).toBe("/admin");
  });

  it.each([
    ["an off-site URL", "?next=//evil.example"],
    ["the API", "?next=/api/auth/me"],
    ["a signed-out page", "?next=/signup"],
    ["nothing", ""],
  ])("falls back to the chat for ?next= naming %s", (_, search) => {
    expect(postLoginPath(null, search)).toBe("/");
  });

  it("never returns to a signed-out page from the redirect state", () => {
    expect(postLoginPath({ from: { pathname: "/forgot-password", search: "", hash: "" } })).toBe("/");
  });
});
