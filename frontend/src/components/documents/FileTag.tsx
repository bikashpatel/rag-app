import { fileTypeTag } from "./fileTypes";

/** Colored file-type label ("PDF", "DOCX", ...). Decorative: the filename next to it already names the type. */
export default function FileTag({ filename }: { filename: string }) {
  const tag = fileTypeTag(filename);
  return (
    <span className={`docs-tag docs-tag-${tag.tone}`} aria-hidden="true">
      {tag.label}
    </span>
  );
}
