import { describe, expect, it } from "vitest";
import { makeDocumentConfig } from "../../test/fixtures";
import { BYTES_PER_MB, fileTypeTag, supportedTypeLabels, uploadRules, validateUpload } from "./fileTypes";

function fileOfSize(name: string, size: number): File {
  const file = new File(["x"], name);
  Object.defineProperty(file, "size", { value: size });
  return file;
}

const rules = uploadRules(makeDocumentConfig({ supported_extensions: [".PDF", ".docx", ".md"], max_upload_mb: 2 }));

describe("fileTypeTag", () => {
  it.each([
    ["Report.PDF", { label: "PDF", tone: "danger" }],
    ["notes.markdown", { label: "MD", tone: "neutral" }],
    ["photo.jpeg", { label: "JPG", tone: "accent" }],
    ["archive.tar.gz", { label: "GZ", tone: "neutral" }],
    ["model.safetensors", { label: "SAFE", tone: "neutral" }],
    ["README", { label: "FILE", tone: "neutral" }],
    [".env", { label: "FILE", tone: "neutral" }],
    ["trailing.", { label: "FILE", tone: "neutral" }],
  ])("tags %s", (filename, tag) => {
    expect(fileTypeTag(filename)).toEqual(tag);
  });
});

describe("supportedTypeLabels", () => {
  it("lists each display label once, whatever the case", () => {
    expect(supportedTypeLabels([".html", ".HTM", ".md", ".markdown", ".pdf"])).toEqual(["HTML", "MD", "PDF"]);
  });
});

describe("uploadRules", () => {
  it("lower-cases the extensions and keeps the size limit", () => {
    expect(rules).toEqual({ extensions: [".pdf", ".docx", ".md"], maxUploadMb: 2 });
  });
});

describe("validateUpload", () => {
  it("accepts a supported, non-empty file within the limit, whatever the extension's case", () => {
    expect(validateUpload(fileOfSize("Q3.Pdf", 2 * BYTES_PER_MB), rules)).toBeNull();
  });

  it("names the modern format for legacy Office files", () => {
    expect(validateUpload(fileOfSize("old.doc", 10), rules)).toBe(
      "Legacy .doc files aren't supported. Save it as .docx and upload that instead.",
    );
  });

  it("refuses unsupported and extension-less files", () => {
    expect(validateUpload(fileOfSize("setup.exe", 10), rules)).toBe(".exe files aren't supported.");
    expect(validateUpload(fileOfSize("Makefile", 10), rules)).toBe("Files without an extension aren't supported.");
  });

  it("refuses empty files", () => {
    expect(validateUpload(fileOfSize("empty.md", 0), rules)).toBe("This file is empty.");
  });

  it("refuses files over the limit with their size", () => {
    expect(validateUpload(fileOfSize("big.pdf", 2.5 * BYTES_PER_MB), rules)).toBe(
      "Too large (2.5 MB). The limit is 2 MB per file.",
    );
  });
});
