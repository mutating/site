import { describe, expect, it } from "vitest";
import { userMenuIdentity } from "../../../../platform/web/src/lib/userIdentity";

const person = (data: Partial<{ first_name: string; last_name: string; nickname: string | null; email: string }>) => ({
  first_name: "",
  last_name: "",
  nickname: null,
  email: "person@example.com",
  ...data,
});

describe("user menu identity", () => {
  it("uses the first name and the surname initial", () => {
    expect(userMenuIdentity(person({ first_name: "Евгений", last_name: "Лебедев" }))).toEqual({ name: "Евгений", initial: "Л." });
  });

  it("trims both fields and uppercases Cyrillic initials", () => {
    expect(userMenuIdentity(person({ first_name: "  Евгений  ", last_name: " ёлкин " }))).toEqual({ name: "Евгений", initial: "Ё." });
  });

  it("falls back to the nickname and then the email without a surname", () => {
    expect(userMenuIdentity(person({ nickname: "evgeniy", last_name: "" }))).toEqual({ name: "evgeniy", initial: null });
    expect(userMenuIdentity(person({ first_name: " ", nickname: " " }))).toEqual({ name: "person@example.com", initial: null });
  });

  it("keeps the surname initial when the first name is missing", () => {
    expect(userMenuIdentity(person({ nickname: "evgeniy", last_name: "Лебедев" }))).toEqual({ name: "evgeniy", initial: "Л." });
  });
});
