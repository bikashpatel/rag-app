import type { ComponentType } from "react";
import type { DocumentSelection } from "../../api/types";
import { formatNumber } from "../../lib/format";
import { useDocumentList } from "../documents/documentEvents";
import { ArrowRightIcon, BookIcon, CalendarIcon, FileIcon, ImageIcon, QuoteIcon, SparkleIcon, type IconProps } from "../ui/icons";

const FILE_PROMPT = "Create a PDF report summarizing my documents";
const IMAGE_PROMPT = "Draw a watercolor illustration of a lighthouse at dawn";

type Example = { prompt: string; note: string; Icon: ComponentType<IconProps> };

/** Which documents the summary example would read: "Across your 6 ready documents". */
function useDocumentsNote(selection: DocumentSelection): string {
  const documents = useDocumentList();
  if (selection !== null) {
    if (selection.length === 0) return "Documents are off: pick some in the documents panel";
    return selection.length === 1 ? "Across the selected document" : `Across the ${formatNumber(selection.length)} selected documents`;
  }
  if (documents === null) return "Across your documents";
  const ready = documents.filter((doc) => doc.status === "ready").length;
  if (ready === 0) return "Upload documents to ask about them";
  return ready === 1 ? "Across your 1 ready document" : `Across your ${formatNumber(ready)} ready documents`;
}

type EmptyStateProps = {
  username: string | null;
  disabled: boolean;
  /** The selected model can create files: adds an example that asks for one. */
  canCreateFiles: boolean;
  /** The selected model can generate images: adds an example that asks for one. */
  canCreateImages: boolean;
  /** The documents the next answer searches (for the summary example's note). */
  selection: DocumentSelection;
  /** Under the image example: "18 of 30 left this month" or "Saved to My documents". */
  imagesNote: string;
  onPick: (prompt: string) => void;
};

/**
 * A new chat (visual spec 6.20): the greeting with the username (never a guessed first name), an italic serif
 * sub-line, and a "contents page" of example prompts that send at a click.
 */
export default function EmptyState({
  username,
  disabled,
  canCreateFiles,
  canCreateImages,
  selection,
  imagesNote,
  onPick,
}: EmptyStateProps) {
  const documentsNote = useDocumentsNote(selection);
  const examples: Example[] = [
    { prompt: "Summarize the key points of my documents", note: documentsNote, Icon: QuoteIcon },
    { prompt: "Which dates, deadlines or figures are mentioned?", note: "Listed with their sources", Icon: CalendarIcon },
    { prompt: "Explain the main topic in simple terms", note: "In plain words, with citations", Icon: BookIcon },
    ...(canCreateFiles ? [{ prompt: FILE_PROMPT, note: "Saved to My documents", Icon: FileIcon }] : []),
    ...(canCreateImages ? [{ prompt: IMAGE_PROMPT, note: imagesNote, Icon: ImageIcon }] : []),
  ];
  return (
    <div className="chat-empty">
      <div className="chat-empty-logo" aria-hidden="true">
        <SparkleIcon size={24} />
      </div>
      <h2 className="chat-empty-title">
        {username ? (
          <>
            How can I help, <em>{username}</em>?
          </>
        ) : (
          "How can I help?"
        )}
      </h2>
      <p className="chat-empty-text">
        Ask about your documents or anything else. Answers cite the passages they draw on, so you can check them.
      </p>
      <ul className="chat-examples" aria-label="Example questions">
        {examples.map(({ prompt, note, Icon }) => (
          <li key={prompt}>
            <button type="button" className="chat-example" onClick={() => onPick(prompt)} disabled={disabled}>
              <Icon size={18} className="chat-example-icon" />
              <span className="chat-example-text">
                <span className="chat-example-prompt">{prompt}</span>
                <small className="chat-example-note">{note}</small>
              </span>
              <ArrowRightIcon size={17} className="chat-example-go" />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
