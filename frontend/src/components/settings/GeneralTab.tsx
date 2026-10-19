import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { api, ApiError } from "../../api/client";
import type { ChatModel, Preferences, PreferencesUpdate, ThemePreference } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { ACCENTS, CHAT_FONT_SIZES, THEMES, useResolvedTheme } from "../../theme/appearance";
import { CheckCircleIcon, CheckIcon, MonitorIcon, MoonIcon, SunIcon } from "../ui/icons";
import SegmentedControl from "../ui/SegmentedControl";
import { PanelHead } from "./PanelParts";
import { useLoad } from "./useLoad";

type Field = keyof Preferences;

const THEME_ICON: Record<ThemePreference, ReactNode> = {
  system: <MonitorIcon size={16} />,
  light: <SunIcon size={16} />,
  dark: <MoonIcon size={16} />,
};

/** How long "✓ Saved" stays after a save. */
const SAVED_MS = 2000;

const IS_APPLE = typeof navigator !== "undefined" && /Mac|iPhone|iPad|iPod/.test(navigator.userAgent);

/** A mini picture of the app in one theme (fixed colours: it depicts the theme, not the one applied). */
function MiniPreview({ theme }: { theme: "light" | "dark" }) {
  return (
    <span className={`settings-mini settings-mini-${theme}`}>
      <span className="settings-mini-side">
        <i />
        <i style={{ width: "70%" }} />
        <i style={{ width: "84%" }} />
      </span>
      <span className="settings-mini-main">
        <i className="settings-mini-bubble" />
        <i className="settings-mini-heading" />
        <i />
        <i style={{ width: "76%" }} />
        <span className="settings-mini-composer" />
      </span>
    </span>
  );
}

function ThemePreview({ theme }: { theme: ThemePreference }) {
  if (theme !== "system") return <MiniPreview theme={theme} />;
  return (
    <span className="settings-mini settings-mini-system">
      <MiniPreview theme="light" />
      <MiniPreview theme="dark" />
    </span>
  );
}

function SaveError({ message }: { message: string | undefined }) {
  if (!message) return null;
  return (
    <p className="error-text settings-error" role="alert">
      Couldn't save: {message}
    </p>
  );
}

/** Settings → General: theme, accent, chat font size, default model, Enter to send. Each change applies at once. */
export default function GeneralTab({ onClose }: { onClose: () => void }) {
  const { preferences, updatePreferences } = useAuth();
  const resolved = useResolvedTheme();
  const id = useId();
  const [errors, setErrors] = useState<Partial<Record<Field, string>>>({});
  const [savedAt, setSavedAt] = useState(0);
  const models = useLoad((signal) => api.get<ChatModel[]>("/models", { signal }), "Couldn't load your models.");
  const live = useRef(true);

  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  useEffect(() => {
    if (!savedAt) return;
    const timer = window.setTimeout(() => setSavedAt(0), SAVED_MS);
    return () => window.clearTimeout(timer);
  }, [savedAt]);

  function change(update: PreferencesUpdate) {
    const field = Object.keys(update)[0] as Field;
    // The next change clears an earlier save error.
    setErrors({});
    updatePreferences(update).then(
      () => {
        if (live.current) setSavedAt(Date.now());
      },
      (err: unknown) => {
        if (!live.current) return;
        const message = err instanceof ApiError ? err.message : "Please try again.";
        setErrors((current) => ({ ...current, [field]: message }));
      },
    );
  }

  const themeHint =
    preferences.theme === "system"
      ? `Currently ${resolved}, from your system.`
      : "System follows your device's setting and switches with it.";
  const serverDefault = models.data?.find((model) => model.is_default);
  const modelValue =
    preferences.default_model_id !== null && models.data?.some((model) => model.id === preferences.default_model_id)
      ? String(preferences.default_model_id)
      : "";
  const sendKey = IS_APPLE ? "⌘" : "Ctrl";
  const enterHint = preferences.enter_to_send ? (
    <>
      When off, Enter adds a new line and <kbd>{sendKey}</kbd> <kbd>Enter</kbd> sends.
    </>
  ) : (
    <>
      <kbd>{sendKey}</kbd> <kbd>Enter</kbd> sends and Enter adds a new line. When on, Enter sends and <kbd>Shift</kbd>{" "}
      <kbd>Enter</kbd> adds a new line.
    </>
  );

  return (
    <>
      <PanelHead
        title="General"
        onClose={onClose}
        actions={
          savedAt ? (
            <span key={savedAt} className="settings-saved" aria-hidden="true">
              <CheckCircleIcon size={15} />
              Saved
            </span>
          ) : null
        }
      />
      <div className="settings-body">
        <div className="settings-group settings-group-first">
          <h3 id={`${id}-theme`}>Theme</h3>
          <p className="settings-desc" id={`${id}-theme-hint`}>
            {themeHint}
          </p>
          <div
            className="settings-control settings-themes"
            role="radiogroup"
            aria-labelledby={`${id}-theme`}
            aria-describedby={`${id}-theme-hint`}
          >
            {THEMES.map((theme) => (
              <label key={theme.id} className="settings-theme">
                <input
                  type="radio"
                  name={`${id}-theme-choice`}
                  value={theme.id}
                  checked={preferences.theme === theme.id}
                  onChange={() => change({ theme: theme.id })}
                />
                <span className="settings-theme-card">
                  <span aria-hidden="true">
                    <ThemePreview theme={theme.id} />
                  </span>
                  <span className="settings-theme-label">
                    {THEME_ICON[theme.id]}
                    {theme.label}
                    <span className="settings-theme-check" aria-hidden="true">
                      <CheckIcon size={12} />
                    </span>
                  </span>
                </span>
              </label>
            ))}
          </div>
          <SaveError message={errors.theme} />
        </div>

        <div className="settings-group">
          <div className="settings-row settings-row-wrap">
            <div className="settings-label">
              <h3 id={`${id}-accent`}>Accent colour</h3>
              <p className="settings-desc" id={`${id}-accent-hint`}>
                Buttons, links, citations and highlights. Every colour is readable in both themes.
              </p>
            </div>
            <div
              className="settings-swatches"
              role="radiogroup"
              aria-labelledby={`${id}-accent`}
              aria-describedby={`${id}-accent-hint`}
            >
              {ACCENTS.map((accent) => (
                <label key={accent.id} className="settings-swatch" data-accent={accent.id}>
                  <input
                    type="radio"
                    name={`${id}-accent-choice`}
                    value={accent.id}
                    checked={preferences.accent === accent.id}
                    onChange={() => change({ accent: accent.id })}
                  />
                  <span className="settings-swatch-dot" aria-hidden="true">
                    <CheckIcon size={16} />
                  </span>
                  <span className="settings-swatch-name">{accent.label}</span>
                </label>
              ))}
            </div>
          </div>
          <SaveError message={errors.accent} />
        </div>

        <div className="settings-group">
          <div className="settings-row settings-row-wrap">
            <div className="settings-label">
              <h3 id={`${id}-size`}>Chat font size</h3>
              <p className="settings-desc" id={`${id}-size-hint`}>
                Answers and your messages. Menus and panels keep their size.
              </p>
            </div>
            <div className="settings-size">
              <SegmentedControl
                label="Chat font size"
                describedBy={`${id}-size-hint`}
                value={preferences.chat_font_size}
                options={CHAT_FONT_SIZES.map((size) => ({
                  value: size.id,
                  label: size.label,
                  icon: (
                    <b className={`settings-aa settings-aa-${size.id}`} aria-hidden="true">
                      Aa
                    </b>
                  ),
                }))}
                onChange={(chat_font_size) => change({ chat_font_size })}
              />
            </div>
          </div>
          <div className="settings-preview" aria-hidden="true">
            Northwind renews every 12 months unless either side gives 60 days’ written{" "}
            <span className="settings-preview-glue">
              notice.<span className="settings-preview-cite">1</span>
            </span>
            <div className="settings-bubble-preview">
              <span>What are the renewal terms?</span>
            </div>
          </div>
          <SaveError message={errors.chat_font_size} />
        </div>

        <div className="settings-group">
          <div className="settings-row settings-row-wrap">
            <div className="settings-label">
              <h3>
                <label htmlFor={`${id}-model`}>Default chat model</label>
              </h3>
              <p className="settings-desc" id={`${id}-model-hint`}>
                New chats start with this model. The model menu at the top changes it for one chat.
              </p>
            </div>
            <select
              id={`${id}-model`}
              className="select settings-model"
              aria-describedby={`${id}-model-hint${models.error ? ` ${id}-model-error` : ""}`}
              value={modelValue}
              disabled={models.data === null}
              onChange={(event) =>
                change({ default_model_id: event.target.value === "" ? null : Number(event.target.value) })
              }
            >
              <option value="">
                {models.data === null
                  ? models.error
                    ? "Server default"
                    : "Loading models…"
                  : `Server default${serverDefault ? ` (${serverDefault.display_name})` : ""}`}
              </option>
              {models.data?.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.display_name}
                </option>
              ))}
            </select>
          </div>
          {models.error ? (
            <div className="settings-error settings-actions">
              <p className="error-text" id={`${id}-model-error`} role="alert">
                {models.error}
              </p>
              <button type="button" className="btn btn-xs" onClick={models.reload}>
                Try again
              </button>
            </div>
          ) : null}
          <SaveError message={errors.default_model_id} />
        </div>

        <div className="settings-group">
          <div className="settings-row settings-enter">
            <div className="settings-label">
              <h3 id={`${id}-enter`}>Enter to send</h3>
              <p className="settings-desc" id={`${id}-enter-hint`}>
                {enterHint}
              </p>
            </div>
            <span className="switch">
              <input
                type="checkbox"
                role="switch"
                checked={preferences.enter_to_send}
                aria-labelledby={`${id}-enter`}
                aria-describedby={`${id}-enter-hint`}
                onChange={(event) => change({ enter_to_send: event.target.checked })}
              />
              <span aria-hidden="true" />
            </span>
          </div>
          <SaveError message={errors.enter_to_send} />
        </div>
      </div>
    </>
  );
}
