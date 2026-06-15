import { expect, test } from "@playwright/test";
import { uploadFits, waitForHistoryEntry } from "./fixtures/helpers";

test("mobile navigation reaches home, analysis report, history, and task status", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "让每一帧深空数据，得到专业判断" })).toBeVisible();

  await page.getByRole("button", { name: "打开导航菜单" }).click();
  await page.getByTestId("mobile-navigation").getByRole("link", { name: "专业分析" }).click();
  await uploadFits(page);
  await page.getByRole("button", { name: "开始专业分析" }).click();
  await expect(page.getByRole("heading", { name: "演示专业指标" })).toBeVisible();
  await expect(page.getByText("已完成")).toBeVisible();
  await waitForHistoryEntry(page, "starun-e2e.fits");

  await page.getByRole("button", { name: "打开导航菜单" }).click();
  await page.getByTestId("mobile-navigation").getByRole("link", { name: "历史记录" }).click();
  await expect(page.getByRole("article").filter({ hasText: "starun-e2e.fits" })).toBeVisible();
});
