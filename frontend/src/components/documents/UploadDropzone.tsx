import { useRef } from "react";
import { UploadIcon } from "./icons";

type UploadDropzoneProps = {
  /** The file input's accept attribute, e.g. ".pdf,.docx". */
  accept: string;
  hint: string;
  disabled: boolean;
  /** Files are being dragged over the panel (the panel owns the drop handlers). */
  dragActive: boolean;
  onFiles: (files: File[]) => void;
};

export default function UploadDropzone({ accept, hint, disabled, dragActive, onFiles }: UploadDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <>
      <button
        type="button"
        className={dragActive ? "docs-dropzone docs-dropzone-active" : "docs-dropzone"}
        onClick={() => inputRef.current?.click()}
        disabled={disabled}
      >
        <span className="docs-dropzone-icon">
          <UploadIcon size={18} />
        </span>
        <span className="docs-dropzone-title">
          {dragActive ? (
            "Drop files to upload"
          ) : (
            <>
              Drop files here or <span className="docs-dropzone-link">browse</span>
            </>
          )}
        </span>
        <span className="docs-dropzone-hint">{hint}</span>
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple
        hidden
        tabIndex={-1}
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          event.target.value = "";
          if (files.length > 0) onFiles(files);
        }}
      />
    </>
  );
}
