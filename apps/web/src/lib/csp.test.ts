import { describe, expect, it } from "vitest";

import { contentSecurityPolicy, newNonce } from "./csp";

function directive(policy: string, name: string): string {
  return (
    policy
      .split("; ")
      .find((part) => part.startsWith(`${name} `))
      ?.slice(name.length + 1) ?? ""
  );
}

describe("content security policy", () => {
  it("lets scripts run only with the nonce", () => {
    const scripts = directive(
      contentSecurityPolicy("abc123", false),
      "script-src",
    );

    expect(scripts).toBe("'self' 'nonce-abc123' 'strict-dynamic'");
    expect(scripts).not.toContain("unsafe-inline");
  });

  it("adds unsafe-eval only in development", () => {
    expect(contentSecurityPolicy("n", true)).toContain("'unsafe-eval'");
    expect(contentSecurityPolicy("n", false)).not.toContain("'unsafe-eval'");
  });

  it("keeps the rest of the policy strict", () => {
    const policy = contentSecurityPolicy("n", false);

    expect(directive(policy, "frame-ancestors")).toBe("'none'");
    expect(directive(policy, "object-src")).toBe("'none'");
    expect(directive(policy, "connect-src")).toBe("'self'");
  });

  it("makes a new, unguessable nonce each time", () => {
    const nonces = new Set(Array.from({ length: 50 }, () => newNonce()));

    expect(nonces.size).toBe(50);
    for (const nonce of nonces) expect(nonce).toMatch(/^[A-Za-z0-9+/]{22}==$/);
  });
});
