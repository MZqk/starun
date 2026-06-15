import { expect, type Page } from "@playwright/test";
import { fitsFile } from "./fits";

export async function uploadFits(page: Page): Promise<void> {
  await page.getByLabel("选择 FITS 文件").setInputFiles(fitsFile);
  await expect(page.getByText("校验完成")).toBeVisible();
  await expect(page.getByLabel("服务器校验结果")).toContainText("HDU 1");
}

export async function waitForHistoryEntry(
  page: Page,
  fileName: string,
): Promise<void> {
  await expect.poll(() =>
    page.evaluate(async (expectedFileName) => {
      const request = indexedDB.open("starun");
      return await new Promise<boolean>((resolve, reject) => {
        request.onerror = () => reject(request.error);
        request.onsuccess = () => {
          const db = request.result;
          const transaction = db.transaction("task_history", "readonly");
          const getAll = transaction.objectStore("task_history").getAll();
          getAll.onsuccess = () => {
            resolve(getAll.result.some((entry) => entry.fileName === expectedFileName));
            db.close();
          };
          getAll.onerror = () => reject(getAll.error);
        };
      });
    }, fileName),
  ).toBe(true);
}

export async function seedHistoryEntry(
  page: Page,
  entry: Record<string, unknown>,
): Promise<void> {
  await page.goto("/history");
  await expect(page.getByText("正在读取本地历史…")).toBeHidden();
  await page.evaluate(async (value) => {
    const request = indexedDB.open("starun");
    await new Promise<void>((resolve, reject) => {
      request.onerror = () => reject(request.error);
      request.onsuccess = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains("task_history")) {
          db.close();
          reject(new Error("production task_history store was not initialized"));
          return;
        }
        const transaction = db.transaction("task_history", "readwrite");
        transaction.objectStore("task_history").put(value);
        transaction.oncomplete = () => {
          db.close();
          resolve();
        };
        transaction.onerror = () => reject(transaction.error);
      };
    });
  }, entry);
}
