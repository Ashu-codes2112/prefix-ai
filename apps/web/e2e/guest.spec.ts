import { expect, test } from "@playwright/test";

test("a guest tries the demo: sees the demo banner, the nurse inbox and a demo patient", async ({
  page,
  context,
}) => {
  await page.goto("/login");
  await page.getByRole("button", { name: "Try as Guest" }).click();

  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(
    page.getByRole("status").filter({ hasText: "Demo mode" }),
  ).toBeVisible();

  // No Twilio values in tests, so messages are only logged, and the app says so.
  await page.getByRole("link", { name: "Messaging: demo mode" }).click();
  await expect(
    page.getByRole("heading", { level: 2, name: "Messaging: demo mode" }),
  ).toBeVisible();
  await expect(
    page.getByText("Only admins can send a test message."),
  ).toBeVisible();

  await page.getByRole("link", { name: "Inbox" }).click();
  await expect(
    page.getByRole("heading", { name: "Nurse inbox" }),
  ).toBeVisible();

  await page.goto("/dashboard");
  const patientPage = context.waitForEvent("page");
  // The demo opens the patient view in a new tab; do not wait for the click to settle.
  await page
    .getByRole("button", { name: "View patient demo (English)" })
    .click({ noWaitAfter: true });
  const portal = await patientPage;
  await portal.waitForURL(/\/p\/home$/);
  await expect(portal.getByRole("heading", { level: 1 })).toContainText(
    "Hello,",
  );
});
