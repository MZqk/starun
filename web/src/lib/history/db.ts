export const STARUN_DATABASE_NAME = "starun";
export const STARUN_DATABASE_VERSION = 1;
export const TASK_HISTORY_STORE = "task_history";
export const CLIENT_SETTINGS_STORE = "client_settings";

export class IndexedDBUnavailableError extends Error {
  readonly cause?: unknown;

  constructor(message = "IndexedDB is unavailable.", options?: { cause?: unknown }) {
    super(message);
    this.name = "IndexedDBUnavailableError";
    this.cause = options?.cause;
  }
}

const databasePromises = new Map<IDBFactory, Promise<IDBDatabase>>();

function getIndexedDB(): IDBFactory {
  const factory = globalThis.indexedDB;
  if (!factory) {
    throw new IndexedDBUnavailableError(
      "IndexedDB is unavailable in this environment.",
    );
  }
  return factory;
}

function createIndexIfMissing(
  store: IDBObjectStore,
  name: string,
  keyPath: string,
): void {
  if (!store.indexNames.contains(name)) {
    store.createIndex(name, keyPath);
  }
}

function upgradeSchema(request: IDBOpenDBRequest): void {
  const db = request.result;
  const transaction = request.transaction;
  if (!transaction) {
    throw new IndexedDBUnavailableError(
      "IndexedDB did not provide an upgrade transaction.",
    );
  }

  const historyStore = db.objectStoreNames.contains(TASK_HISTORY_STORE)
    ? transaction.objectStore(TASK_HISTORY_STORE)
    : db.createObjectStore(TASK_HISTORY_STORE, { keyPath: "taskId" });
  createIndexIfMissing(historyStore, "createdAt", "createdAt");
  createIndexIfMissing(historyStore, "lastStatus", "lastStatus");
  createIndexIfMissing(historyStore, "expiresAt", "expiresAt");

  if (!db.objectStoreNames.contains(CLIENT_SETTINGS_STORE)) {
    db.createObjectStore(CLIENT_SETTINGS_STORE, { keyPath: "key" });
  }
}

export function openStarunDatabase(
  indexedDb?: IDBFactory,
): Promise<IDBDatabase> {
  let factory: IDBFactory;
  try {
    factory = indexedDb ?? getIndexedDB();
  } catch (error) {
    return Promise.reject(error);
  }

  const cached = databasePromises.get(factory);
  if (cached) {
    return cached;
  }

  const databasePromise = new Promise<IDBDatabase>((resolve, reject) => {
    let request: IDBOpenDBRequest;
    let settled = false;
    const rejectOnce = (error: IndexedDBUnavailableError): void => {
      if (settled) {
        return;
      }
      settled = true;
      reject(error);
    };
    try {
      request = factory.open(STARUN_DATABASE_NAME, STARUN_DATABASE_VERSION);
    } catch (error) {
      rejectOnce(
        error instanceof IndexedDBUnavailableError
          ? error
          : new IndexedDBUnavailableError("Unable to open IndexedDB.", {
              cause: error,
            }),
      );
      return;
    }

    request.onupgradeneeded = () => upgradeSchema(request);
    request.onblocked = () => {
      rejectOnce(
        new IndexedDBUnavailableError(
          "IndexedDB is blocked by another open connection.",
        ),
      );
    };
    request.onerror = () => {
      rejectOnce(
        new IndexedDBUnavailableError("Unable to open IndexedDB.", {
          cause: request.error,
        }),
      );
    };
    request.onsuccess = () => {
      const db = request.result;
      if (settled) {
        db.close();
        return;
      }
      settled = true;
      db.onversionchange = () => {
        db.close();
        databasePromises.delete(factory);
      };
      resolve(db);
    };
  }).catch((error) => {
    databasePromises.delete(factory);
    throw error;
  });

  databasePromises.set(factory, databasePromise);
  return databasePromise;
}

export async function closeStarunDatabase(): Promise<void> {
  const pendingDatabases = [...databasePromises.values()];
  databasePromises.clear();
  for (const pending of pendingDatabases) {
    try {
      (await pending).close();
    } catch {
      // Opening failures are already exposed to their original caller.
    }
  }
}

export function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () =>
      reject(request.error ?? new Error("IndexedDB request failed."));
  });
}

export function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onabort = () =>
      reject(transaction.error ?? new Error("IndexedDB transaction aborted."));
    transaction.onerror = () => {
      // The abort event carries the final transaction failure.
    };
  });
}
