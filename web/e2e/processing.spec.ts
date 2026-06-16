import { expect, test } from "@playwright/test";
import { uploadFits } from "./fixtures/helpers";

test("processing defaults to balanced, streams status events, and exposes AI artifacts", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.goto("/processing");
  await expect(page.getByRole("radio", { name: /平衡/ })).toBeChecked();
  await uploadFits(page);
  await page.getByRole("button", { name: "开始 AI 自动出图" }).click();

  const eventLog = page
    .getByRole("heading", { name: "处理日志" })
    .locator("xpath=ancestor::section");
  await expect(eventLog).toContainText("Task Started");
  await expect(eventLog).toContainText("Tool Started");
  await expect(page.getByText("处理中")).toBeVisible();
  await expect(page.getByText("已完成")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /下载 (processing-reference\.png|generated-artwork\.(png|jpg))/ }),
  ).toHaveCount(0);
  await expect(page.getByText("已完成")).toBeVisible({ timeout: 90_000 });
  await expect(page.getByRole("button", { name: "下载 processing-reference.png" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: /下载 generated-artwork\.(png|jpg)/ }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "下载 art-direction.json" })).toBeVisible();
});

test("a running processing task can be cancelled", async ({ page }) => {
  await page.goto("/processing");
  await uploadFits(page);
  await page.getByRole("button", { name: "开始 AI 自动出图" }).click();
  const cancel = page.getByRole("button", { name: "取消任务" });
  await expect(cancel).toBeVisible();
  await cancel.click();
  await expect(page.getByText("已取消")).toBeVisible();
});
