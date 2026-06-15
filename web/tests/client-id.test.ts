import "fake-indexeddb/auto";
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getOrCreateClientId } from "../src/lib/client-id";
import {
  closeStarunDatabase,
  openStarunDatabase,
  transactionDone,
} from "../src/lib/history/db";

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

describe("anonymous client ID", () => {
  beforeEach(() => {
    vi.stubGlobal("indexedDB", new IDBFactory());
  });

  afterEach(async () => {
    await closeStarunDatabase();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("persists and converges across concurrent calls", async () => {
    const ids = await Promise.all(
      Array.from({ length: 20 }, () => getOrCreateClientId()),
    );

    expect(new Set(ids)).toHaveLength(1);
    expect(await getOrCreateClientId()).toBe(ids[0]);
    expect(ids[0]).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
  });

  it.each([
    ["malformed", "malformed"],
    ["non-v4 UUID", "550e8400-e29b-11d4-a716-446655440000"],
    ["opaque ID", "secure_opaque_client_identifier_123"],
  ])("securely replaces a stored %s", async (_label, storedValue) => {
    const db = await openStarunDatabase();
    const write = db.transaction("client_settings", "readwrite");
    write.objectStore("client_settings").put({
      key: "anonymous_client_id",
      value: storedValue,
    });
    await transactionDone(write);

    const id = await getOrCreateClientId();

    expect(id).not.toBe(storedValue);
    expect(id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
    const read = db.transaction("client_settings", "readonly");
    const stored = await requestResult(
      read.objectStore("client_settings").get("anonymous_client_id"),
    );
    await transactionDone(read);
    expect(stored).toEqual({ key: "anonymous_client_id", value: id });
  });

  it("never accesses localStorage", async () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem");
    const setItem = vi.spyOn(Storage.prototype, "setItem");

    await getOrCreateClientId();

    expect(getItem).not.toHaveBeenCalled();
    expect(setItem).not.toHaveBeenCalled();
  });
});
