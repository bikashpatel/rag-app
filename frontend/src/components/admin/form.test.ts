import { describe, expect, it } from "vitest";
import { FormValidator, numberText } from "./form";

type Key = "name" | "notes" | "limit" | "price";

describe("FormValidator", () => {
  it("trims text and checks its length", () => {
    const v = new FormValidator<Key>();
    expect(v.text("name", "  maya  ", 3, 64)).toBe("maya");
    expect(v.valid).toBe(true);

    expect(new FormValidator<Key>().text("name", "   ", 3, 64)).toBe("");
    const blank = new FormValidator<Key>();
    blank.text("name", "   ", 3, 64);
    expect(blank.errors).toEqual({ name: "This field is required." });

    const short = new FormValidator<Key>();
    short.text("name", "ab", 3, 64);
    expect(short.errors.name).toBe("Use at least 3 characters.");

    const long = new FormValidator<Key>();
    long.text("name", "x".repeat(65), 3, 64);
    expect(long.errors.name).toBe("Use at most 64 characters.");
    expect(long.valid).toBe(false);
  });

  it("allows blank optional text as null", () => {
    const v = new FormValidator<Key>();
    expect(v.optionalText("notes", "   ", 10)).toBeNull();
    expect(v.optionalText("notes", " ok ", 10)).toBe("ok");
    v.optionalText("notes", "x".repeat(11), 10);
    expect(v.errors.notes).toBe("Use at most 10 characters.");
  });

  it("reads whole numbers within bounds", () => {
    const v = new FormValidator<Key>();
    expect(v.integer("limit", " 25 ", 1, 100)).toBe(25);
    expect(v.valid).toBe(true);

    const cases: [string, string][] = [
      ["2.5", "Enter a whole number."],
      ["", "Enter a whole number."],
      ["1e3", "Enter a whole number."],
      ["0", "Must be at least 1."],
      ["101", "Must be at most 100."],
    ];
    for (const [raw, message] of cases) {
      const check = new FormValidator<Key>();
      check.integer("limit", raw, 1, 100);
      expect(check.errors.limit, raw).toBe(message);
    }
  });

  it("reads optional decimals and integers", () => {
    const v = new FormValidator<Key>();
    expect(v.optionalNumber("price", "  ", 0, 1000)).toBeNull();
    expect(v.optionalNumber("price", "2.75", 0, 1000)).toBe(2.75);
    expect(v.optionalNumber("price", ".5", 0, 1000)).toBe(0.5);
    expect(v.valid).toBe(true);

    v.optionalNumber("price", "abc", 0, 1000);
    expect(v.errors.price).toBe("Enter a number.");

    const integer = new FormValidator<Key>();
    integer.optionalNumber("limit", "4.5", 0, 10, true);
    expect(integer.errors.limit).toBe("Enter a whole number.");
  });

  it("keeps the first error of a field", () => {
    const v = new FormValidator<Key>();
    v.fail("name", "First problem.");
    v.fail("name", "Second problem.");
    expect(v.errors).toEqual({ name: "First problem." });
  });
});

describe("numberText", () => {
  it("shows null and undefined as an empty field", () => {
    expect(numberText(null)).toBe("");
    expect(numberText(undefined)).toBe("");
    expect(numberText(0)).toBe("0");
    expect(numberText(1.5)).toBe("1.5");
  });
});
