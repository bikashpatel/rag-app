// The "+" menu's starter prompts (functional brief 9.2): put before the draft, never sent on their own.

export const STARTERS = {
  image: "Create an image: ",
  pdf: "Create a PDF document: ",
  docx: "Create a Word document: ",
  xlsx: "Create an Excel spreadsheet: ",
  md: "Create a Markdown file: ",
  search: "Search my Gmail: ",
  draft: "Draft an email: ",
} as const;

const KNOWN = Object.values(STARTERS).map((starter) => starter.trim().toLowerCase());

/**
 * The draft with `prefix` in front: unchanged when it already starts with it (case-insensitive, leading spaces
 * ignored); another starter at its start is replaced, keeping the text after it.
 */
export function withStarter(current: string, prefix: string): string {
  const draft = current.trimStart();
  // Compare a slice, so the length stays right when lowercasing changes the length of other characters.
  const startsWith = (starter: string) => draft.slice(0, starter.length).toLowerCase() === starter;
  if (startsWith(prefix.trim().toLowerCase())) return current;
  const other = KNOWN.find(startsWith);
  return prefix + (other ? draft.slice(other.length).trimStart() : draft);
}
