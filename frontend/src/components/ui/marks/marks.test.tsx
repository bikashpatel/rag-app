import { render } from "@testing-library/react";
import { Fragment } from "react";
import { describe, expect, it } from "vitest";
import AdminMark from "./AdminMark";
import AllSetMark from "./AllSetMark";
import DocumentsMark from "./DocumentsMark";
import InboxMark from "./InboxMark";
import PaymentCheckMark from "./PaymentCheckMark";
import PlansMark from "./PlansMark";
import SecurityMark from "./SecurityMark";
import SettingsMark from "./SettingsMark";
import UpiPayMark from "./UpiPayMark";
import UploadMark from "./UploadMark";

const MARKS = [
  ["documents", DocumentsMark],
  ["upload", UploadMark],
  ["plans", PlansMark],
  ["upi-pay", UpiPayMark],
  ["payment-check", PaymentCheckMark],
  ["all-set", AllSetMark],
  ["inbox", InboxMark],
  ["settings", SettingsMark],
  ["admin", AdminMark],
  ["security", SecurityMark],
] as const;

/** The ids a node's attributes point at: url(#…) in fill, stroke, filter or clip-path, and a use's href. */
function references(node: Element): string[] {
  return [...node.attributes].flatMap((attr) => {
    const url = /^url\(#(.+)\)$/.exec(attr.value);
    if (url) return [url[1]];
    return attr.name === "href" && attr.value.startsWith("#") ? [attr.value.slice(1)] : [];
  });
}

describe("marks", () => {
  it.each(MARKS)("draws the %s mark as a decorative SVG on the shared floating tile", (kind, Mark) => {
    const { container } = render(<Mark />);
    const svg = container.querySelector("svg");
    expect(svg).toHaveClass("mark", `mark-${kind}`);
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).toHaveAttribute("focusable", "false");
    expect(svg).toHaveAttribute("width", "72");
    expect(svg).toHaveAttribute("height", "72");
    expect(svg).toHaveAttribute("viewBox", "0 0 120 120");
    expect(svg?.querySelector("title")).toBeNull();
    expect(svg?.querySelector("style")).toBeNull();
    expect(svg?.querySelector(".mark-float > rect")).not.toBeNull();
  });

  it("takes a size and a class", () => {
    const { container } = render(<InboxMark size={40} className="auth-sent-mark" />);
    const svg = container.querySelector("svg");
    expect(svg).toHaveAttribute("width", "40");
    expect(svg).toHaveAttribute("height", "40");
    expect(svg?.getAttribute("class")).toBe("mark mark-inbox auth-sent-mark");
  });

  it("gives every instance its own ids, and each reference points inside its own SVG", () => {
    const { container } = render(
      <>
        {MARKS.map(([kind, Mark]) => (
          <Fragment key={kind}>
            <Mark />
            <Mark size={36} />
          </Fragment>
        ))}
      </>,
    );
    const ids = [...container.querySelectorAll("[id]")].map((node) => node.id);
    expect(ids.length).toBeGreaterThan(MARKS.length * 2);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) expect(id).toMatch(/^mark[\w-]+$/);

    const svgs = [...container.querySelectorAll("svg")];
    expect(svgs).toHaveLength(MARKS.length * 2);
    for (const svg of svgs) {
      const own = new Set([...svg.querySelectorAll("[id]")].map((node) => node.id));
      const refs = [...svg.querySelectorAll("*")].flatMap(references);
      expect(refs.length).toBeGreaterThan(0);
      for (const ref of refs) expect(own).toContain(ref);
    }
  });
});
