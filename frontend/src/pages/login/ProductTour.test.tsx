import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setMediaQueries } from "../../test/browser";
import ProductTour from "./ProductTour";
import { TOUR_SCENES } from "./tourScenes";

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

const SCENES = [
  { title: "Cited answers", caption: "Every answer points to the passage it came from.", plan: null },
  { title: "Drop in any file", caption: "PDF, Word, Excel, slides, even scanned pages.", plan: null },
  {
    title: "Turn answers into files",
    caption: "PDF, Word, Excel or Markdown, saved to your documents.",
    plan: "Standard and up",
  },
  { title: "Picture it", caption: "Images made in chat, ready for your reports.", plan: "Professional" },
  {
    title: "Your inbox, on hand",
    caption: "Search, import and draft. Nothing is sent without your OK.",
    plan: "Professional",
  },
  { title: "Private by default", caption: "Your files stay yours, next to a shared knowledge base.", plan: null },
];
const TITLES = SCENES.map((scene) => scene.title);
const ASK = "When does the Northwind contract renew?";
const duration = (index: number) => TOUR_SCENES[index].duration;

// Fake timeouts and Date only, driven by hand; interactions use fireEvent (user-event's waits need a running clock).
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
});

afterEach(() => {
  vi.useRealTimers();
});

function setup() {
  return render(
    <>
      <ProductTour />
      <button type="button">Elsewhere</button>
    </>,
  );
}

const tour = () => screen.getByRole("region", { name: "Product tour" });
/** The slide assistive technology can see: the others are aria-hidden. */
const slide = () => screen.getByRole("group");
const slides = () => slide().parentElement!;
const tabs = () => within(screen.getByRole("tablist", { name: "Tour scenes" })).getAllByRole("tab");
const tab = (index: number) => screen.getByRole("tab", { name: TITLES[index] });
const toggle = () => screen.getByRole("button", { name: /the tour$/ });
const elsewhere = () => screen.getByRole("button", { name: "Elsewhere" });

/** A mouse click: the pointer comes over the control, which takes focus, then clicks. */
function press(element: HTMLElement) {
  fireEvent.pointerEnter(element);
  act(() => element.focus());
  fireEvent.click(element);
}

/** The pointer and focus go elsewhere on the page. */
function leave() {
  fireEvent.pointerLeave(tour());
  act(() => elsewhere().focus());
}

function key(name: string) {
  fireEvent.keyDown(document.activeElement!, { key: name });
}

function wait(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

function expectScene(index: number) {
  expect(slide()).toHaveAccessibleName(`${index + 1} of 6: ${TITLES[index]}`);
  expect(tabs().map((t) => t.getAttribute("aria-selected"))).toEqual(TITLES.map((_, i) => String(i === index)));
}

/** How often the visible slide's text holds `text` (the typed question is one span per character). */
function occurrences(text: string): number {
  return slide().textContent!.split(text).length - 1;
}

/** Runs out the current scene's time to the millisecond: it stays until then and moves on after. */
function expectToMoveOnAfter(ms: number, from: number) {
  wait(ms - 1);
  expectScene(from);
  wait(1);
  expectScene((from + 1) % TITLES.length);
}

describe("ProductTour", () => {
  it("is a carousel of six labelled slides with a tab each, showing the first", () => {
    setup();
    expect(tour()).toHaveAttribute("aria-roledescription", "carousel");
    expect(tabs().map((t) => t.getAttribute("aria-label"))).toEqual(TITLES);
    expect(tabs().map((t) => t.tabIndex)).toEqual([0, -1, -1, -1, -1, -1]);
    expectScene(0);
    expect(slide()).toHaveAttribute("aria-roledescription", "slide");
    expect(tab(0)).toHaveAttribute("aria-controls", slide().id);
    expect(within(slide()).getByRole("heading", { level: 3, name: TITLES[0] })).toBeInTheDocument();

    // All six are in the DOM; the five inactive ones are hidden from assistive technology.
    const all = screen.getAllByRole("group", { hidden: true });
    const labels = TITLES.map((title, i) => `${i + 1} of 6: ${title}`);
    expect(all.map((group) => group.getAttribute("aria-label"))).toEqual(labels);
    expect(all.filter((group) => group.getAttribute("aria-hidden") === "true")).toHaveLength(5);
    expect(all.filter((group) => group.hasAttribute("inert"))).toHaveLength(5);

    // The miniature is decorative; the title and caption are what assistive technology reads.
    expect(within(slide()).getByText(ASK).closest('[aria-hidden="true"]')).not.toBeNull();
    expect(within(slide()).getByText(SCENES[0].caption).closest('[aria-hidden="true"]')).toBeNull();

    expect(slides()).toHaveAttribute("aria-live", "off");
    expect(toggle()).toHaveAccessibleName("Pause the tour");
  });

  it("names every scene with a caption, and gated ones with their plan", () => {
    setup();
    SCENES.forEach(({ title, caption, plan }, i) => {
      fireEvent.click(tab(i));
      expectScene(i);
      expect(within(slide()).getByRole("heading", { level: 3 })).toHaveTextContent(title);
      expect(within(slide()).getByText(caption)).toBeInTheDocument();
      for (const name of ["Standard and up", "Professional"]) {
        if (name === plan) expect(within(slide()).getByText(name)).toBeInTheDocument();
        else expect(within(slide()).queryByText(name)).not.toBeInTheDocument();
      }
    });
  });

  it("moves on by itself after each scene's time and loops back to the first", () => {
    setup();
    TITLES.forEach((_, i) => expectToMoveOnAfter(duration(i), i));
    expectScene(0);
  });

  it("jumps to a scene from its tab and gives it its full time", () => {
    setup();
    wait(4000);
    press(tab(3));
    expectScene(3);
    // The pointer and focus are on the tab, so the tour holds until they leave.
    wait(60_000);
    expectScene(3);
    leave();
    expectToMoveOnAfter(duration(3), 3);

    wait(1000);
    fireEvent.click(tab(4)); // the current scene: its time keeps running
    expectToMoveOnAfter(duration(4) - 1000, 4);
  });

  it("moves between the tabs with the arrow keys, Home and End, keeping only the selected one in the tab order", () => {
    setup();
    act(() => tab(0).focus());
    key("ArrowRight");
    expect(tab(1)).toHaveFocus();
    expectScene(1);
    expect(tabs().map((t) => t.tabIndex)).toEqual([-1, 0, -1, -1, -1, -1]);
    key("End");
    expect(tab(5)).toHaveFocus();
    expectScene(5);
    key("ArrowRight");
    expect(tab(0)).toHaveFocus();
    expectScene(0);
    key("ArrowLeft");
    expect(tab(5)).toHaveFocus();
    key("ArrowLeft");
    expect(tab(4)).toHaveFocus();
    expectScene(4);
    key("Home");
    expect(tab(0)).toHaveFocus();
    expectScene(0);
    key("ArrowDown");
    key("Enter");
    expect(tab(0)).toHaveFocus();
    expectScene(0);
    expect(tabs().map((t) => t.tabIndex)).toEqual([0, -1, -1, -1, -1, -1]);
  });

  it("pauses and plays from its button, whose name says what it will do", () => {
    setup();
    wait(2000);
    press(toggle());
    expect(toggle()).toHaveAccessibleName("Play the tour");
    expect(slides()).toHaveAttribute("aria-live", "polite");
    leave();
    wait(60_000);
    expectScene(0);

    // Play starts at once, with the pointer and focus still on the button, and keeps the time already shown.
    press(toggle());
    expect(toggle()).toHaveAccessibleName("Pause the tour");
    expect(slides()).toHaveAttribute("aria-live", "off");
    expectToMoveOnAfter(duration(0) - 2000, 0);
  });

  it("holds while the pointer is over it and carries on when it leaves", () => {
    setup();
    wait(4000);
    fireEvent.pointerEnter(screen.getByText(SCENES[0].caption));
    expect(slides()).toHaveAttribute("aria-live", "polite");
    wait(60_000);
    expectScene(0);
    fireEvent.pointerLeave(tour());
    expect(slides()).toHaveAttribute("aria-live", "off");
    expectToMoveOnAfter(duration(0) - 4000, 0);
  });

  it("holds while focus is inside it and carries on when focus leaves", () => {
    setup();
    wait(4000);
    act(() => toggle().focus());
    act(() => tab(0).focus());
    wait(60_000);
    expectScene(0);
    expect(slides()).toHaveAttribute("aria-live", "polite");
    act(() => elsewhere().focus());
    expectToMoveOnAfter(duration(0) - 4000, 0);
  });

  it("holds while the page is hidden, with no timer running", () => {
    setup();
    wait(4000);
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(vi.getTimerCount()).toBe(0);
    wait(60_000);
    expectScene(0);

    visibility.mockReturnValue("visible");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expectToMoveOnAfter(duration(0) - 4000, 0);
  });

  it("plays a scene's passing states; paused, it shows the scene finished and doesn't replay it", () => {
    setup();
    expect(occurrences(ASK)).toBe(2); // typed into the composer, then sent

    press(tab(1));
    expect(within(slide()).getAllByText("Queued")).toHaveLength(4);
    expect(within(slide()).getAllByText("Indexing…")).toHaveLength(4);

    press(toggle());
    expect(within(slide()).queryByText("Queued")).not.toBeInTheDocument();
    expect(within(slide()).queryByText("Indexing…")).not.toBeInTheDocument();
    expect(within(slide()).getAllByText("Ready")).toHaveLength(4);

    press(toggle());
    expect(within(slide()).queryByText("Queued")).not.toBeInTheDocument();
    expectToMoveOnAfter(duration(1), 1);
    expect(within(slide()).getByText("Writing the file…")).toBeInTheDocument();
  });

  it("under reduced motion shows finished scenes that change only from the tabs", () => {
    setMediaQueries((query) => query === REDUCED_MOTION);
    setup();
    expect(screen.queryByRole("button", { name: /the tour$/ })).not.toBeInTheDocument();
    expect(slides()).toHaveAttribute("aria-live", "polite");
    expect(vi.getTimerCount()).toBe(0);
    wait(60_000);
    expectScene(0);
    expect(occurrences(ASK)).toBe(1); // sent, with no typing

    press(tab(1));
    expectScene(1);
    expect(within(slide()).getAllByText("Ready")).toHaveLength(4);
    expect(within(slide()).queryByText("Queued")).not.toBeInTheDocument();
    press(tab(3));
    expect(within(slide()).queryByText("Creating the image…")).not.toBeInTheDocument();
    press(tab(2));
    expect(within(slide()).queryByText("Writing the file…")).not.toBeInTheDocument();
  });

  it("follows the reduced-motion setting when it changes", () => {
    setup();
    wait(1000);
    act(() => setMediaQueries((query) => query === REDUCED_MOTION));
    expect(screen.queryByRole("button", { name: /the tour$/ })).not.toBeInTheDocument();
    expect(slides()).toHaveAttribute("aria-live", "polite");
    wait(60_000);
    expectScene(0);

    act(() => setMediaQueries(() => false));
    expect(toggle()).toHaveAccessibleName("Pause the tour");
    expect(slides()).toHaveAttribute("aria-live", "off");
    expectToMoveOnAfter(duration(0) - 1000, 0);
  });

  it("leaves no timer behind when it unmounts", () => {
    const { unmount } = setup();
    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
