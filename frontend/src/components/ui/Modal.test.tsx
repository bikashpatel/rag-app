import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, useState } from "react";
import { describe, expect, it, vi } from "vitest";
import Modal from "./Modal";

function Harness({ onClose, initialFocusOnSave = false }: { onClose?: () => void; initialFocusOnSave?: boolean }) {
  const [open, setOpen] = useState(false);
  const save = useRef<HTMLButtonElement>(null);
  const close = () => {
    onClose?.();
    setOpen(false);
  };
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Rename
      </button>
      <Modal
        open={open}
        title="Rename conversation"
        onClose={close}
        initialFocus={initialFocusOnSave ? save : undefined}
        footer={
          <button ref={save} type="button" onClick={close}>
            Save
          </button>
        }
      >
        <label>
          Title
          <input defaultValue="Q3 review" />
        </label>
      </Modal>
    </>
  );
}

describe("Modal", () => {
  it("renders nothing while closed", () => {
    render(<Harness />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("is a named modal dialog that focuses its first form control", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Rename" }));
    const dialog = screen.getByRole("dialog", { name: "Rename conversation" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("textbox", { name: "Title" })).toHaveFocus();
  });

  it("focuses initialFocus instead when given", async () => {
    const user = userEvent.setup();
    render(<Harness initialFocusOnSave />);
    await user.click(screen.getByRole("button", { name: "Rename" }));
    expect(screen.getByRole("button", { name: "Save" })).toHaveFocus();
  });

  it("closes on Escape and returns focus to the opener", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    const opener = screen.getByRole("button", { name: "Rename" });
    await user.click(opener);
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("closes from the Close button", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Rename" }));
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps Tab and Shift+Tab inside the dialog", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Rename" }));
    const title = screen.getByRole("textbox", { name: "Title" });
    const close = screen.getByRole("button", { name: "Close" });
    const save = screen.getByRole("button", { name: "Save" });

    await user.tab();
    expect(save).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();
    await user.tab({ shift: true });
    expect(save).toHaveFocus();
    await user.tab({ shift: true });
    expect(title).toHaveFocus();
    await user.tab({ shift: true });
    expect(close).toHaveFocus();
  });
});
