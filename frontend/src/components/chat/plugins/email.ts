import type { EmailAddress, GmailImportItem } from "../../../api/types";

/** A link from the server (Gmail) opened in a new tab: only https URLs are rendered as links. */
export function safeHttpsUrl(url: string | null | undefined): string | null {
  return url && /^https:\/\//i.test(url) ? url : null;
}

/** "local@bücher.de" for an internationalized domain (the address itself is the punycode form that is sent). */
export function unicodeAddress(recipient: EmailAddress): string | null {
  if (!recipient.unicode_domain) return null;
  const at = recipient.address.lastIndexOf("@");
  return `${at === -1 ? recipient.address : recipient.address.slice(0, at)}@${recipient.unicode_domain}`;
}

/**
 * `Name <address>` or `address` (the form of PendingSendRevision). A name containing an RFC 5322 special that the
 * server's parser (getaddresses) reads as structure is quoted: `,` `;` split the list, `:` starts a group (the words
 * before it would be dropped), and `[` `]` `<` `>` `(` `)` `@` `"` `\` would make the entry invalid. A "." is fine
 * unquoted ("J. Smith").
 */
export function formatAddressForInput(recipient: EmailAddress): string {
  const name = recipient.name?.trim();
  if (!name) return recipient.address;
  const quoted = /[",;:<>()[\]@\\]/.test(name) ? `"${name.replace(/(["\\])/g, "\\$1")}"` : name;
  return `${quoted} <${recipient.address}>`;
}

/** Splits "a@x.com, \"Doe, Jane\" <j@y.org>" on commas outside quotes and angle brackets. */
export function splitAddressList(text: string): string[] {
  const parts: string[] = [];
  let current = "";
  let quoted = false;
  let angle = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (char === "\\" && quoted && i + 1 < text.length) {
      current += char + text[++i];
      continue;
    }
    if (char === '"') quoted = !quoted;
    else if (char === "<" && !quoted) angle = true;
    else if (char === ">" && !quoted) angle = false;
    if ((char === "," || char === ";") && !quoted && !angle) {
      parts.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  parts.push(current);
  return parts.map((part) => part.trim()).filter(Boolean);
}

export function formatEmailDate(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleString(undefined, {
    year: sameYear ? undefined : "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** "29:41" or "1:02:03" for a positive duration. */
export function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = String(total % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${seconds}` : `${minutes}:${seconds}`;
}

export type ImportStatusView = { label: string; tone: "success" | "neutral" | "warning" | "danger" };

export function importStatusView(status: GmailImportItem["status"]): ImportStatusView {
  switch (status) {
    case "imported":
      return { label: "Imported", tone: "success" };
    case "already_existed":
      return { label: "Already in your documents", tone: "neutral" };
    case "skipped":
      return { label: "Skipped", tone: "warning" };
    case "failed":
      return { label: "Failed", tone: "danger" };
  }
}

export function plural(count: number, noun: string, pluralNoun = `${noun}s`): string {
  return `${count} ${count === 1 ? noun : pluralNoun}`;
}
