import type { DocumentConfig } from "../../api/types";

export const BYTES_PER_MB = 1024 * 1024;

export type TagTone = "danger" | "info" | "warning" | "success" | "accent" | "neutral";
export type FileTypeTag = { label: string; tone: TagTone };

const TAGS = new Map<string, FileTypeTag>([
  [".pdf", { label: "PDF", tone: "danger" }],
  [".docx", { label: "DOCX", tone: "info" }],
  [".pptx", { label: "PPTX", tone: "warning" }],
  [".xlsx", { label: "XLSX", tone: "success" }],
  [".csv", { label: "CSV", tone: "success" }],
  [".html", { label: "HTML", tone: "accent" }],
  [".htm", { label: "HTML", tone: "accent" }],
  [".json", { label: "JSON", tone: "accent" }],
  [".md", { label: "MD", tone: "neutral" }],
  [".markdown", { label: "MD", tone: "neutral" }],
  [".txt", { label: "TXT", tone: "neutral" }],
  // Images the assistant generated (they can't be uploaded).
  [".png", { label: "PNG", tone: "accent" }],
  [".jpg", { label: "JPG", tone: "accent" }],
  [".jpeg", { label: "JPG", tone: "accent" }],
  [".webp", { label: "WEBP", tone: "accent" }],
]);

/** Old binary Office formats the server rejects, with the format to save them as instead. */
const LEGACY_FORMATS = new Map([
  [".doc", ".docx"],
  [".ppt", ".pptx"],
  [".xls", ".xlsx"],
]);

/** Lower-case extension including the dot (".pdf"), or "" when the name has none. */
function fileExtension(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot > 0 && dot < filename.length - 1 ? filename.slice(dot).toLowerCase() : "";
}

function tagForExtension(extension: string): FileTypeTag {
  return TAGS.get(extension) ?? { label: extension ? extension.slice(1, 5).toUpperCase() : "FILE", tone: "neutral" };
}

export function fileTypeTag(filename: string): FileTypeTag {
  return tagForExtension(fileExtension(filename));
}

/** Distinct display labels for the supported extensions: [".html", ".htm", ".md"] -> ["HTML", "MD"]. */
export function supportedTypeLabels(extensions: readonly string[]): string[] {
  return [...new Set(extensions.map((extension) => tagForExtension(extension.toLowerCase()).label))];
}

export type UploadRules = { extensions: readonly string[]; maxUploadMb: number };

export function uploadRules(config: DocumentConfig): UploadRules {
  return {
    extensions: config.supported_extensions.map((extension) => extension.toLowerCase()),
    maxUploadMb: config.max_upload_mb,
  };
}

/** Client-side pre-check of the server's upload rules. Returns an error message, or null when the file may be sent. */
export function validateUpload(file: File, rules: UploadRules): string | null {
  const extension = fileExtension(file.name);
  if (!rules.extensions.includes(extension)) {
    const modern = LEGACY_FORMATS.get(extension);
    if (modern) return `Legacy ${extension} files aren't supported. Save it as ${modern} and upload that instead.`;
    return extension ? `${extension} files aren't supported.` : "Files without an extension aren't supported.";
  }
  if (file.size === 0) return "This file is empty.";
  if (file.size > rules.maxUploadMb * BYTES_PER_MB) {
    return `Too large (${(file.size / BYTES_PER_MB).toFixed(1)} MB). The limit is ${rules.maxUploadMb} MB per file.`;
  }
  return null;
}
