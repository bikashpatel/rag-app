import { assistantMessages, openChat, send } from "../support/app";
import { LIVE } from "../support/env";
import { expect, signIn, test } from "../support/fixtures";

// The one test that calls a real model: a short answer from Claude Haiku 5.5 (a fraction of a cent). Skipped unless
// E2E_LIVE=1; run it alone with `E2E_LIVE=1 npx playwright test --grep @live`.
test.use({ allowModelCalls: true });

test("a real short answer streams from Claude Haiku 5.5 and is saved", { tag: "@live" }, async ({ page, context, admin, makeUser }) => {
  test.skip(!LIVE, "calls a real model: set E2E_LIVE=1");
  test.setTimeout(120_000);
  const haiku = (await admin.listModels()).find(
    (model) => model.kind === "chat" && model.enabled && /haiku/i.test(`${model.display_name} ${model.litellm_model}`),
  );
  test.skip(!haiku, "no enabled Claude Haiku chat model");
  if (!haiku) return;
  const user = await makeUser({
    restrict_models: true,
    allowed_model_ids: [haiku.id],
    overrides: { monthly_token_limit: 20_000, rate_limit_per_min: 2 },
  });
  await signIn(context, user);
  await openChat(page);
  await expect(page.getByRole("button", { name: `Model: ${haiku.display_name}` })).toBeVisible();

  await send(page, "Reply with exactly one word: pong");
  const answer = assistantMessages(page).last();
  await expect(answer).toHaveAttribute("aria-busy", "false", { timeout: 90_000 });
  await expect(answer).toContainText(/pong/i);
  await expect(answer.getByRole("button", { name: "Copy answer" })).toBeVisible();
  await expect(page).toHaveURL(/\/c\/\d+$/);
  expect(await (await page.request.get("/api/conversations")).json()).toHaveLength(1);
});
