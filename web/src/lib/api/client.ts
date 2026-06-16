import { getOrCreateClientId } from "../client-id";
import { StarunApiError } from "./errors";
import type {
  AnalysisTaskCreate,
  ApiErrorResponse,
  ArtifactDownload,
  BasicStatistics,
  FitsInspection,
  HduSummary,
  JsonObject,
  JsonValue,
  ProcessingStyle,
  ProcessingTaskCreate,
  RequestOptions,
  TaskDetailResponse,
  TaskEventsResponse,
  TaskResponse,
  TaskStatus,
  TaskType,
  UploadResponse,
  UploadStatus,
  UsageResponse,
} from "./types";

const TASK_STATUSES = new Set<TaskStatus>([
  "queued",
  "running",
  "cancelling",
  "cancelled",
  "completed",
  "failed",
  "expired",
]);
const TASK_TYPES = new Set<TaskType>(["analysis", "processing"]);
const UPLOAD_STATUSES = new Set<UploadStatus>([
  "uploading",
  "validating",
  "ready",
  "invalid",
]);
const PROCESSING_STYLES = new Set<ProcessingStyle>([
  "realistic",
  "balanced",
  "artistic",
]);

type ResponseGuard<T> = (value: unknown) => value is T;

export interface StarunApiClientOptions {
  baseUrl?: string;
  fetch?: typeof fetch;
  getClientId?: () => Promise<string>;
}

export interface PreparedUploadRequest {
  url: string;
  headers: Headers;
  body: FormData;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isJsonValue(value: unknown): value is JsonValue {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean"
  ) {
    return true;
  }
  if (typeof value === "number") {
    return Number.isFinite(value);
  }
  if (Array.isArray(value)) {
    return value.every(isJsonValue);
  }
  return isRecord(value) && Object.values(value).every(isJsonValue);
}

function isJsonObject(value: unknown): value is JsonObject {
  return isRecord(value) && Object.values(value).every(isJsonValue);
}

function isHduSummary(value: unknown): value is HduSummary {
  return (
    isRecord(value) &&
    Number.isInteger(value.index) &&
    typeof value.name === "string" &&
    typeof value.kind === "string" &&
    (value.shape === null ||
      (Array.isArray(value.shape) &&
        value.shape.every((item) => Number.isInteger(item)))) &&
    isNullableString(value.dtype) &&
    typeof value.supported === "boolean"
  );
}

function isBasicStatistics(value: unknown): value is BasicStatistics {
  return (
    isRecord(value) &&
    typeof value.minimum === "number" &&
    typeof value.maximum === "number" &&
    typeof value.mean === "number" &&
    typeof value.median === "number" &&
    typeof value.standard_deviation === "number" &&
    Number.isInteger(value.finite_pixel_count)
  );
}

function isFitsInspection(value: unknown): value is FitsInspection {
  return (
    isRecord(value) &&
    Array.isArray(value.hdus) &&
    value.hdus.every(isHduSummary) &&
    isHduSummary(value.selected_hdu) &&
    isBasicStatistics(value.statistics) &&
    isRecord(value.header) &&
    Object.values(value.header).every(
      (item) =>
        typeof item === "string" ||
        typeof item === "number" ||
        typeof item === "boolean",
    )
  );
}

function isUploadResponse(value: unknown): value is UploadResponse {
  return (
    isRecord(value) &&
    typeof value.upload_id === "string" &&
    typeof value.status === "string" &&
    UPLOAD_STATUSES.has(value.status as UploadStatus) &&
    typeof value.expires_at === "string" &&
    isFitsInspection(value.inspection)
  );
}

export function parseUploadResponse(
  value: unknown,
  status = 200,
): UploadResponse {
  if (isUploadResponse(value)) {
    return value;
  }
  throw new StarunApiError(
    "client_invalid_response",
    "The API returned an invalid upload response.",
    false,
    false,
    null,
    status,
  );
}

export function parseApiError(
  value: unknown,
  status: number,
): StarunApiError {
  if (isApiErrorResponse(value)) {
    return new StarunApiError(
      value.error_code,
      value.message,
      value.retryable,
      value.quota_charged,
      value.diagnostic_id ?? null,
      status,
    );
  }
  return new StarunApiError(
    "client_invalid_response",
    "The API returned an unstructured error response.",
    false,
    false,
    null,
    status,
  );
}

function isTaskResponse(value: unknown): value is TaskResponse {
  return (
    isRecord(value) &&
    typeof value.task_id === "string" &&
    typeof value.type === "string" &&
    TASK_TYPES.has(value.type as TaskType) &&
    typeof value.status === "string" &&
    TASK_STATUSES.has(value.status as TaskStatus) &&
    typeof value.quota_charged === "boolean" &&
    typeof value.created_at === "string" &&
    isNullableString(value.expires_at) &&
    (value.style === null ||
      (typeof value.style === "string" &&
        PROCESSING_STYLES.has(value.style as ProcessingStyle)))
  );
}

function isTaskDetailResponse(value: unknown): value is TaskDetailResponse {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    typeof value.type !== "string" ||
    !TASK_TYPES.has(value.type as TaskType) ||
    typeof value.status !== "string" ||
    !TASK_STATUSES.has(value.status as TaskStatus) ||
    !isNullableString(value.stage) ||
    !Number.isInteger(value.progress) ||
    (value.style !== null &&
      (typeof value.style !== "string" ||
        !PROCESSING_STYLES.has(value.style as ProcessingStyle))) ||
    typeof value.created_at !== "string" ||
    !isNullableString(value.started_at) ||
    !isNullableString(value.finished_at) ||
    !isNullableString(value.expires_at) ||
    !isNullableString(value.error_code) ||
    !isNullableString(value.message) ||
    typeof value.retryable !== "boolean" ||
    typeof value.quota_charged !== "boolean" ||
    typeof value.cleanup_pending !== "boolean" ||
    (value.selected_hdu !== null && !Number.isInteger(value.selected_hdu)) ||
    (value.inspection !== null && !isJsonObject(value.inspection)) ||
    !isRecord(value.result)
  ) {
    return false;
  }
  return (
    typeof value.result.manifest_available === "boolean" &&
    (value.result.summary === null || isJsonObject(value.result.summary)) &&
    Array.isArray(value.result.artifacts) &&
    value.result.artifacts.every((item) => typeof item === "string")
  );
}

function isTaskEventsResponse(value: unknown): value is TaskEventsResponse {
  return (
    isRecord(value) &&
    Array.isArray(value.events) &&
    value.events.every(
      (event) =>
        isRecord(event) &&
        Number.isInteger(event.sequence) &&
        typeof event.level === "string" &&
        ["debug", "info", "warning", "error"].includes(event.level) &&
        typeof event.event_type === "string" &&
        isJsonObject(event.payload) &&
        typeof event.created_at === "string",
    ) &&
    Number.isInteger(value.next_after) &&
    typeof value.has_more === "boolean"
  );
}

function isUsageResponse(value: unknown): value is UsageResponse {
  return (
    isRecord(value) &&
    typeof value.date === "string" &&
    Number.isInteger(value.limit) &&
    Number.isInteger(value.used) &&
    Number.isInteger(value.remaining)
  );
}

function isApiErrorResponse(value: unknown): value is ApiErrorResponse {
  return (
    isRecord(value) &&
    typeof value.error_code === "string" &&
    typeof value.message === "string" &&
    typeof value.retryable === "boolean" &&
    typeof value.quota_charged === "boolean" &&
    (value.diagnostic_id === undefined ||
      value.diagnostic_id === null ||
      typeof value.diagnostic_id === "string")
  );
}

function parseContentDispositionFilename(value: string | null): string | null {
  if (!value) {
    return null;
  }
  const utf8Match = value.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return utf8Match[1];
    }
  }
  const quotedMatch = value.match(/filename="([^"]+)"/i);
  if (quotedMatch) {
    return quotedMatch[1];
  }
  const plainMatch = value.match(/filename=([^;]+)/i);
  return plainMatch?.[1].trim() ?? null;
}

export function normalizeApiBaseUrl(value: string): string {
  const normalized = value.trim();
  if (!normalized) {
    throw new Error("The Starun API base URL is empty.");
  }
  let url: URL;
  try {
    url = new URL(normalized);
  } catch {
    throw new Error("The Starun API base URL is invalid.");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("The Starun API base URL must use HTTP or HTTPS.");
  }
  if (
    url.username ||
    url.password ||
    !/^\/+$/.test(url.pathname) ||
    url.search ||
    url.hash
  ) {
    throw new Error("The Starun API base URL must be an origin only.");
  }
  return url.origin;
}

export function resolveApiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (configured) {
    return normalizeApiBaseUrl(configured);
  }
  if (
    process.env.NODE_ENV === "development" &&
    typeof window !== "undefined"
  ) {
    return "http://127.0.0.1:8000";
  }
  if (typeof window !== "undefined" && !process.env.VITEST) {
    return window.location.origin;
  }
  if (typeof window !== "undefined" && !process.env.VITEST) {
    return window.location.origin;
  }
  throw new Error(
    "NEXT_PUBLIC_API_BASE_URL is required outside browser development.",
  );
}

export class StarunApiClient {
  readonly baseUrl: string;
  private readonly fetchImplementation: typeof fetch;
  private readonly clientIdProvider: () => Promise<string>;

  constructor(options: StarunApiClientOptions = {}) {
    this.baseUrl = normalizeApiBaseUrl(options.baseUrl ?? resolveApiBaseUrl());
    const fetchImplementation =
      options.fetch ?? globalThis.fetch?.bind(globalThis);
    if (!fetchImplementation) {
      throw new Error("Fetch is unavailable in this environment.");
    }
    this.fetchImplementation = fetchImplementation;
    this.clientIdProvider = options.getClientId ?? getOrCreateClientId;
  }

  private url(path: string): string {
    return new URL(path, `${this.baseUrl}/`).toString();
  }

  private async authenticatedHeaders(accept = "application/json"): Promise<Headers> {
    const headers = new Headers({ Accept: accept });
    headers.set("X-Starun-Client-Id", await this.clientIdProvider());
    return headers;
  }

  private async requestJson<T>(
    path: string,
    guard: ResponseGuard<T>,
    options: RequestInit = {},
  ): Promise<T> {
    const headers = await this.authenticatedHeaders();
    new Headers(options.headers).forEach((value, key) => headers.set(key, value));
    const response = await this.fetchImplementation(this.url(path), {
      ...options,
      headers,
    });
    const body = await this.readJson(response);
    if (!response.ok) {
      throw this.apiError(response.status, body);
    }
    if (!guard(body)) {
      throw new StarunApiError(
        "client_invalid_response",
        "The API returned an invalid response.",
        false,
        false,
        null,
        response.status,
      );
    }
    return body;
  }

  private async readJson(response: Response): Promise<unknown> {
    try {
      return await response.json();
    } catch {
      if (!response.ok) {
        return null;
      }
      throw new StarunApiError(
        "client_invalid_response",
        "The API returned invalid JSON.",
        false,
        false,
        null,
        response.status,
      );
    }
  }

  private apiError(status: number, body: unknown): StarunApiError {
    return parseApiError(body, status);
  }

  async buildUploadRequest(file: File): Promise<PreparedUploadRequest> {
    const body = new FormData();
    body.append("file", file, file.name);
    return {
      url: this.url("/api/uploads"),
      headers: await this.authenticatedHeaders(),
      body,
    };
  }

  async createUpload(
    file: File,
    options: RequestOptions = {},
  ): Promise<UploadResponse> {
    const request = await this.buildUploadRequest(file);
    const response = await this.fetchImplementation(request.url, {
      method: "POST",
      headers: request.headers,
      body: request.body,
      signal: options.signal,
    });
    const body = await this.readJson(response);
    if (!response.ok) {
      throw this.apiError(response.status, body);
    }
    return parseUploadResponse(body, response.status);
  }

  createAnalysisTask(
    payload: AnalysisTaskCreate,
    options: RequestOptions = {},
  ): Promise<TaskResponse> {
    return this.requestJson("/api/tasks/analysis", isTaskResponse, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: options.signal,
    });
  }

  createProcessingTask(
    payload: ProcessingTaskCreate,
    options: RequestOptions = {},
  ): Promise<TaskResponse> {
    return this.requestJson("/api/tasks/process", isTaskResponse, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: options.signal,
    });
  }

  getTask(
    taskId: string,
    options: RequestOptions = {},
  ): Promise<TaskDetailResponse> {
    return this.requestJson(
      `/api/tasks/${encodeURIComponent(taskId)}`,
      isTaskDetailResponse,
      { signal: options.signal },
    );
  }

  getTaskEvents(
    taskId: string,
    after = 0,
    options: RequestOptions = {},
  ): Promise<TaskEventsResponse> {
    const query = new URLSearchParams({ after: String(after) });
    return this.requestJson(
      `/api/tasks/${encodeURIComponent(taskId)}/events?${query}`,
      isTaskEventsResponse,
      { signal: options.signal },
    );
  }

  cancelTask(
    taskId: string,
    options: RequestOptions = {},
  ): Promise<TaskDetailResponse> {
    return this.requestJson(
      `/api/tasks/${encodeURIComponent(taskId)}/cancel`,
      isTaskDetailResponse,
      { method: "POST", signal: options.signal },
    );
  }

  retryTask(
    taskId: string,
    options: RequestOptions = {},
  ): Promise<TaskResponse> {
    return this.requestJson(
      `/api/tasks/${encodeURIComponent(taskId)}/retry`,
      isTaskResponse,
      { method: "POST", signal: options.signal },
    );
  }

  deleteTask(
    taskId: string,
    options: RequestOptions = {},
  ): Promise<TaskDetailResponse> {
    return this.requestJson(
      `/api/tasks/${encodeURIComponent(taskId)}`,
      isTaskDetailResponse,
      { method: "DELETE", signal: options.signal },
    );
  }

  getUsage(options: RequestOptions = {}): Promise<UsageResponse> {
    return this.requestJson("/api/usage", isUsageResponse, {
      signal: options.signal,
    });
  }

  getArtifactUrl(taskId: string, name: string): string {
    return this.url(
      `/api/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(name)}`,
    );
  }

  async getArtifactRequest(
    taskId: string,
    name: string,
    options: RequestOptions = {},
  ): Promise<Request> {
    return new Request(this.getArtifactUrl(taskId, name), {
      headers: await this.authenticatedHeaders("*/*"),
      signal: options.signal,
    });
  }

  async downloadArtifact(
    taskId: string,
    name: string,
    options: RequestOptions = {},
  ): Promise<ArtifactDownload> {
    const request = await this.getArtifactRequest(taskId, name, options);
    const response = await this.fetchImplementation(request);
    if (!response.ok) {
      const body = await this.readJson(response);
      throw this.apiError(response.status, body);
    }
    const blob = await response.blob();
    const sizeHeader = response.headers.get("content-length");
    const parsedSize = sizeHeader === null ? Number.NaN : Number(sizeHeader);
    return {
      blob,
      fileName:
        parseContentDispositionFilename(
          response.headers.get("content-disposition"),
        ) ?? name,
      mediaType: response.headers.get("content-type") ?? blob.type,
      size: Number.isFinite(parsedSize) ? parsedSize : blob.size,
    };
  }
}

let defaultClient: StarunApiClient | null = null;

export function getApiClient(): StarunApiClient {
  defaultClient ??= new StarunApiClient();
  return defaultClient;
}
