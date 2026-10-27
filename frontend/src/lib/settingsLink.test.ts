import { describe, expect, it } from "vitest";
import { parseSettingsTab, withSettingsTab } from "./settingsLink";

describe("parseSettingsTab", () => {
  it.each([
    ["general", "general"],
    ["account", "account"],
    ["billing", "billing"],
    ["usage", "usage"],
    ["plugins", "plugins"],
    ["data", "plugins"],
    ["nope", "general"],
    ["", "general"],
  ])("reads ?settings=%s as %s", (value, tab) => {
    expect(parseSettingsTab(value)).toBe(tab);
  });

  it("is null without the parameter", () => {
    expect(parseSettingsTab(null)).toBeNull();
  });

  it("sets or removes the tab, keeping the other parameters", () => {
    expect(withSettingsTab("?x=1", "billing")).toBe("?x=1&settings=billing");
    expect(withSettingsTab("?settings=usage&x=1", null)).toBe("?x=1");
    expect(withSettingsTab("", null)).toBe("");
  });
});
