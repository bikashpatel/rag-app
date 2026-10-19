import {
  useCallback,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ComponentPropsWithRef,
  type ReactNode,
  type Ref,
} from "react";
import { EyeIcon, EyeOffIcon } from "./icons";

type PasswordInputProps = Omit<ComponentPropsWithRef<"input">, "type"> & {
  /** Renders a .field with this label above the input. Without it, label the input yourself (id, aria-label…). */
  label?: ReactNode;
};

function assignRef<T>(ref: Ref<T> | undefined, value: T | null): void {
  if (typeof ref === "function") ref(value);
  else if (ref) ref.current = value;
}

/**
 * A password field with a show/hide toggle (login, Change password, the admin user form). The toggle switches the
 * input's type and keeps focus and the caret where they were.
 */
export default function PasswordInput({ label, className, id, ref, ...inputProps }: PasswordInputProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const [visible, setVisible] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  /** The caret before the type changed (a type change can reset it). */
  const selection = useRef<[number, number] | null>(null);
  const setInput = useCallback(
    (element: HTMLInputElement | null) => {
      inputRef.current = element;
      assignRef(ref, element);
    },
    [ref],
  );

  useLayoutEffect(() => {
    const input = inputRef.current;
    const saved = selection.current;
    selection.current = null;
    if (!input || !saved || document.activeElement !== input) return;
    // The browser rebuilds the field's editor at the next layout after a type change, which moves the caret to the
    // start: lay out now, then put the caret back.
    void input.offsetWidth;
    input.setSelectionRange(saved[0], saved[1]);
  }, [visible]);

  function toggle() {
    const input = inputRef.current;
    if (input && input.selectionStart !== null && input.selectionEnd !== null) {
      selection.current = [input.selectionStart, input.selectionEnd];
    }
    setVisible((value) => !value);
  }

  const field = (
    <div className="ui-password">
      <input
        spellCheck={false}
        autoCapitalize="none"
        {...inputProps}
        ref={setInput}
        id={inputId}
        type={visible ? "text" : "password"}
        className={className ? `input ${className}` : "input"}
      />
      <button
        type="button"
        className="ui-password-toggle"
        aria-label={visible ? "Hide password" : "Show password"}
        aria-pressed={visible}
        aria-controls={inputId}
        disabled={inputProps.disabled}
        // A mouse click keeps focus (and the caret) in the field.
        onMouseDown={(event) => event.preventDefault()}
        onClick={toggle}
      >
        {visible ? <EyeOffIcon size={18} /> : <EyeIcon size={18} />}
      </button>
    </div>
  );

  if (label === undefined) return field;
  return (
    <div className="field">
      <label htmlFor={inputId} className="field-label">
        {label}
      </label>
      {field}
    </div>
  );
}
