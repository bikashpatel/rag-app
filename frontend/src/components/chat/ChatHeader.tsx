import { DocumentsIcon, MenuIcon } from "./icons";
import ModelSelect from "./ModelSelect";
import type { ModelsState } from "./useModels";

export type PanelToggle = { open: boolean; controls: string; onToggle: () => void };

type ChatHeaderProps = {
  title: string;
  models: ModelsState;
  /** Shown on medium screens, where the conversation sidebar is a drawer. */
  sidebarToggle: PanelToggle | null;
  /** Shown on narrow screens, where the document panel is a slide-over. */
  documentsToggle: PanelToggle | null;
};

export default function ChatHeader({ title, models, sidebarToggle, documentsToggle }: ChatHeaderProps) {
  return (
    <header className="chat-header">
      {sidebarToggle ? (
        <button
          type="button"
          className="btn btn-ghost btn-icon"
          onClick={sidebarToggle.onToggle}
          aria-expanded={sidebarToggle.open}
          aria-controls={sidebarToggle.controls}
          aria-label="Conversations"
          title="Conversations"
        >
          <MenuIcon size={18} />
        </button>
      ) : null}
      <h1 className="chat-title" title={title}>
        {title}
      </h1>
      <ModelSelect {...models} />
      {documentsToggle ? (
        <button
          type="button"
          className="btn btn-sm chat-docs-button"
          onClick={documentsToggle.onToggle}
          aria-expanded={documentsToggle.open}
          aria-controls={documentsToggle.controls}
          aria-label="Documents"
        >
          <DocumentsIcon size={15} />
          <span className="chat-docs-button-label">Documents</span>
        </button>
      ) : null}
    </header>
  );
}
