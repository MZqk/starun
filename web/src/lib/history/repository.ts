import {
  openStarunDatabase,
  requestResult,
  TASK_HISTORY_STORE,
  transactionDone,
} from "./db";
import type {
  JsonValue,
  TaskHistorySummary,
  TaskHistoryUpsert,
} from "./types";
import type { TaskStatus } from "../api/types";

const MAX_FILENAME_LENGTH = 255;
const MAX_SUMMARY_BYTES = 64 * 1024;
const TASK_TYPES = new Set(["analysis", "processing"]);
const TASK_STATUSES = new Set([
  "queued",
  "running",
  "cancelling",
  "cancelled",
  "completed",
  "review_required",
  "failed",
  "expired",
]);
const PROCESSING_STYLES = new Set(["realistic", "balanced", "artistic"]);
const UPSERT_FIELDS = new Set([
  "taskId",
  "type",
  "fileName",
  "style",
  "lastStatus",
  "createdAt",
  "expiresAt",
  "summary",
  "resultAvailable",
  "updatedAt",
]);
const VALID_STATUS_TRANSITIONS: Record<TaskStatus, ReadonlySet<TaskStatus>> = {
  queued: new Set([
    "queued",
    "running",
    "cancelling",
    "cancelled",
    "completed",
    "review_required",
    "failed",
    "expired",
  ]),
  running: new Set([
    "running",
    "cancelling",
    "cancelled",
    "completed",
    "review_required",
    "failed",
    "expired",
  ]),
  cancelling: new Set([
    "cancelling",
    "cancelled",
    "completed",
    "review_required",
    "failed",
    "expired",
  ]),
  cancelled: new Set(["cancelled", "expired"]),
  completed: new Set(["completed", "expired"]),
  review_required: new Set(["review_required", "expired"]),
  failed: new Set(["failed", "expired"]),
  expired: new Set(["expired"]),
};

export class HistoryValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "HistoryValidationError";
  }
}

function validateJsonValue(
  value: unknown,
  ancestors: Set<object>,
  path: string,
): asserts value is JsonValue {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean"
  ) {
    return;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new HistoryValidationError(`${path} must contain finite numbers.`);
    }
    return;
  }
  if (typeof value !== "object") {
    throw new HistoryValidationError(`${path} must contain JSON values only.`);
  }
  if (
    value instanceof Blob ||
    value instanceof ArrayBuffer ||
    ArrayBuffer.isView(value)
  ) {
    throw new HistoryValidationError(
      `${path} must not contain binary or typed-array values.`,
    );
  }
  if (Object.getPrototypeOf(value) !== Object.prototype && !Array.isArray(value)) {
    throw new HistoryValidationError(`${path} must contain plain JSON objects.`);
  }
  if (ancestors.has(value)) {
    throw new HistoryValidationError(`${path} must not contain cycles.`);
  }

  ancestors.add(value);
  if (Array.isArray(value)) {
    value.forEach((item, index) =>
      validateJsonValue(item, ancestors, `${path}[${index}]`),
    );
  } else {
    for (const [key, item] of Object.entries(value)) {
      validateJsonValue(item, ancestors, `${path}.${key}`);
    }
  }
  ancestors.delete(value);
}

function requireString(value: unknown, field: string): asserts value is string {
  if (typeof value !== "string" || value.length === 0) {
    throw new HistoryValidationError(`${field} must be a non-empty string.`);
  }
}

function validateEntry(entry: TaskHistorySummary): void {
  requireString(entry.taskId, "taskId");
  requireString(entry.fileName, "fileName");
  requireString(entry.createdAt, "createdAt");
  requireString(entry.updatedAt, "updatedAt");
  if (entry.fileName.length > MAX_FILENAME_LENGTH) {
    throw new HistoryValidationError(
      `fileName must be at most ${MAX_FILENAME_LENGTH} characters.`,
    );
  }
  if (!TASK_TYPES.has(entry.type)) {
    throw new HistoryValidationError("type is invalid.");
  }
  if (!TASK_STATUSES.has(entry.lastStatus)) {
    throw new HistoryValidationError("lastStatus is invalid.");
  }
  if (entry.style !== null && !PROCESSING_STYLES.has(entry.style)) {
    throw new HistoryValidationError("style is invalid.");
  }
  if (entry.expiresAt !== null) {
    requireString(entry.expiresAt, "expiresAt");
  }
  if (typeof entry.resultAvailable !== "boolean") {
    throw new HistoryValidationError("resultAvailable must be a boolean.");
  }

  validateJsonValue(entry.summary, new Set(), "summary");
  const serialized = JSON.stringify(entry.summary);
  if (new TextEncoder().encode(serialized).byteLength > MAX_SUMMARY_BYTES) {
    throw new HistoryValidationError(
      `summary must be at most ${MAX_SUMMARY_BYTES} bytes.`,
    );
  }
}

function validateUpsertFields(input: TaskHistoryUpsert): void {
  for (const field of Reflect.ownKeys(input)) {
    if (typeof field !== "string" || !UPSERT_FIELDS.has(field)) {
      throw new HistoryValidationError(`Unknown history field: ${String(field)}.`);
    }
  }
}

function canTransition(from: TaskStatus, to: TaskStatus): boolean {
  return VALID_STATUS_TRANSITIONS[from].has(to);
}

function isTerminalStatus(status: TaskStatus): boolean {
  return [
    "cancelled",
    "completed",
    "review_required",
    "failed",
    "expired",
  ].includes(status);
}

function summaryRichness(summary: JsonValue | null): number {
  return summary === null ? -1 : JSON.stringify(summary).length;
}

function resolveSameStatusWrite(
  current: TaskHistorySummary,
  candidate: TaskHistorySummary,
): TaskHistorySummary {
  const timestampOrder = candidate.updatedAt.localeCompare(current.updatedAt);
  if (timestampOrder < 0) {
    return current;
  }
  if (timestampOrder > 0 || !isTerminalStatus(current.lastStatus)) {
    return candidate;
  }

  return {
    ...candidate,
    resultAvailable: current.resultAvailable || candidate.resultAvailable,
    summary:
      summaryRichness(current.summary) > summaryRichness(candidate.summary)
        ? current.summary
        : candidate.summary,
  };
}

function resolveWrite(
  current: TaskHistorySummary,
  candidate: TaskHistorySummary,
): TaskHistorySummary {
  if (!canTransition(current.lastStatus, candidate.lastStatus)) {
    return current;
  }
  if (current.lastStatus === candidate.lastStatus) {
    return resolveSameStatusWrite(current, candidate);
  }
  return candidate;
}

function cloneEntry(entry: TaskHistorySummary): TaskHistorySummary {
  return structuredClone(entry);
}

export interface UpdateStatusOptions {
  resultAvailable?: boolean;
  summary?: JsonValue | null;
  expiresAt?: string | null;
  updatedAt?: string;
}

export class TaskHistoryRepository {
  constructor(private readonly indexedDb?: IDBFactory) {}

  async upsert(input: TaskHistoryUpsert): Promise<void> {
    validateUpsertFields(input);
    const entry: TaskHistorySummary = {
      taskId: input.taskId,
      type: input.type,
      fileName: input.fileName,
      style: input.style ?? null,
      lastStatus: input.lastStatus,
      createdAt: input.createdAt,
      expiresAt: input.expiresAt,
      summary: input.summary ?? null,
      resultAvailable: input.resultAvailable,
      updatedAt: input.updatedAt ?? input.createdAt,
    };
    validateEntry(entry);
    const db = await openStarunDatabase(this.indexedDb);
    const transaction = db.transaction(TASK_HISTORY_STORE, "readwrite");
    const completion = transactionDone(transaction);
    const store = transaction.objectStore(TASK_HISTORY_STORE);
    const current = await requestResult<TaskHistorySummary | undefined>(
      store.get(entry.taskId),
    );
    const resolved = current ? resolveWrite(current, entry) : entry;
    if (!current || resolved !== current) {
      await requestResult(store.put(cloneEntry(resolved)));
    }
    await completion;
  }

  async get(taskId: string): Promise<TaskHistorySummary | null> {
    const db = await openStarunDatabase(this.indexedDb);
    const transaction = db.transaction(TASK_HISTORY_STORE, "readonly");
    const completion = transactionDone(transaction);
    const result = await requestResult<TaskHistorySummary | undefined>(
      transaction.objectStore(TASK_HISTORY_STORE).get(taskId),
    );
    await completion;
    return result ? cloneEntry(result) : null;
  }

  async list(): Promise<TaskHistorySummary[]> {
    const db = await openStarunDatabase(this.indexedDb);
    const transaction = db.transaction(TASK_HISTORY_STORE, "readonly");
    const completion = transactionDone(transaction);
    const entries = await requestResult<TaskHistorySummary[]>(
      transaction.objectStore(TASK_HISTORY_STORE).getAll(),
    );
    await completion;
    return entries
      .map(cloneEntry)
      .sort((left, right) => right.createdAt.localeCompare(left.createdAt));
  }

  async remove(taskId: string): Promise<void> {
    const db = await openStarunDatabase(this.indexedDb);
    const transaction = db.transaction(TASK_HISTORY_STORE, "readwrite");
    const completion = transactionDone(transaction);
    await requestResult(transaction.objectStore(TASK_HISTORY_STORE).delete(taskId));
    await completion;
  }

  async clear(): Promise<void> {
    const db = await openStarunDatabase(this.indexedDb);
    const transaction = db.transaction(TASK_HISTORY_STORE, "readwrite");
    const completion = transactionDone(transaction);
    await requestResult(transaction.objectStore(TASK_HISTORY_STORE).clear());
    await completion;
  }

  async updateStatus(
    taskId: string,
    status: TaskStatus,
    options: UpdateStatusOptions = {},
  ): Promise<TaskHistorySummary | null> {
    if (!TASK_STATUSES.has(status)) {
      throw new HistoryValidationError("status is invalid.");
    }
    const db = await openStarunDatabase(this.indexedDb);
    const transaction = db.transaction(TASK_HISTORY_STORE, "readwrite");
    const completion = transactionDone(transaction);
    const store = transaction.objectStore(TASK_HISTORY_STORE);
    const current = await requestResult<TaskHistorySummary | undefined>(
      store.get(taskId),
    );
    if (!current) {
      await completion;
      return null;
    }
    const updated: TaskHistorySummary = {
      ...current,
      lastStatus: status,
      resultAvailable: options.resultAvailable ?? current.resultAvailable,
      summary: options.summary === undefined ? current.summary : options.summary,
      expiresAt:
        options.expiresAt === undefined ? current.expiresAt : options.expiresAt,
      updatedAt: options.updatedAt ?? new Date().toISOString(),
    };
    validateEntry(updated);
    const resolved = resolveWrite(current, updated);
    if (resolved !== current) {
      await requestResult(store.put(cloneEntry(resolved)));
    }
    await completion;
    return cloneEntry(resolved);
  }

  markExpired(
    taskId: string,
    updatedAt = new Date().toISOString(),
  ): Promise<TaskHistorySummary | null> {
    return this.updateStatus(taskId, "expired", {
      resultAvailable: false,
      updatedAt,
    });
  }
}

export function createHistoryRepository(
  indexedDb: IDBFactory,
): TaskHistoryRepository {
  return new TaskHistoryRepository(indexedDb);
}
