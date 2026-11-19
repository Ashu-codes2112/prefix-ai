import { expect, test } from "@playwright/test";

const BASE = "http://localhost:3000";

function scriptSource(policy: string | undefined): string {
  return (
    (policy ?? "")
      .split(";")
      .map((part) => part.trim())
      .find((part) => part.startsWith("script-src")) ?? ""
  );
}

test("pages refuse framing and sniffing, and carry a content security policy", async ({
  request,
}) => {
  const response = await request.get(`${BASE}/login`);

  const headers = response.headers();
  expect(headers["x-frame-options"]).toBe("DENY");
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["referrer-policy"]).toBe("strict-origin-when-cross-origin");
  expect(headers["content-security-policy"]).toContain(
    "frame-ancestors 'none'",
  );
  expect(headers["content-security-policy"]).toContain("connect-src 'self'");
});

test("scripts run only with a fresh nonce, never as unsafe-inline", async ({
  request,
}) => {
  const first = scriptSource(
    (await request.get(`${BASE}/login`)).headers()["content-security-policy"],
  );
  const second = scriptSource(
    (await request.get(`${BASE}/login`)).headers()["content-security-policy"],
  );

  expect(first).toMatch(/'nonce-[A-Za-z0-9+/=]{16,}'/);
  expect(first).toContain("'strict-dynamic'");
  expect(first).not.toContain("'unsafe-inline'");
  expect(first).not.toBe(second);
});

test("the sign-in page works under the policy, with no blocked scripts", async ({
  page,
}) => {
  const violations: string[] = [];
  page.on("console", (message) => {
    if (/Content Security Policy|Refused to execute/i.test(message.text())) {
      violations.push(message.text());
    }
  });

  await page.goto("/login");
  // A working button proves the page's scripts ran (hydration needs them).
  await page.getByRole("button", { name: "Try as Guest" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);

  expect(violations).toEqual([]);
});
