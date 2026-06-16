import { expect, test } from "@playwright/test";
import {
  seedHistoryEntry,
  uploadFits,
  waitForHistoryEntry,
} from "./fixtures/helpers";

test("history resumes polling after navigation and refresh", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/processing");
  await uploadFits(page);
  await page.getByRole("button", { name: "开始 AI 自动出图" }).click();
  await waitForHistoryEntry(page, "starun-e2e.fits");

  await page.goto("/history");
  const entry = page.getByRole("article").filter({ hasText: "starun-e2e.fits" });
  await expect(entry).toBeVisible();
  await entry.getByRole("link", { name: "打开并继续" }).click();
  await expect(page).toHaveURL(/\/processing\?task=/);
  const taskId = new URL(page.url()).searchParams.get("task");
  expect(taskId).not.toBeNull();
  let taskRequests = 0;
  let eventRequests = 0;
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (request.method() !== "GET" || !url.pathname.includes(`/api/tasks/${taskId}`)) {
      return;
    }
    if (url.pathname.endsWith("/events")) eventRequests += 1;
    else taskRequests += 1;
  });
  await page.reload();
  await expect(page.getByText("处理中")).toBeVisible();
  await expect.poll(() => taskRequests).toBeGreaterThanOrEqual(2);
  await expect.poll(() => eventRequests).toBeGreaterThanOrEqual(2);
  await expect(page.getByText("已完成")).toBeVisible({ timeout: 90_000 });
});

test("an expired result remains as a local summary", async ({ page }) => {
  await seedHistoryEntry(page, {
    taskId: "expired-local-summary",
    type: "processing",
    fileName: "expired-summary.fits",
    style: "balanced",
    lastStatus: "completed",
    createdAt: "2025-01-01T00:00:00.000Z",
    expiresAt: "2025-01-02T00:00:00.000Z",
    summary: { demo: false, qualityScore: 0.9 },
    resultAvailable: true,
    updatedAt: "2025-01-01T00:01:00.000Z",
  });

  await page.goto("/history");
  const entry = page.getByRole("article").filter({ hasText: "expired-summary.fits" });
  await expect(entry).toContainText("已过期");
  await expect(entry).toContainText("结果不可用");
  await expect(entry.getByRole("link", { name: "打开并继续" })).toHaveCount(0);
});
