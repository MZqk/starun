import { expect, test } from "@playwright/test";
import { uploadFits } from "./fixtures/helpers";

test("processing defaults to balanced, streams status events, and exposes PNG/TIFF", async ({
  page,
}) => {
  await page.goto("/processing");
  await expect(page.getByRole("radio", { name: /平衡/ })).toBeChecked();
  await uploadFits(page);
  await page.getByRole("button", { name: "开始 Mock 自动出图" }).click();

  const eventLog = page
    .getByRole("heading", { name: "处理日志" })
    .locator("xpath=ancestor::section");
  await expect(eventLog).toContainText("Task Started");
  await expect(eventLog).toContainText("Tool Started");
  await expect(page.getByText("处理中")).toBeVisible();
  await expect(page.getByText("已完成")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /下载 (preview-demo\.png|result-demo\.tiff)/ }),
  ).toHaveCount(0);
  await expect(page.getByText("已完成")).toBeVisible();
  await expect(page.getByRole("button", { name: "下载 preview-demo.png" })).toBeVisible();
  await expect(page.getByRole("button", { name: "下载 result-demo.tiff" })).toBeVisible();
});

test("a running processing task can be cancelled", async ({ page }) => {
  await page.goto("/processing");
  await uploadFits(page);
  await page.getByRole("button", { name: "开始 Mock 自动出图" }).click();
  const cancel = page.getByRole("button", { name: "取消任务" });
  await expect(cancel).toBeVisible();
  await cancel.click();
  await expect(page.getByText("已取消")).toBeVisible();
});
