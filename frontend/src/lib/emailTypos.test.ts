import { describe, expect, it } from "vitest";
import { suggestEmail } from "./emailTypos";

describe("suggestEmail", () => {
  it.each([
    ["maya@gmial.com", "maya@gmail.com"], // two letters swapped: one edit
    ["maya@gmai.com", "maya@gmail.com"],
    ["maya@gmail.con", "maya@gmail.com"],
    ["maya@gmail.co", "maya@gmail.com"],
    ["maya@gmailcom", "maya@gmail.com"],
    ["maya@gnail.cmo", "maya@gmail.com"], // two edits
    ["maya@gamil.co", "maya@gmail.com"],
    ["Maya.Chen+docs@GMAIL.CON", "Maya.Chen+docs@gmail.com"],
    ["  maya@hotmial.com ", "maya@hotmail.com"],
    ["maya@outlok.com", "maya@outlook.com"],
    ["maya@yaho.com", "maya@yahoo.com"],
    ["maya@yahoo.co", "maya@yahoo.com"],
    ["maya@yahooo.co.in", "maya@yahoo.co.in"],
    ["maya@yahoo.inn", "maya@yahoo.in"],
    ["maya@iclod.com", "maya@icloud.com"],
    ["maya@rediffmial.com", "maya@rediffmail.com"],
    ["maya@rediffmail.co", "maya@rediffmail.com"],
    ["maya@protonmial.com", "maya@protonmail.com"],
    ["maya@proton.mee", "maya@proton.me"],
    ["maya@zoho.co", "maya@zoho.com"],
    ["maya@aol.co", "maya@aol.com"],
    ["maya@me.co", "maya@me.com"],
    ["maya@googlemial.com", "maya@googlemail.com"],
    ["a@b@gmial.com", "a@b@gmail.com"], // the domain is after the last @
  ])("suggests %s → %s", (value, suggestion) => {
    expect(suggestEmail(value)).toBe(suggestion);
  });

  it.each([
    "maya@gmail.com",
    "maya@GMail.com",
    "maya@yahoo.co.in",
    "maya@proton.me",
    "maya@zoho.com",
    "maya@me.com",
  ])("leaves the common domain %s alone", (value) => {
    expect(suggestEmail(value)).toBeNull();
  });

  it.each([
    "maya@studio-nine.in", // an ordinary domain, far from every common one
    "maya@example.com",
    "maya@gmaiiil.coom", // three edits
    "maya@x.com", // short domains get one edit: x.com isn't me.com
    "maya@aim.com",
    "maya@msn.com",
    "maya@ymail.com", // real providers a letter from gmail.com
    "maya@email.com",
    "maya@mail.com",
    "maya@yahoo.it", // the same provider in another country
    "maya@yahoo.co.uk",
    "maya@yahoo.ca",
    "maya@live.ca",
    "maya@proton.ch",
    "maya@protonmail.ch",
    "maya@zoho.in",
    "maya@hotmail.co.uk",
  ])("doesn't correct %s", (value) => {
    expect(suggestEmail(value)).toBeNull();
  });

  it.each(["", "maya", "maya@", "@gmial.com", "   "])("ignores the incomplete address %j", (value) => {
    expect(suggestEmail(value)).toBeNull();
  });
});
