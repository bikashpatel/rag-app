import { fileTypeTag } from "./fileTypes";
import "./documents.css";

/** "md" (default) 34×40 for rows; "lg" 40×48 for chat file cards; "sm" 22×26 for attachment chips and upload rows. */
export type FileTagSize = "sm" | "md" | "lg";

/**
 * The file type as a small sheet of paper with a folded corner and its label ("PDF", "DOCX", ...), in a fixed colour
 * per type (visual spec 6.5). Decorative: the filename next to it already names the type.
 */
export default function FileTag({ filename, size = "md" }: { filename: string; size?: FileTagSize }) {
  const tag = fileTypeTag(filename);
  const classes = ["docs-tag", `docs-tag-${tag.tone}`, size === "md" ? null : `docs-tag-${size}`].filter(Boolean).join(" ");
  return (
    <span className={classes} aria-hidden="true">
      {tag.label}
    </span>
  );
}
