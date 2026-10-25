import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { makeDocument } from "../../test/fixtures";
import { publishDocuments } from "../documents/documentEvents";
import EmptyState from "./EmptyState";

type Props = ComponentProps<typeof EmptyState>;

function renderEmpty(overrides: Partial<Props> = {}) {
  const props: Props = {
    username: "maya.chen",
    disabled: false,
    canCreateFiles: false,
    canCreateImages: false,
    selection: null,
    imagesNote: "47 of 50 left this month",
    onPick: vi.fn(),
    ...overrides,
  };
  render(<EmptyState {...props} />);
  return props;
}

const example = (prompt: string) => screen.getByRole("button", { name: new RegExp(`^${prompt}`) });
const summaryNote = () => within(example("Summarize the key points of my documents")).getByText(/document/i, { selector: "small" });

afterEach(() => {
  act(() => publishDocuments(null));
});

describe("EmptyState", () => {
  it("greets the user and sends an example at a click", async () => {
    const user = userEvent.setup();
    const { onPick } = renderEmpty();
    expect(screen.getByRole("heading", { name: /How can I help/ })).toHaveTextContent("How can I help, maya.chen?");
    expect(within(screen.getByRole("list", { name: "Example questions" })).getAllByRole("button")).toHaveLength(3);
    await user.click(example("Which dates, deadlines or figures are mentioned\\?"));
    expect(onPick).toHaveBeenCalledWith("Which dates, deadlines or figures are mentioned?");
  });

  it("greets without a name and disables the examples while chat is unavailable", () => {
    renderEmpty({ username: null, disabled: true });
    expect(screen.getByRole("heading", { name: "How can I help?" })).toBeInTheDocument();
    for (const button of screen.getAllByRole("button")) expect(button).toBeDisabled();
  });

  it("adds file and image examples the model supports, with the image allowance", () => {
    renderEmpty({ canCreateFiles: true, canCreateImages: true });
    expect(within(example("Create a PDF report")).getByText("Saved to My documents")).toBeInTheDocument();
    expect(within(example("Draw a watercolor illustration")).getByText("47 of 50 left this month")).toBeInTheDocument();
  });

  it("says which documents the summary example reads", () => {
    renderEmpty();
    expect(summaryNote()).toHaveTextContent("Across your documents");
    act(() => publishDocuments([]));
    expect(summaryNote()).toHaveTextContent("Upload documents to ask about them");
    act(() => publishDocuments([makeDocument({ id: 901 })]));
    expect(summaryNote()).toHaveTextContent("Across your 1 ready document");
    act(() =>
      publishDocuments([
        makeDocument({ id: 901 }),
        makeDocument({ id: 902 }),
        makeDocument({ id: 903, status: "processing" }),
      ]),
    );
    expect(summaryNote()).toHaveTextContent("Across your 2 ready documents");
  });

  it.each([
    [[], "Documents are off: pick some in the documents panel"],
    [[101], "Across the selected document"],
    [[101, 102, 103], "Across the 3 selected documents"],
  ])("describes the selection %j", (selection, note) => {
    renderEmpty({ selection });
    expect(summaryNote()).toHaveTextContent(note);
  });
});
