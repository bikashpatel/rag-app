import { describe, expect, it } from "vitest";
import { greetingName, nameProblem, nameRefusal, normalizeName, personName } from "./names";

const char = (...codes: number[]) => String.fromCodePoint(...codes);
const NBSP = char(0xa0);
const IDEOGRAPHIC_SPACE = char(0x3000);
const ZWJ = char(0x200d);
const ZWNJ = char(0x200c);

const account = (overrides: Partial<Parameters<typeof personName>[0]> = {}) => ({
  display_name: null,
  username: "user-xtimkcnf",
  email: null,
  ...overrides,
});

describe("normalizeName", () => {
  it("trims and collapses every kind of space to one", () => {
    expect(normalizeName(`  Bikash\t${NBSP}Kumar${IDEOGRAPHIC_SPACE}\n Patel  `)).toBe("Bikash Kumar Patel");
  });

  it("composes the name (NFC), so the server's length and comparisons agree", () => {
    expect(normalizeName(`Jose${char(0x301)}`)).toBe(`Jos${char(0xe9)}`);
  });
});

describe("nameProblem", () => {
  it("accepts names in any script, with accents, apostrophes, dots and joiners", () => {
    for (const name of ["Bikash Patel", "José Álvarez-Núñez", "Dr. A. P. J. Abdul Kalam", "O'Brien", "बिकाश पटेल", "李小龙"]) {
      expect(nameProblem(name)).toBeNull();
    }
    expect(nameProblem(`क्${ZWJ}ष ${ZWNJ}`)).toBeNull();
  });

  it("asks for a name when only spaces are given", () => {
    expect(nameProblem("")).toBe("Enter your name.");
    expect(nameProblem(` ${NBSP}\t`)).toBe("Enter your name.");
  });

  it("allows 80 characters, counted as the server counts them", () => {
    expect(nameProblem("a".repeat(80))).toBeNull();
    expect(nameProblem("a".repeat(81))).toBe("Use at most 80 characters.");
    expect(nameProblem(char(0x1f600).repeat(80))).toBeNull();
  });

  it("refuses < and >", () => {
    expect(nameProblem("<script>")).toBe("Leave out < and >.");
    expect(nameProblem("Maya > Chen")).toBe("Leave out < and >.");
  });

  it("refuses control and invisible characters other than the joiners", () => {
    for (const code of [0x0, 0x7, 0x1c, 0x7f, 0x85, 0x200b, 0x202e, 0xfeff, 0xe000]) {
      expect(nameProblem(`Maya${char(code)}Chen`)).toBe("Remove the invisible or control characters.");
    }
  });
});

describe("nameRefusal", () => {
  it("reads the server's refusal of the name, preferring our own sentence", () => {
    expect(nameRefusal("name: Value error, Use at most 80 characters.", "Maya")).toBe("Use at most 80 characters.");
    expect(nameRefusal("name: Value error, Leave out < and >.", "<b>")).toBe("Leave out < and >.");
    expect(nameRefusal("name: Field required", "Maya")).toBe("Field required");
  });

  it("leaves refusals of other fields alone", () => {
    expect(nameRefusal("Enter a valid email address.", "Maya")).toBeNull();
    expect(nameRefusal("email: Field required", "")).toBeNull();
  });
});

describe("personName", () => {
  it("is the display name when there is one", () => {
    expect(personName(account({ display_name: "Bikash Patel", email: "b@example.com" }))).toBe("Bikash Patel");
  });

  it("is the username of an account the admin created", () => {
    expect(personName(account({ username: "bikash", email: "bikash@example.com" }))).toBe("bikash");
    expect(personName(account({ username: "user-xtimkcn1" }))).toBe("user-xtimkcn1");
  });

  it("is the email's local part for a generated sign-up handle", () => {
    expect(personName(account({ email: "bikash.patel@example.com" }))).toBe("bikash.patel");
    expect(personName(account({ email: '"a@b"@example.com' }))).toBe('"a@b"');
  });

  it("is the generated handle when there is nothing else", () => {
    expect(personName(account())).toBe("user-xtimkcnf");
  });
});

describe("greetingName", () => {
  it("is the first word of the display name", () => {
    expect(greetingName(account({ display_name: "Bikash Kumar Patel" }))).toBe("Bikash");
    expect(greetingName(account({ display_name: "बिकाश पटेल" }))).toBe("बिकाश");
  });

  it("is the whole name when the first word ends with a dot", () => {
    expect(greetingName(account({ display_name: "Dr. Rao" }))).toBe("Dr. Rao");
  });

  it("falls back to the shown name without a display name", () => {
    expect(greetingName(account({ username: "bikash" }))).toBe("bikash");
    expect(greetingName(account({ email: "maya@example.com" }))).toBe("maya");
  });
});
