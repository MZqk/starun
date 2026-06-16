import { expect, test } from "@playwright/test";
import { uploadFits } from "./fixtures/helpers";

test("analysis selects the largest image HDU and continues to processing", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.goto("/analysis");
  await uploadFits(page);

  const realData = page.getByLabel("真实 FITS、HDU、头信息与基础统计");
  await expect(realData).toContainText("HDU 1");
  await expect(realData).toContainText("LARGE_IMAGE");

  await page.getByRole("button", { name: "开始专业分析" }).click();
  await expect(page.getByRole("heading", { name: "专业解读与后期建议" })).toBeVisible({
    timeout: 90_000,
  });

  await page.getByRole("link", { name: "使用此文件自动出图" }).click();
  await expect(page).toHaveURL(/\/processing\?source_task_id=/);
  await expect(page.getByText("已选择分析源")).toBeVisible();
});
