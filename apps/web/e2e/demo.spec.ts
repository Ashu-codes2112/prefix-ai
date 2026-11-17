import { expect, test } from "@playwright/test";

/**
 * The teacher's demo, from one guest login: open the patient demo, raise a problem, ask about a
 * red-flag symptom and ask to reschedule; the guest's open dashboard shows all three, newest
 * first, with the time and the urgent one in red; the Inbox and Requests agree, and the guest
 * answers the request.
 */

// "30 Oct, 1:02 pm": the facility's time.
const RAISED_AT = /\d{1,2} [A-Z][a-z]{2}, \d{1,2}:\d{2}\s(am|pm)/;

// A stuck click fails with its reason instead of waiting out the whole test.
test.use({ actionTimeout: 20_000 });

test("the whole demo works from one guest login", async ({ browser }) => {
  // The bell asks every 15 seconds, so allow for a couple of rounds.
  test.setTimeout(180_000);
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto("/login");
  await page.getByRole("button", { name: "Try as Guest" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(
    page.getByRole("status").filter({ hasText: "Demo mode" }),
  ).toBeVisible();

  // The patient demo opens a patient of the same hospital, in a new tab.
  const opened = context.waitForEvent("page");
  await page
    .getByRole("button", { name: "View patient demo (English)" })
    .click({ noWaitAfter: true });
  const portal = await opened;
  await portal.waitForURL(/\/p\/home$/);
  const hello = await portal.getByRole("heading", { level: 1 }).textContent();
  const firstName = /Hello, (\S+)/.exec(hello ?? "")?.[1] ?? "";
  expect(firstName).not.toBe("");
  const sections = portal.getByRole("navigation", { name: "Sections" });

  // 1. A problem with a prep step.
  await sections.getByRole("link", { name: "My prep", exact: true }).click();
  // Home has an "I have a problem" button too: wait until My prep is the page shown.
  await expect(portal).toHaveURL(/\/p\/prep$/);
  await expect(
    portal.getByRole("heading", { level: 1, name: "My prep" }),
  ).toBeVisible();
  // Next keeps earlier pages mounted but hidden, so only visible buttons count.
  await portal
    .getByRole("button", { name: /^I have a problem: / })
    .filter({ visible: true })
    .first()
    .click();
  const problem = portal.getByRole("dialog", {
    name: "Tell us about a problem",
  });
  await problem
    .getByLabel("Tell us what is wrong (optional)")
    .fill("I could not buy the prep solution");
  await problem.getByRole("button", { name: "Tell my care team" }).click();
  await expect(problem).toBeHidden();

  // 2. A red-flag question: escalated by the fixed rules, with or without an LLM.
  await sections.getByRole("link", { name: "Help", exact: true }).click();
  await expect(portal).toHaveURL(/\/p\/help$/);
  await portal
    .getByLabel("Your question")
    .fill("I have chest pain since this morning");
  await portal.getByRole("button", { name: "Send question" }).click();
  await expect(
    portal.getByRole("alert").filter({ hasText: "112" }),
  ).toBeVisible();

  // 3. A reschedule request.
  await portal.getByRole("button", { name: "I need to reschedule" }).click();
  const reschedule = portal.getByRole("dialog", { name: "Change your date" });
  await reschedule
    .getByLabel("Anything we should know? (optional)")
    .fill("Travelling that week");
  await reschedule.getByRole("button", { name: "Ask to reschedule" }).click();
  await expect(reschedule).toBeHidden();
  await portal.close();

  // The guest's dashboard, never reloaded: the urgent question raises a toast (and the
  // problem raises one too when its step is critical).
  await expect(
    page
      .getByRole("alert")
      .filter({ hasText: `Urgent: ${firstName}` })
      .first(),
  ).toBeVisible({ timeout: 45_000 });
  await page.getByRole("button", { name: /^Notifications/ }).click();
  const items = page
    .getByRole("list", { name: "Notifications, newest first" })
    .getByRole("link");
  await expect(items.nth(2)).toBeVisible();
  const [newest, question, raised] = [items.nth(0), items.nth(1), items.nth(2)];
  await expect(newest).toContainText(firstName);
  await expect(newest).toContainText("Asked to move the procedure");
  await expect(newest).toContainText(RAISED_AT);
  await expect(question).toContainText(firstName);
  await expect(question).toContainText("Asked the assistant about chest pain");
  await expect(question).toContainText("Urgent");
  await expect(question).toHaveClass(/border-l-urgent/);
  await expect(raised).toContainText(firstName);
  await expect(raised).toContainText("Cannot complete: ");
  await page.keyboard.press("Escape");

  // The nurse inbox has the red-flag question on top, marked urgent.
  await page.goto("/dashboard/inbox");
  const escalation = page
    .getByRole("list", { name: "Escalations, newest first" })
    .getByRole("listitem")
    .first();
  await expect(escalation).toContainText(firstName);
  await expect(escalation).toContainText("Urgent");
  await expect(escalation).toContainText(RAISED_AT);

  // Requests has the new request on top, and the guest can answer it.
  await page.goto("/dashboard/requests");
  const waiting = page
    .getByRole("region", { name: "Waiting for an answer" })
    .getByRole("listitem")
    .first();
  await expect(waiting).toContainText(firstName);
  await expect(waiting).toContainText("Asked to move the procedure");
  await waiting.getByRole("button", { name: "Decline" }).click();
  await page
    .getByRole("dialog", { name: "Decline this request?" })
    .getByRole("button", { name: "Decline" })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "Request declined" }),
  ).toBeVisible();
  await context.close();
});
