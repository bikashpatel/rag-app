import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { setMediaQueries } from "../../test/browser";
import { renderWithRouter } from "../../test/render";
import Menu, { MenuGroup, MenuItem, MenuLink, MenuRadioItem, MenuSeparator } from "./Menu";

const THEMES = ["Light", "Dark", "System"] as const;

function Harness({ onSelect = () => {} }: { onSelect?: (action: string) => void }) {
  const [theme, setTheme] = useState<(typeof THEMES)[number]>("Light");
  return (
    <>
      <button type="button">Outside</button>
      <Menu
        label="Conversation actions"
        description="Actions for Q3 review"
        trigger={(props) => (
          <button {...props} type="button">
            Actions
          </button>
        )}
      >
        <MenuItem onSelect={() => onSelect("rename")}>Rename</MenuItem>
        <MenuItem disabled description="Only the owner can share it." onSelect={() => onSelect("share")}>
          Share
        </MenuItem>
        <MenuItem onSelect={() => onSelect("duplicate")}>Duplicate</MenuItem>
        <MenuGroup label="Theme" orientation="horizontal">
          {THEMES.map((name) => (
            <MenuRadioItem key={name} checked={theme === name} keepOpen onSelect={() => setTheme(name)}>
              {name}
            </MenuRadioItem>
          ))}
        </MenuGroup>
        <MenuSeparator />
        <MenuLink to="/admin">Go to admin</MenuLink>
        <MenuItem onSelect={() => onSelect("delete")}>Delete</MenuItem>
      </Menu>
    </>
  );
}

function setup(onSelect?: (action: string) => void) {
  const user = userEvent.setup();
  const view = renderWithRouter(<Harness onSelect={onSelect} />);
  const trigger = screen.getByRole("button", { name: "Actions" });
  return { user, view, trigger };
}

const item = (name: string) => screen.getByRole("menuitem", { name });
const radio = (name: string) => screen.getByRole("menuitemradio", { name });

describe("Menu", () => {
  it("is a menu button that opens on click and focuses the first item", async () => {
    const { user, trigger } = setup();
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    await user.click(trigger);

    const menu = screen.getByRole("menu", { name: "Conversation actions" });
    expect(menu).toHaveAccessibleDescription("Actions for Q3 review");
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(trigger).toHaveAttribute("aria-controls", menu.id);
    expect(item("Rename")).toHaveFocus();
  });

  it("opens from the keyboard: ArrowDown on the first item, ArrowUp on the last", async () => {
    const { user, trigger } = setup();
    trigger.focus();
    await user.keyboard("{ArrowDown}");
    expect(item("Rename")).toHaveFocus();
    await user.keyboard("{Escape}");

    await user.keyboard("{ArrowUp}");
    expect(item("Delete")).toHaveFocus();
  });

  it("moves focus with the arrow keys, wrapping at both ends, and with Home and End", async () => {
    const { user, trigger } = setup();
    await user.click(trigger);

    await user.keyboard("{ArrowDown}");
    expect(item("Share")).toHaveFocus();
    await user.keyboard("{ArrowDown}{ArrowDown}{ArrowDown}{ArrowDown}{ArrowDown}");
    expect(item("Go to admin")).toHaveFocus();
    await user.keyboard("{ArrowDown}{ArrowDown}");
    expect(item("Rename")).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(item("Delete")).toHaveFocus();
    await user.keyboard("{Home}");
    expect(item("Rename")).toHaveFocus();
    await user.keyboard("{End}");
    expect(item("Delete")).toHaveFocus();
  });

  it("jumps to the next item starting with a typed letter", async () => {
    const { user, trigger } = setup();
    await user.click(trigger);

    await user.keyboard("d");
    expect(item("Duplicate")).toHaveFocus();
    await user.keyboard("d");
    expect(radio("Dark")).toHaveFocus();
    await user.keyboard("d");
    expect(item("Delete")).toHaveFocus();
    await user.keyboard("d");
    expect(item("Duplicate")).toHaveFocus();
  });

  it("closes on Escape and on Tab, returning focus to the trigger", async () => {
    const { user, trigger } = setup();
    await user.click(trigger);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();

    await user.click(trigger);
    await user.keyboard("{ArrowDown}{Tab}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("runs an item's action once, closes and returns focus to the trigger", async () => {
    const onSelect = vi.fn();
    const { user, trigger } = setup(onSelect);
    await user.click(trigger);
    await user.click(item("Duplicate"));
    expect(onSelect).toHaveBeenCalledExactlyOnceWith("duplicate");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();

    await user.click(trigger);
    await user.keyboard("{End}{Enter}");
    expect(onSelect).toHaveBeenLastCalledWith("delete");
  });

  it("keeps a disabled item focusable, describes why, and ignores activating it", async () => {
    const onSelect = vi.fn();
    const { user, trigger } = setup(onSelect);
    await user.click(trigger);
    const share = item("Share");
    expect(share).toHaveAttribute("aria-disabled", "true");
    expect(share).toHaveAccessibleDescription("Only the owner can share it.");

    await user.keyboard("{ArrowDown}");
    expect(share).toHaveFocus();
    await user.keyboard("{Enter}");
    await user.click(share);
    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("marks the checked radio item and keeps the menu open for keepOpen items", async () => {
    const { user, trigger } = setup();
    await user.click(trigger);
    expect(radio("Light")).toHaveAttribute("aria-checked", "true");
    expect(radio("Dark")).toHaveAttribute("aria-checked", "false");

    await user.click(radio("Dark"));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(radio("Dark")).toHaveAttribute("aria-checked", "true");
    expect(radio("Light")).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("group", { name: "Theme" })).toContainElement(radio("Dark"));
  });

  it("moves within a horizontal group with Left and Right, without wrapping", async () => {
    const { user, trigger } = setup();
    await user.click(trigger);
    await user.keyboard("{ArrowRight}");
    expect(item("Rename")).toHaveFocus();

    await user.keyboard("{ArrowDown}{ArrowDown}{ArrowDown}");
    expect(radio("Light")).toHaveFocus();
    await user.keyboard("{ArrowLeft}");
    expect(radio("Light")).toHaveFocus();
    await user.keyboard("{ArrowRight}{ArrowRight}{ArrowRight}");
    expect(radio("System")).toHaveFocus();
    await user.keyboard("{ArrowLeft}");
    expect(radio("Dark")).toHaveFocus();
  });

  it("activates a link item with Space and navigates", async () => {
    const { user, view, trigger } = setup();
    await user.click(trigger);
    await user.keyboard("g");
    expect(item("Go to admin")).toHaveFocus();
    await user.keyboard(" ");
    expect(view.location()).toBe("/admin");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("closes on a pointer press outside without taking focus back", async () => {
    const { user, trigger } = setup();
    await user.click(trigger);
    const outside = screen.getByRole("button", { name: "Outside" });
    await user.click(outside);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(outside).toHaveFocus();
  });

  it("toggles closed when the trigger is clicked again", async () => {
    const { user, trigger } = setup();
    await user.click(trigger);
    await user.click(trigger);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("closes when the viewport crosses the bottom-sheet breakpoint", async () => {
    const { user, trigger } = setup();
    await user.click(trigger);
    act(() => setMediaQueries((query) => query.includes("max-width")));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
