// "Did you mean …?" for common email-domain typos (docs/briefs/signup-auth.md 13): a suggestion, never a block.

const COMMON_DOMAINS = [
  "gmail.com",
  "googlemail.com",
  "yahoo.com",
  "yahoo.co.in",
  "yahoo.in",
  "outlook.com",
  "hotmail.com",
  "live.com",
  "icloud.com",
  "me.com",
  "rediffmail.com",
  "proton.me",
  "protonmail.com",
  "aol.com",
  "zoho.com",
];

/** Real providers a letter away from a common domain: never "corrected". */
const OTHER_PROVIDERS = new Set(["ymail.com", "email.com", "mail.com"]);

/** Edits (insertions, deletions, substitutions and swaps of neighbours) between two strings. */
function editDistance(a: string, b: string): number {
  const rows = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array<number>(b.length).fill(0)]);
  for (let j = 1; j <= b.length; j++) rows[0][j] = j;
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      rows[i][j] = Math.min(rows[i - 1][j] + 1, rows[i][j - 1] + 1, rows[i - 1][j - 1] + cost);
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
        rows[i][j] = Math.min(rows[i][j], rows[i - 2][j - 2] + 1);
      }
    }
  }
  return rows[a.length][b.length];
}

/** Short domains are two letters from other real ones (x.com, aim.com): they get one edit. */
const maxEdits = (candidate: string) => (candidate.length <= 7 ? 1 : 2);

function splitTopLevel(domain: string): [string, string] {
  const dot = domain.lastIndexOf(".");
  return dot === -1 ? [domain, ""] : [domain.slice(0, dot), domain.slice(dot + 1)];
}

/** The same name under another real top-level domain (yahoo.it, yahoo.ca, proton.ch), not a typo of ours. */
function otherTopLevel(domain: string, candidate: string): boolean {
  const [name, topLevel] = splitTopLevel(domain);
  const [candidateName, candidateTopLevel] = splitTopLevel(candidate);
  if (name !== candidateName) return false;
  const bothCountries = topLevel.length === 2 && candidateTopLevel.length === 2;
  return bothCountries || editDistance(topLevel, candidateTopLevel) > 1;
}

/** `value` with its domain replaced by the nearest common one within its edit limit; null when there is none. */
export function suggestEmail(value: string): string | null {
  const address = value.trim();
  const at = address.lastIndexOf("@");
  const domain = address.slice(at + 1).toLowerCase();
  if (at < 1 || !domain || COMMON_DOMAINS.includes(domain) || OTHER_PROVIDERS.has(domain)) return null;
  let best: string | null = null;
  let bestEdits = Number.POSITIVE_INFINITY;
  for (const candidate of COMMON_DOMAINS) {
    const edits = editDistance(domain, candidate);
    if (edits < bestEdits && edits <= maxEdits(candidate) && !otherTopLevel(domain, candidate)) {
      best = candidate;
      bestEdits = edits;
    }
  }
  return best === null ? null : `${address.slice(0, at)}@${best}`;
}
