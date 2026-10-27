import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Me, Preferences } from "../../api/types";
import { formatNumber } from "../../lib/format";
import {
  makeAdminMe,
  makeLapsedPlanAccess,
  makeLimits,
  makeMe,
  makePaidPlanAccess,
  makePlanAccess,
  makePreferences,
  NOW,
} from "../../test/fixtures";
import { deferred, errorResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import { renderWithAuth } from "../../test/render";
import AvatarMenu from "./AvatarMenu";

async function setup(me: Me = makeMe(), { imagesOffered = true, patch }: { imagesOffered?: boolean; patch?: Route } = {}) {
  const calls = stubFetch({
    "GET /auth/me": me,
    "PATCH /me/preferences": patch ?? ((call: FetchCall) => ({ ...me.preferences, ...(call.body as Partial<Preferences>) })),
  });
  const user = userEvent.setup();
  const handlers = { onOpenSettings: vi.fn(), onPreferenceError: vi.fn(), onLogout: vi.fn() };
  const view = await renderWithAuth(<AvatarMenu user={me} imagesOffered={imagesOffered} {...handlers} />);
  return { user, view, calls, ...handlers };
}

const openMenu = async (user: ReturnType<typeof userEvent.setup>, name: string | RegExp = /^Account: /) =>
  user.click(screen.getByRole("button", { name }));

const themeItem = (name: string) => screen.getByRole("menuitemradio", { name });

describe("AvatarMenu", () => {
  it("names the account and role, and describes this period's chat allowance and images", async () => {
    const { user } = await setup();
    await openMenu(user, "Account: maya.chen");
    expect(screen.getByRole("menu", { name: "Account: maya.chen, Member" })).toBeInTheDocument();
    expect(screen.getByText("Complimentary Professional")).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "View usage" })).toHaveAccessibleDescription(
      `Chat allowance this period: ${formatNumber(120_000)} of ${formatNumber(2_000_000)}. Images this period: 3 of 50.`,
    );
    expect(screen.getByText("Chat allowance this period")).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Admin" })).not.toBeInTheDocument();
  });

  it("counts the chat allowance in standard tokens, not plain tokens", async () => {
    const me = makeMe();
    const { user } = await setup({ ...me, usage: { ...me.usage, chat_tokens: 1_900_000, chat_allowance_used: 1_900_000 } });
    await openMenu(user, "Account: maya.chen, usage almost at the limit");
    expect(screen.getByRole("menuitem", { name: "View usage" })).toHaveAccessibleDescription(
      new RegExp(`^Chat allowance this period: ${formatNumber(1_900_000)} of ${formatNumber(2_000_000)}\\. Almost used up\\.`),
    );
  });

  it("puts the plan in the subtitle: until when, paid ahead, complimentary or none", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
    const paid = await setup(makeMe({ plan: makePaidPlanAccess() }));
    await openMenu(paid.user);
    expect(screen.getByText("Standard · until 7 Nov")).toBeInTheDocument();
    paid.view.unmount();

    const ahead = await setup(
      makeMe({ plan: makePaidPlanAccess({ paid_through: "2026-12-07T18:30:00Z", valid_through: "2026-12-07" }) }),
    );
    await openMenu(ahead.user);
    expect(screen.getByText("Standard · paid through 7 Dec")).toBeInTheDocument();
    ahead.view.unmount();

    const comp = await setup(
      makeMe({
        plan: makePaidPlanAccess({
          complimentary: true,
          period_end: "2026-11-25T18:30:00Z",
          paid_through: "2026-11-25T18:30:00Z",
          valid_through: "2026-11-25",
        }),
      }),
    );
    await openMenu(comp.user);
    expect(screen.getByText("Complimentary Standard · until 25 Nov")).toBeInTheDocument();
  });

  it("marks Plan & billing with the days left while the plan ends soon, and opens that tab", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
    const ending = makePaidPlanAccess({
      expiring_soon: true,
      period_end: "2026-10-24T18:30:00Z",
      paid_through: "2026-10-24T18:30:00Z",
      valid_through: "2026-10-24",
    });
    const { user, onOpenSettings } = await setup(makeMe({ plan: ending }));
    await openMenu(user);
    const item = screen.getByRole("menuitem", { name: /^Plan & billing/ });
    expect(item).toHaveTextContent("4 days left");
    await user.click(item);
    expect(onOpenSettings).toHaveBeenLastCalledWith("billing");
  });

  it("flags the avatar on the last day of a plan with nothing after it", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
    const lastDay = makePaidPlanAccess({
      expiring_soon: true,
      period_end: "2026-10-20T18:30:00Z",
      paid_through: "2026-10-20T18:30:00Z",
      valid_through: "2026-10-20",
    });
    const { user } = await setup(makeMe({ plan: lastDay }));
    await openMenu(user, "Account: maya.chen, your plan ends today");
    expect(screen.getByRole("menuitem", { name: /^Plan & billing/ })).toHaveTextContent("Last day");
  });

  it("shows no usage for a user without a plan, and says so on Plan & billing", async () => {
    const { user } = await setup(makeMe({ plan: makeLapsedPlanAccess("none") }));
    await openMenu(user);
    expect(screen.getByText("No active plan")).toBeInTheDocument();
    expect(screen.queryByText(/Chat allowance/)).not.toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "View usage" })).not.toHaveAccessibleDescription();
    expect(screen.getByRole("menuitem", { name: /^Plan & billing/ })).toHaveTextContent("No plan");
  });

  it("shows unlimited usage for the admin, without images when none are offered or made", async () => {
    const admin = makeAdminMe();
    const { user, view } = await setup({ ...admin, usage: { ...admin.usage, images: 0 } }, { imagesOffered: false });
    await openMenu(user, "Account: admin");
    expect(screen.getByRole("menu", { name: "Account: admin, Administrator" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "View usage" })).toHaveAccessibleDescription(
      `Chat allowance this month: ${formatNumber(120_000)}, unlimited.`,
    );
    await user.click(screen.getByRole("menuitem", { name: "Admin" }));
    expect(view.location()).toBe("/admin");
  });

  it("opens Settings on the usage or general tab", async () => {
    const { user, onOpenSettings } = await setup();
    await openMenu(user);
    await user.click(screen.getByRole("menuitem", { name: "View usage" }));
    expect(onOpenSettings).toHaveBeenLastCalledWith("usage");
    await openMenu(user);
    await user.click(screen.getByRole("menuitem", { name: "Settings" }));
    expect(onOpenSettings).toHaveBeenLastCalledWith("general");
  });

  it("logs out", async () => {
    const { user, onLogout } = await setup();
    await openMenu(user);
    await user.click(screen.getByRole("menuitem", { name: "Log out" }));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it("switches the theme in place, keeps the menu open and saves it", async () => {
    const { user, calls } = await setup(makeMe({ preferences: makePreferences({ theme: "light" }) }));
    await openMenu(user);
    expect(screen.getByRole("group", { name: "Theme" })).toContainElement(themeItem("Dark"));
    expect(themeItem("Light")).toHaveAttribute("aria-checked", "true");

    await user.click(themeItem("Dark"));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(themeItem("Dark")).toHaveAttribute("aria-checked", "true");
    expect(themeItem("Light")).toHaveAttribute("aria-checked", "false");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(calls).toContainEqual(expect.objectContaining({ method: "PATCH", path: "/me/preferences", body: { theme: "dark" } }));
  });

  it("reverts and reports a theme that couldn't be saved while the menu is open", async () => {
    const { user, onPreferenceError } = await setup(makeMe({ preferences: makePreferences({ theme: "light" }) }), {
      patch: errorResponse(503, "The server is busy."),
    });
    await openMenu(user);
    await user.click(themeItem("Dark"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't save the theme. The server is busy.");
    expect(themeItem("Light")).toHaveAttribute("aria-checked", "true");
    expect(themeItem("Dark")).toHaveAccessibleDescription("Couldn't save the theme. The server is busy.");
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(onPreferenceError).not.toHaveBeenCalled();
  });

  it("reports a failed theme save to the page once the menu has closed", async () => {
    const pending = deferred<Response>();
    const { user, onPreferenceError } = await setup(makeMe({ preferences: makePreferences({ theme: "light" }) }), {
      patch: () => pending.promise,
    });
    await openMenu(user);
    await user.click(themeItem("Dark"));
    await user.keyboard("{Escape}");
    await act(async () => pending.resolve(errorResponse(503, "The server is busy.")));
    expect(onPreferenceError).toHaveBeenCalledExactlyOnceWith("Couldn't save the theme. The server is busy.");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows an images meter only when images are limited, offered or made", async () => {
    const me = makeMe({ limits: makeLimits({ monthly_image_limit: 0 }) });
    const { user } = await setup({ ...me, usage: { ...me.usage, images: 0 } }, { imagesOffered: false });
    await openMenu(user);
    expect(screen.getByRole("menuitem", { name: "View usage" })).toHaveAccessibleDescription(
      `Chat allowance this period: ${formatNumber(120_000)} of ${formatNumber(2_000_000)}.`,
    );
  });

  it("shows no images meter when the plan doesn't include images", async () => {
    const me = makeMe({ plan: makePlanAccess({ features: { files: true, images: false, plugins: [] } }) });
    const { user } = await setup(me);
    await openMenu(user);
    expect(screen.getByRole("menuitem", { name: "View usage" })).toHaveAccessibleDescription(
      `Chat allowance this period: ${formatNumber(120_000)} of ${formatNumber(2_000_000)}.`,
    );
  });
});
