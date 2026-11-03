// A person's name (SPEC.md section 4, users.display_name): the rules the server applies (app.schemas_auth.person_name),
// and the one helper that decides which name the app shows for an account.

export const NAME_MAX_LENGTH = 80;

/** A username the sign-up generator made ("user-" and 8 base32 characters; app/services/auth/usernames.py). */
const GENERATED_USERNAME = /^user-[a-z2-7]{8}$/;
/** The whitespace the server collapses to one space (an explicit set, the same on both sides). */
const SPACES = /[\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/g;
/** Control, format, surrogate, private-use and unassigned characters, except the joiners some scripts need. */
const INVISIBLE = /(?![\u200c\u200d])\p{C}/u;

/** NFC, inner whitespace collapsed to one space, trimmed: the name as the server stores it. */
export function normalizeName(value: string): string {
  return value.normalize("NFC").replace(SPACES, " ").replace(/^ | $/g, "");
}

/** Why the server would refuse the name (its own sentence), or null when it's fine. */
export function nameProblem(value: string): string | null {
  const name = normalizeName(value);
  if (!name) return "Enter your name.";
  if (Array.from(name).length > NAME_MAX_LENGTH) return `Use at most ${NAME_MAX_LENGTH} characters.`;
  if (/[<>]/.test(name)) return "Leave out < and >.";
  if (INVISIBLE.test(name)) return "Remove the invisible or control characters.";
  return null;
}

/**
 * The name field's error for a server refusal about the name (a 422 whose message is "name: Value error, …"): our own
 * sentence when we find the problem too, else the server's. Null for a refusal about another field.
 */
export function nameRefusal(message: string, value: string): string | null {
  if (!message.startsWith("name:")) return null;
  return nameProblem(value) ?? message.replace(/^name: (Value error, )?/, "");
}

type Named = { display_name: string | null; username: string; email: string | null };

/**
 * The name shown for an account: the display name; else the username, unless it is a generated sign-up handle; else
 * the email's local part; else the username.
 */
export function personName({ display_name, username, email }: Named): string {
  if (display_name) return display_name;
  if (!GENERATED_USERNAME.test(username)) return username;
  const at = email?.lastIndexOf("@") ?? -1;
  return at > 0 && email ? email.slice(0, at) : username;
}

/** The chat greeting's name: the display name's first word (all of it when that word ends with "."), else personName. */
export function greetingName(user: Named): string {
  if (!user.display_name) return personName(user);
  const first = user.display_name.split(" ")[0];
  return first.endsWith(".") ? user.display_name : first;
}
