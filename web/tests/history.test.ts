import "fake-indexeddb/auto";
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  closeStarunDatabase,
  IndexedDBUnavailableError,
  openStarunDatabase,
  transactionDone,
} from "../src/lib/history/db";
import {
  createHistoryRepository,
  HistoryValidationError,
  TaskHistoryRepository,
} from "../src/lib/history/repository";
import type { TaskHistorySummary } from "../src/lib/history/types";

const baseEntry: TaskHistorySummary = {
  taskId: "task-1",
  type: "analysis",
  fileName: "m42.fits",
  style: null,
  lastStatus: "queued",
  createdAt: "2026-06-13T00:00:00Z",
  expiresAt: null,
  summary: { target: "M42" },
  resultAvailable: false,
  updatedAt: "2026-06-13T00:00:00Z",
};

describe("task history repository", () => {
  beforeEach(() => {
    vi.stubGlobal("indexedDB", new IDBFactory());
  });

  afterEach(async () => {
    await closeStarunDatabase();
    vi.unstubAllGlobals();
  });

  it("creates the version 1 stores and indexes", async () => {
    const db = await openStarunDatabase();

    expect(db.version).toBe(1);
    expect([...db.objectStoreNames]).toEqual(["client_settings", "task_history"]);

    const transaction = db.transaction("task_history", "readonly");
    const store = transaction.objectStore("task_history");
    expect(store.keyPath).toBe("taskId");
    expect([...store.indexNames]).toEqual(["createdAt", "expiresAt", "lastStatus"]);
    await transactionDone(transaction);
  });

  it("closes and resets the cached connection on versionchange", async () => {
    const first = await openStarunDatabase();

    first.onversionchange?.(
      new Event("versionchange") as unknown as IDBVersionChangeEvent,
    );

    expect(() => first.transaction("task_history", "readonly")).toThrow();
    const second = await openStarunDatabase();
    expect(second).not.toBe(first);
    expect(second.version).toBe(1);
  });

  it("stores, updates, and lists newest first", async () => {
    const repository = new TaskHistoryRepository();
    await repository.upsert(baseEntry);
    await repository.upsert({
      ...baseEntry,
      taskId: "task-2",
      createdAt: "2026-06-14T00:00:00Z",
      updatedAt: "2026-06-14T00:00:00Z",
    });
    await repository.updateStatus("task-1", "completed", {
      resultAvailable: true,
      summary: { score: 0.9 },
      updatedAt: "2026-06-15T00:00:00Z",
    });

    expect(await repository.get("task-1")).toMatchObject({
      lastStatus: "completed",
      resultAvailable: true,
      summary: { score: 0.9 },
    });
    expect((await repository.list()).map((entry) => entry.taskId)).toEqual([
      "task-2",
      "task-1",
    ]);

    await repository.markExpired("task-2", "2026-06-16T00:00:00Z");
    expect(await repository.get("task-2")).toMatchObject({
      lastStatus: "expired",
      resultAvailable: false,
    });
    await repository.remove("task-1");
    expect(await repository.get("task-1")).toBeNull();
    await repository.clear();
    expect(await repository.list()).toEqual([]);
  });

  it.each([
    ["extra scalar", { unexpected: "value" }],
    ["Blob", { file: new Blob(["bad"]) }],
    ["File", { file: new File(["bad"], "bad.fits") }],
    ["ArrayBuffer", { file: new ArrayBuffer(4) }],
    ["DataView", { file: new DataView(new ArrayBuffer(4)) }],
    ["typed array", { file: new Uint8Array([1, 2]) }],
  ])("rejects a top-level %s field without writing", async (_label, extra) => {
    const repository = new TaskHistoryRepository();

    await expect(
      repository.upsert({ ...baseEntry, ...extra } as TaskHistorySummary),
    ).rejects.toBeInstanceOf(HistoryValidationError);
    expect(await repository.list()).toEqual([]);
  });

  it("creates a repository for the supplied IndexedDB and accepts minimal upserts", async () => {
    const fakeIndexedDb = new IDBFactory();
    vi.stubGlobal("indexedDB", undefined);
    const repository = createHistoryRepository(fakeIndexedDb);

    await repository.upsert({
      taskId: "task-minimal",
      type: "analysis",
      fileName: "m31.fits",
      lastStatus: "queued",
      createdAt: "2026-06-11T00:00:00Z",
      expiresAt: "2026-06-12T00:00:00Z",
      resultAvailable: false,
    });

    expect(await repository.get("task-minimal")).toEqual({
      taskId: "task-minimal",
      type: "analysis",
      fileName: "m31.fits",
      style: null,
      lastStatus: "queued",
      createdAt: "2026-06-11T00:00:00Z",
      expiresAt: "2026-06-12T00:00:00Z",
      summary: null,
      resultAvailable: false,
      updatedAt: "2026-06-11T00:00:00Z",
    });
  });

  it("does not regress terminal records from delayed or concurrent updates", async () => {
    const repository = new TaskHistoryRepository();
    await repository.upsert({
      ...baseEntry,
      lastStatus: "completed",
      resultAvailable: true,
      updatedAt: "2026-06-15T00:00:00Z",
    });

    await repository.upsert({
      ...baseEntry,
      lastStatus: "running",
      updatedAt: "2026-06-14T00:00:00Z",
    });
    await repository.updateStatus("task-1", "cancelling", {
      updatedAt: "2026-06-14T01:00:00Z",
    });

    expect(await repository.get("task-1")).toMatchObject({
      lastStatus: "completed",
      resultAvailable: true,
      updatedAt: "2026-06-15T00:00:00Z",
    });

    await repository.upsert({
      ...baseEntry,
      taskId: "task-concurrent",
    });
    await Promise.all([
      repository.updateStatus("task-concurrent", "completed", {
        resultAvailable: true,
        updatedAt: "2026-06-15T00:00:00Z",
      }),
      repository.updateStatus("task-concurrent", "running", {
        updatedAt: "2026-06-14T00:00:00Z",
      }),
    ]);
    expect(await repository.get("task-concurrent")).toMatchObject({
      lastStatus: "completed",
      resultAvailable: true,
    });
  });

  it("ignores delayed same-status completed upserts and merges equal-time results", async () => {
    const repository = new TaskHistoryRepository();
    await repository.upsert({
      ...baseEntry,
      lastStatus: "completed",
      resultAvailable: true,
      summary: { score: 0.9, details: "complete" },
      updatedAt: "2026-06-15T00:00:00Z",
    });

    await repository.upsert({
      ...baseEntry,
      lastStatus: "completed",
      resultAvailable: false,
      summary: null,
      updatedAt: "2026-06-14T00:00:00Z",
    });
    await repository.upsert({
      ...baseEntry,
      lastStatus: "completed",
      resultAvailable: false,
      summary: { score: 0.9 },
      updatedAt: "2026-06-15T00:00:00Z",
    });

    expect(await repository.get("task-1")).toMatchObject({
      lastStatus: "completed",
      resultAvailable: true,
      summary: { score: 0.9, details: "complete" },
      updatedAt: "2026-06-15T00:00:00Z",
    });
  });

  it("ignores delayed same-status updateStatus calls and preserves equal-time results", async () => {
    const repository = new TaskHistoryRepository();
    await repository.upsert({
      ...baseEntry,
      lastStatus: "completed",
      resultAvailable: true,
      summary: { score: 0.9, details: "complete" },
      updatedAt: "2026-06-15T00:00:00Z",
    });

    const delayed = await repository.updateStatus("task-1", "completed", {
      resultAvailable: false,
      summary: null,
      updatedAt: "2026-06-14T00:00:00Z",
    });
    const equalTime = await repository.updateStatus("task-1", "completed", {
      resultAvailable: false,
      summary: { score: 0.9 },
      updatedAt: "2026-06-15T00:00:00Z",
    });

    expect(delayed).toMatchObject({
      resultAvailable: true,
      summary: { score: 0.9, details: "complete" },
    });
    expect(equalTime).toMatchObject({
      resultAvailable: true,
      summary: { score: 0.9, details: "complete" },
    });
    expect(await repository.get("task-1")).toMatchObject({
      resultAvailable: true,
      summary: { score: 0.9, details: "complete" },
      updatedAt: "2026-06-15T00:00:00Z",
    });
  });

  it.each([
    ["Blob", new Blob(["bad"])],
    ["File", new File(["bad"], "bad.fits")],
    ["ArrayBuffer", new ArrayBuffer(4)],
    ["DataView", new DataView(new ArrayBuffer(4))],
    ["typed array", new Uint16Array([1, 2])],
  ])("rejects nested %s values before writing", async (_label, unsafeValue) => {
    const repository = new TaskHistoryRepository();

    await expect(
      repository.upsert({
        ...baseEntry,
        summary: { level1: [{ level2: unsafeValue }] } as never,
      }),
    ).rejects.toBeInstanceOf(HistoryValidationError);
    expect(await repository.list()).toEqual([]);
  });

  it("rejects oversized summaries and filenames before writing", async () => {
    const repository = new TaskHistoryRepository();

    await expect(
      repository.upsert({
        ...baseEntry,
        summary: { text: "x".repeat(64 * 1024) },
      }),
    ).rejects.toThrow(HistoryValidationError);
    await expect(
      repository.upsert({ ...baseEntry, fileName: "x".repeat(256) }),
    ).rejects.toThrow(HistoryValidationError);
    expect(await repository.list()).toEqual([]);
  });

  it("surfaces transaction aborts", async () => {
    const db = await openStarunDatabase();
    const transaction = db.transaction("task_history", "readwrite");
    const completion = transactionDone(transaction);
    transaction.abort();

    await expect(completion).rejects.toThrow(/abort/i);
  });

  it("exposes blocked browser storage without falling back", async () => {
    await closeStarunDatabase();
    vi.stubGlobal("indexedDB", undefined);

    await expect(openStarunDatabase()).rejects.toBeInstanceOf(
      IndexedDBUnavailableError,
    );
  });

  it("closes a connection that succeeds after a blocked open was rejected", async () => {
    const resultHolder: { value?: IDBDatabase } = {};
    const request = {
      get result() {
        return resultHolder.value;
      },
      error: null,
      transaction: null,
      onblocked: null,
      onerror: null,
      onsuccess: null,
      onupgradeneeded: null,
    } as unknown as IDBOpenDBRequest;
    const factory = {
      open: vi.fn(() => request),
    } as unknown as IDBFactory;
    const opening = openStarunDatabase(factory);

    request.onblocked?.(
      new Event("blocked") as unknown as IDBVersionChangeEvent,
    );
    await expect(opening).rejects.toBeInstanceOf(IndexedDBUnavailableError);

    const close = vi.fn();
    resultHolder.value = { close } as unknown as IDBDatabase;
    request.onsuccess?.(new Event("success"));
    expect(close).toHaveBeenCalledOnce();
  });
});
