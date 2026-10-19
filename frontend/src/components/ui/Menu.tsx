import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { Link, type To } from "react-router-dom";
import { CheckIcon } from "./icons";

// The WAI-ARIA menu button pattern (docs/briefs/ui-shell-settings.md section 12, visual spec 6.9). The panel renders in
// a portal on <body>, positioned from the trigger (fixed), so no ancestor's stacking context, transform or
// backdrop-filter can trap it; under 800 px it is a bottom sheet over a scrim. Item actions run synchronously in the
// activating click or keydown handler, right after focus returns to the trigger, so a file input's click() stays
// inside the user gesture (Safari) and a dialog the action opens records the trigger as its opener.

export type MenuPlacement = "bottom-start" | "bottom-end" | "top-start";

const SHEET_QUERY = "(max-width: 799.98px)";
const ITEM_SELECTOR = '[role="menuitem"], [role="menuitemradio"], [role="menuitemcheckbox"]';
const HORIZONTAL_GROUP = '[data-orientation="horizontal"]';
const VIEWPORT_MARGIN = 8;

type MenuContextValue = {
  /** Closes the menu; with `restoreFocus` (the default) focus goes back to the trigger. */
  close: (restoreFocus?: boolean) => void;
  /** An item was activated: focus back on the trigger and close (unless `keepOpen`), then run the action. */
  activate: (action: (() => void) | undefined, keepOpen: boolean) => void;
};

const MenuContext = createContext<MenuContextValue | null>(null);

/** For custom content inside a menu that needs to close it. */
export function useMenu(): MenuContextValue {
  const menu = useContext(MenuContext);
  if (!menu) throw new Error("useMenu must be used inside a <Menu>");
  return menu;
}

function subscribeSheet(onChange: () => void): () => void {
  const list = window.matchMedia(SHEET_QUERY);
  list.addEventListener("change", onChange);
  return () => list.removeEventListener("change", onChange);
}

function isSheetWidth(): boolean {
  return window.matchMedia(SHEET_QUERY).matches;
}

function itemsOf(panel: HTMLElement | null): HTMLElement[] {
  return panel ? Array.from(panel.querySelectorAll<HTMLElement>(ITEM_SELECTOR)) : [];
}

function classes(...names: (string | false | null | undefined)[]): string {
  return names.filter(Boolean).join(" ");
}

function ids(...values: (string | false | null | undefined)[]): string | undefined {
  const joined = values.filter(Boolean).join(" ");
  return joined || undefined;
}

/** Spread on the trigger button. */
export type MenuTriggerProps = {
  ref: (element: HTMLButtonElement | null) => void;
  id: string;
  "aria-haspopup": "menu";
  "aria-expanded": boolean;
  "aria-controls": string | undefined;
  onClick: () => void;
  onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
};

type MenuProps = {
  /** The menu's accessible name ("Plugins", "Account: maya.chen, Member"). */
  label: string;
  /** Id of an element describing the menu. */
  describedBy?: string;
  /** A description rendered (hidden) inside the panel and referenced: the visual head's subtitle, "Loading plugins…". */
  description?: string;
  placement?: MenuPlacement;
  /** Space between the trigger and the panel, px (10; the "+" menu uses 8). */
  offset?: number;
  /** Added to the panel (its width, by the caller's own prefix). */
  className?: string;
  /** Renders the trigger: spread the props on a <button>. */
  trigger: (props: MenuTriggerProps, open: boolean) => ReactNode;
  /** Set to the trigger element (to focus it from outside). */
  triggerRef?: RefObject<HTMLButtonElement | null>;
  /** Called when the menu opens or closes (load what it shows; abort on close). */
  onOpenChange?: (open: boolean) => void;
  children: ReactNode;
};

/**
 * A menu button and its menu: roving focus (arrows, Home, End, a first letter), Escape and Tab close it and return
 * focus to the trigger, a pointer press outside closes it. Disabled items stay focusable (aria-disabled, with their
 * reason as the description). Groups may be horizontal: Left and Right move within them without wrapping, Up and Down
 * go through every item in DOM order. Content that isn't an item (heads, notes) is aria-hidden.
 */
export default function Menu({
  label,
  describedBy,
  description,
  placement = "bottom-start",
  offset = 10,
  className,
  trigger,
  triggerRef,
  onOpenChange,
  children,
}: MenuProps) {
  const [open, setOpen] = useState(false);
  const triggerId = useId();
  const menuId = useId();
  const descriptionId = useId();
  const triggerElement = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const scrimRef = useRef<HTMLDivElement>(null);
  const openRef = useRef(false);
  const focusIntent = useRef<"first" | "last">("first");
  const onOpenChangeRef = useRef(onOpenChange);
  onOpenChangeRef.current = onOpenChange;
  const sheet = useSyncExternalStore(subscribeSheet, isSheetWidth);

  const setTrigger = useCallback(
    (element: HTMLButtonElement | null) => {
      triggerElement.current = element;
      if (triggerRef) triggerRef.current = element;
    },
    [triggerRef],
  );

  const show = useCallback((focus: "first" | "last") => {
    focusIntent.current = focus;
    if (openRef.current) return;
    openRef.current = true;
    setOpen(true);
    onOpenChangeRef.current?.(true);
  }, []);

  const close = useCallback((restoreFocus = true) => {
    if (restoreFocus) triggerElement.current?.focus();
    if (!openRef.current) return;
    openRef.current = false;
    setOpen(false);
    onOpenChangeRef.current?.(false);
  }, []);

  const context = useMemo<MenuContextValue>(
    () => ({
      close,
      activate: (action, keepOpen) => {
        if (!keepOpen) close(true);
        action?.();
      },
    }),
    [close],
  );

  const position = useCallback(() => {
    const panel = panelRef.current;
    const anchor = triggerElement.current;
    if (!panel || !anchor || isSheetWidth()) return;
    const rect = anchor.getBoundingClientRect();
    const viewportWidth = document.documentElement.clientWidth;
    const viewportHeight = window.innerHeight;
    panel.style.maxHeight = "";
    const natural = panel.offsetHeight;
    const width = panel.offsetWidth;
    const below = viewportHeight - rect.bottom - offset - VIEWPORT_MARGIN;
    const above = rect.top - offset - VIEWPORT_MARGIN;
    // Open on the preferred side unless it is too short and the other side has more room.
    const up = placement === "top-start" ? !(natural > above && below > above) : natural > below && above > below;
    const room = Math.max(up ? above : below, 0);
    if (natural > room) panel.style.maxHeight = `${room}px`;
    const height = Math.min(natural, room);
    const alignEnd = placement === "bottom-end";
    const maxLeft = Math.max(VIEWPORT_MARGIN, viewportWidth - width - VIEWPORT_MARGIN);
    const left = Math.min(Math.max(alignEnd ? rect.right - width : rect.left, VIEWPORT_MARGIN), maxLeft);
    panel.style.top = `${Math.round(up ? rect.top - offset - height : rect.bottom + offset)}px`;
    panel.style.left = `${Math.round(left)}px`;
    panel.style.transformOrigin = `${alignEnd ? "right" : "left"} ${up ? "bottom" : "top"}`;
    panel.dataset.side = up ? "top" : "bottom";
  }, [offset, placement]);

  // Place the panel before it paints, then focus the first enabled item (a radio menu: the checked one).
  useLayoutEffect(() => {
    if (!open) return;
    position();
    const panel = panelRef.current;
    const items = itemsOf(panel);
    let target: HTMLElement | undefined;
    if (focusIntent.current === "last") {
      target = items[items.length - 1];
    } else {
      const firstEnabled = items.find((item) => item.getAttribute("aria-disabled") !== "true");
      target =
        firstEnabled?.getAttribute("role") === "menuitemradio"
          ? (items.find((item) => item.getAttribute("aria-checked") === "true") ?? firstEnabled)
          : (firstEnabled ?? items[0]);
    }
    (target ?? panel)?.focus();
  }, [open, position]);

  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (panel?.contains(target) || triggerElement.current?.contains(target) || scrimRef.current?.contains(target)) {
        return;
      }
      close(false);
    };
    const reposition = () => position();
    document.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(reposition) : null;
    if (panel) observer?.observe(panel);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
      observer?.disconnect();
    };
  }, [open, close, position]);

  // Crossing the 800 px breakpoint while open would leave a popover where a sheet belongs (or the reverse).
  const sheetWhenOpened = useRef(sheet);
  useEffect(() => {
    if (!open) {
      sheetWhenOpened.current = sheet;
      return;
    }
    if (sheet !== sheetWhenOpened.current) close(false);
  }, [open, sheet, close]);

  function onTriggerKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      show(event.key === "ArrowUp" ? "last" : "first");
    }
  }

  function onPanelKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const items = itemsOf(panelRef.current);
    const active = document.activeElement as HTMLElement | null;
    const current = active ? items.indexOf(active) : -1;
    const focusAt = (index: number) => items[(index + items.length) % items.length]?.focus();
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        focusAt(current + 1);
        return;
      case "ArrowUp":
        event.preventDefault();
        focusAt(current < 0 ? -1 : current - 1);
        return;
      case "Home":
        event.preventDefault();
        focusAt(0);
        return;
      case "End":
        event.preventDefault();
        focusAt(-1);
        return;
      case "ArrowLeft":
      case "ArrowRight": {
        const group = active?.closest<HTMLElement>(HORIZONTAL_GROUP);
        if (!group || !active) return;
        event.preventDefault();
        const row = items.filter((item) => group.contains(item));
        const next = row[row.indexOf(active) + (event.key === "ArrowRight" ? 1 : -1)];
        next?.focus();
        return;
      }
      case "Escape":
        // Handled here, so a drawer or panel around the trigger doesn't close as well.
        event.preventDefault();
        event.stopPropagation();
        close(true);
        return;
      case "Tab":
        event.preventDefault();
        close(true);
        return;
      case " ":
        // A link item: Space activates it like Enter (buttons do that natively).
        if (active instanceof HTMLAnchorElement && items.includes(active)) {
          event.preventDefault();
          active.click();
        }
        return;
    }
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && items.length > 0) {
      const letter = event.key.toLocaleLowerCase();
      const order = [...items.slice(current + 1), ...items.slice(0, current + 1)];
      order.find((item) => item.textContent?.trim().toLocaleLowerCase().startsWith(letter))?.focus();
    }
  }

  const panel = open
    ? createPortal(
        <>
          {sheet ? <div ref={scrimRef} className="ui-menu-scrim" aria-hidden="true" onClick={() => close(true)} /> : null}
          <div
            ref={panelRef}
            id={menuId}
            role="menu"
            aria-label={label}
            aria-describedby={ids(describedBy, description && descriptionId)}
            tabIndex={-1}
            className={classes("ui-menu", sheet && "ui-menu-sheet", className)}
            onKeyDown={onPanelKeyDown}
          >
            <MenuContext.Provider value={context}>{children}</MenuContext.Provider>
            {description ? (
              <span id={descriptionId} hidden>
                {description}
              </span>
            ) : null}
          </div>
        </>,
        document.body,
      )
    : null;

  return (
    <>
      {trigger(
        {
          ref: setTrigger,
          id: triggerId,
          "aria-haspopup": "menu",
          "aria-expanded": open,
          "aria-controls": open ? menuId : undefined,
          onClick: () => (openRef.current ? close(true) : show("first")),
          onKeyDown: onTriggerKeyDown,
        },
        open,
      )}
      {panel}
    </>
  );
}

function useMenuContext(): MenuContextValue {
  const menu = useContext(MenuContext);
  if (!menu) throw new Error("Menu items must be rendered inside a <Menu>");
  return menu;
}

type ItemContentProps = {
  /** The item's name (the visible title). */
  children: ReactNode;
  /** An 18 px icon, or a `.ui-menu-icon` tile. */
  icon?: ReactNode;
  /** A line under the title; it is the item's accessible description (a disabled item's reason goes here). */
  description?: ReactNode;
  /** More ids describing the item (a reason shared by a row of items, hidden usage numbers). */
  describedBy?: string;
  /** Trailing content: a chevron, a count, a pill. Not part of the name. */
  end?: ReactNode;
  /** Focusable but inert: activating it does nothing (U9). */
  disabled?: boolean;
  /** The accessible name when the title alone isn't it (a chip showing "PDF" is "PDF document"). */
  label?: string;
  className?: string;
};

function useItemParts({ children, icon, description, describedBy, end, disabled, label, className }: ItemContentProps) {
  const titleId = useId();
  const descriptionId = useId();
  return {
    attributes: {
      tabIndex: -1,
      className: classes("ui-menu-item", className),
      "aria-disabled": disabled || undefined,
      "aria-label": label,
      "aria-labelledby": label ? undefined : titleId,
      "aria-describedby": ids(description ? descriptionId : null, describedBy),
    },
    content: (
      <>
        {icon}
        <span className="ui-menu-text">
          <span id={titleId} className="ui-menu-title-text">
            {children}
          </span>
          {description ? (
            <span id={descriptionId} className="ui-menu-desc">
              {description}
            </span>
          ) : null}
        </span>
        {end ? <span className="ui-menu-end">{end}</span> : null}
      </>
    ),
  };
}

type MenuItemProps = ItemContentProps & {
  /** Runs synchronously in the click or key handler that activated the item (after focus is back on the trigger). */
  onSelect?: () => void;
  /** The menu stays open (the avatar menu's theme row). */
  keepOpen?: boolean;
};

export function MenuItem({ onSelect, keepOpen = false, ...content }: MenuItemProps) {
  const menu = useMenuContext();
  const { attributes, content: inner } = useItemParts(content);
  return (
    <button
      type="button"
      role="menuitem"
      {...attributes}
      onClick={() => {
        if (!content.disabled) menu.activate(onSelect, keepOpen);
      }}
    >
      {inner}
    </button>
  );
}

type MenuRadioItemProps = MenuItemProps & {
  checked: boolean;
  /** The trailing check on the checked item (default true; compact rows show the state by their own styles). */
  showCheck?: boolean;
};

export function MenuRadioItem({ checked, showCheck = true, onSelect, keepOpen = false, end, ...content }: MenuRadioItemProps) {
  const menu = useMenuContext();
  const check = checked && showCheck ? (
    <span className="ui-menu-check">
      <CheckIcon size={18} />
    </span>
  ) : null;
  const { attributes, content: inner } = useItemParts({
    ...content,
    end: end || check ? (
      <>
        {end}
        {check}
      </>
    ) : undefined,
  });
  return (
    <button
      type="button"
      role="menuitemradio"
      aria-checked={checked}
      {...attributes}
      onClick={() => {
        if (!content.disabled) menu.activate(onSelect, keepOpen);
      }}
    >
      {inner}
    </button>
  );
}

type MenuLinkProps = Omit<ItemContentProps, "disabled"> & {
  to: To;
  /** Runs before the navigation, after focus is back on the trigger. */
  onSelect?: () => void;
};

/** An item that navigates inside the app (react-router). */
export function MenuLink({ to, onSelect, ...content }: MenuLinkProps) {
  const menu = useMenuContext();
  const { attributes, content: inner } = useItemParts(content);
  return (
    <Link to={to} role="menuitem" {...attributes} onClick={() => menu.activate(onSelect, false)}>
      {inner}
    </Link>
  );
}

type MenuGroupProps = {
  /** A visible label above the group (a caps label such as "CREATE"); it names the group. */
  label?: ReactNode;
  /** Or: the id of another element that names it (the "Create a document" row). */
  labelledBy?: string;
  /** Or: an accessible name without a visible label. */
  ariaLabel?: string;
  /** "horizontal": Left and Right move within the group, without wrapping (the theme row, the format chips). */
  orientation?: "vertical" | "horizontal";
  className?: string;
  children: ReactNode;
};

export function MenuGroup({ label, labelledBy, ariaLabel, orientation = "vertical", className, children }: MenuGroupProps) {
  const labelId = useId();
  return (
    <>
      {label !== undefined ? (
        <div id={labelId} className="ui-menu-label" aria-hidden="true">
          {label}
        </div>
      ) : null}
      <div
        role="group"
        aria-labelledby={labelledBy ?? (label !== undefined ? labelId : undefined)}
        aria-label={labelledBy || label !== undefined ? undefined : ariaLabel}
        data-orientation={orientation === "horizontal" ? "horizontal" : undefined}
        className={className}
      >
        {children}
      </div>
    </>
  );
}

export function MenuSeparator() {
  return <div role="separator" className="ui-menu-sep" />;
}

type MenuHeadProps = { title: ReactNode; subtitle?: ReactNode; icon?: ReactNode; className?: string };

/** A menu's visual head (a serif title and a subtitle, an avatar): aria-hidden, so name the menu with the same text. */
export function MenuHead({ title, subtitle, icon, className }: MenuHeadProps) {
  return (
    <div className={classes("ui-menu-head", className)} aria-hidden="true">
      {icon}
      <div className="ui-menu-head-text">
        <div className="ui-menu-title">{title}</div>
        {subtitle ? <div className="ui-menu-sub">{subtitle}</div> : null}
      </div>
    </div>
  );
}

/** A line that isn't an item ("Loading plugins…"): aria-hidden, so put the same text in the menu's description. */
export function MenuNote({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={classes("ui-menu-note", className)} aria-hidden="true">
      {children}
    </div>
  );
}
