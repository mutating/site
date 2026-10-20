import { describe, expect, it } from "vitest";
import { githubAuthorizationURL } from "../../../../platform/web/src/lib/github";

describe("GitHub authorization URLs", () => {
  it("accepts a GitHub HTTPS authorization redirect", () => {
    const url = "https://github.com/login/oauth/authorize?client_id=fixture&state=one-time";
    expect(githubAuthorizationURL(url)).toBe(url);
  });
  it.each([
    "https://evil.test/", "https://github.com.evil.test/", "http://github.com/",
    "https://user:password@github.com/", "//github.com/login/oauth/authorize", "javascript:alert(1)",
  ])("rejects an unexpected redirect: %s", (value) => {
    expect(() => githubAuthorizationURL(value)).toThrow();
  });
});
