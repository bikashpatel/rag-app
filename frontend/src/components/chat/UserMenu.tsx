import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import type { Me } from "../../api/types";
import { usePlugins } from "../connections/pluginStore";
import { KeyIcon, LogoutIcon, MoreIcon, PlugIcon, ShieldIcon } from "./icons";

type UserMenuProps = {
  user: Me;
  onChangePassword: () => void;
  onOpenConnections: () => void;
  onLogout: () => void;
};

/**
 * Account button in the sidebar footer with a popup menu: Admin, Change password (not for the admin), Connections
 * (when any plugin is available to the user), Log out.
 */
export default function UserMenu({ user, onChangePassword, onOpenConnections, onLogout }: UserMenuProps) {
  const { plugins } = usePlugins();
  const [open, setOpen] = useState(false);
  const menuId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    const onPointerDown = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  function choose(action: () => void) {
    setOpen(false);
    // Move focus off the menu item before it unmounts, so a dialog opened by `action` records the account button
    // as its opener and returns focus there (instead of <body>) when it closes.
    buttonRef.current?.focus();
    action();
  }

  function onMenuKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
    const current = items.indexOf(document.activeElement as HTMLElement);
    const focusAt = (index: number) => items[(index + items.length) % items.length]?.focus();
    switch (e.key) {
      case "Escape":
        e.preventDefault();
        setOpen(false);
        buttonRef.current?.focus();
        break;
      case "ArrowDown":
        e.preventDefault();
        focusAt(current + 1);
        break;
      case "ArrowUp":
        e.preventDefault();
        focusAt(current - 1);
        break;
      case "Home":
        e.preventDefault();
        focusAt(0);
        break;
      case "End":
        e.preventDefault();
        focusAt(items.length - 1);
        break;
      case "Tab":
        setOpen(false);
        break;
    }
  }

  function onButtonKeyDown(e: KeyboardEvent<HTMLButtonElement>) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setOpen(true);
    }
  }

  return (
    <div className="chat-user" ref={rootRef}>
      {open ? (
        <div ref={menuRef} id={menuId} className="chat-user-menu" role="menu" aria-label="Account" onKeyDown={onMenuKeyDown}>
          {user.is_admin ? (
            <Link to="/admin" role="menuitem" tabIndex={-1} className="chat-menu-item">
              <ShieldIcon size={15} />
              Admin
            </Link>
          ) : (
            <button type="button" role="menuitem" tabIndex={-1} className="chat-menu-item" onClick={() => choose(onChangePassword)}>
              <KeyIcon size={15} />
              Change password
            </button>
          )}
          {plugins && plugins.length > 0 ? (
            <button
              type="button"
              role="menuitem"
              tabIndex={-1}
              className="chat-menu-item"
              onClick={() => choose(onOpenConnections)}
            >
              <PlugIcon size={15} />
              Connections
            </button>
          ) : null}
          <button type="button" role="menuitem" tabIndex={-1} className="chat-menu-item" onClick={() => choose(onLogout)}>
            <LogoutIcon size={15} />
            Log out
          </button>
        </div>
      ) : null}
      <button
        ref={buttonRef}
        type="button"
        className="chat-user-button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((value) => !value)}
        onKeyDown={onButtonKeyDown}
      >
        <span className="chat-user-avatar" aria-hidden="true">
          {user.username.charAt(0).toUpperCase()}
        </span>
        <span className="chat-user-name">{user.username}</span>
        {user.is_admin ? <span className="badge badge-info">Admin</span> : null}
        <MoreIcon size={16} />
      </button>
    </div>
  );
}
