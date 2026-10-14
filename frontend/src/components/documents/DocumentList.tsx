import { useEffect, useId, useState, type ReactNode } from "react";
import type { DocumentItem, DocumentSelection } from "../../api/types";
import DocumentRow from "./DocumentRow";
import { DocumentIcon } from "./icons";
import { isDocumentSelected, readyDocumentIds, toggleDocument } from "./selection";

type DocumentListProps = {
  documents: DocumentItem[];
  /**
   * An upload is queued or running. Its row above already stands in for the document until the server returns it,
   * so the "No documents yet" empty state would contradict it. (Failed uploads don't count: nothing is coming.)
   */
  uploadsActive: boolean;
  selection: DocumentSelection;
  onSelectionChange: (selection: DocumentSelection) => void;
  /** Admin: may upload to the shared knowledge base, so its section is shown even when empty. */
  canShare: boolean;
  /** Display labels of the supported file types, for the empty state. */
  supportedTypes: string[];
  onDelete: (id: number) => Promise<void>;
  onReprocess: (id: number) => Promise<void>;
};

/** Re-render once a minute so relative upload times stay current while nothing else changes. */
function useMinuteTick(): void {
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setTick((tick) => tick + 1), 60_000);
    return () => window.clearInterval(timer);
  }, []);
}

export default function DocumentList({
  documents,
  uploadsActive,
  selection,
  onSelectionChange,
  canShare,
  supportedTypes,
  onDelete,
  onReprocess,
}: DocumentListProps) {
  useMinuteTick();
  if (documents.length === 0) {
    return uploadsActive ? null : <EmptyState canShare={canShare} supportedTypes={supportedTypes} />;
  }

  const readyIds = readyDocumentIds(documents);
  const mine = documents.filter((doc) => doc.scope === "private");
  const shared = documents.filter((doc) => doc.scope === "shared");

  const rows = (items: DocumentItem[]) => (
    <ul className="docs-rows">
      {items.map((doc) => (
        <DocumentRow
          key={doc.id}
          doc={doc}
          checked={isDocumentSelected(selection, doc)}
          onToggle={() => onSelectionChange(toggleDocument(selection, doc.id, readyIds))}
          onDelete={onDelete}
          onReprocess={onReprocess}
        />
      ))}
    </ul>
  );

  return (
    <>
      <DocumentSection title="My documents" count={mine.length}>
        {mine.length > 0 ? (
          rows(mine)
        ) : (
          <p className="docs-section-empty">You haven't uploaded anything yet. Files you upload are private to you.</p>
        )}
      </DocumentSection>
      {shared.length > 0 || canShare ? (
        <DocumentSection title="Shared knowledge base" count={shared.length}>
          {shared.length > 0 ? (
            rows(shared)
          ) : (
            <p className="docs-section-empty">
              Nothing shared yet. Choose “Shared knowledge base” above to add documents every user can search.
            </p>
          )}
        </DocumentSection>
      ) : null}
    </>
  );
}

function DocumentSection({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  const headingId = useId();
  return (
    <section className="docs-section" aria-labelledby={headingId}>
      <h3 id={headingId} className="docs-section-title">
        {title}{" "}
        <span className="docs-section-count">{count}</span>
      </h3>
      {children}
    </section>
  );
}

function EmptyState({ canShare, supportedTypes }: { canShare: boolean; supportedTypes: string[] }) {
  const types =
    supportedTypes.length > 0 ? new Intl.ListFormat("en", { type: "conjunction" }).format(supportedTypes) : null;
  return (
    <div className="docs-state">
      <span className="docs-state-icon">
        <DocumentIcon size={22} />
      </span>
      <p className="docs-state-title">No documents yet</p>
      <p className="docs-state-text">
        {types ? `Upload ${types} files` : "Upload files"} and ask questions about them in chat.
      </p>
      <p className="docs-state-text">
        {canShare
          ? "Files you upload are private to you. As an admin you can also add them to the shared knowledge base, which every user can search."
          : "Files you upload are private to you."}
      </p>
    </div>
  );
}
