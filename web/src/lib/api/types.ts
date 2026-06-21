export type TaskStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "cancelled"
  | "completed"
  | "review_required"
  | "failed"
  | "expired";

export type TaskType = "analysis" | "processing";
export type UploadStatus = "uploading" | "validating" | "ready" | "invalid";
export type ProcessingStyle = "realistic" | "balanced" | "artistic";
export type EventLevel = "debug" | "info" | "warning" | "error";
export type ArtifactMediaType =
  | "application/json"
  | "image/jpeg"
  | "image/png"
  | "image/tiff";

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue =
  | JsonPrimitive
  | { [key: string]: JsonValue }
  | JsonValue[];
export type JsonObject = { [key: string]: JsonValue };

export interface HduSummary {
  index: number;
  name: string;
  kind: string;
  shape: number[] | null;
  dtype: string | null;
  supported: boolean;
}

export interface BasicStatistics {
  minimum: number;
  maximum: number;
  mean: number;
  median: number;
  standard_deviation: number;
  finite_pixel_count: number;
}

export interface FitsInspection {
  hdus: HduSummary[];
  selected_hdu: HduSummary;
  statistics: BasicStatistics;
  header: Record<string, string | number | boolean>;
}

export interface UploadResponse {
  upload_id: string;
  status: UploadStatus;
  expires_at: string;
  inspection: FitsInspection;
}

export interface ApiErrorResponse {
  error_code: string;
  message: string;
  retryable: boolean;
  quota_charged: boolean;
  diagnostic_id?: string | null;
}

export type UploadErrorResponse = ApiErrorResponse;
export type TaskErrorResponse = ApiErrorResponse;

export interface AnalysisTaskCreate {
  upload_id: string;
}

export type ProcessingTaskCreate =
  | {
      upload_id: string;
      source_task_id?: null;
      style?: ProcessingStyle;
    }
  | {
      upload_id?: null;
      source_task_id: string;
      style?: ProcessingStyle;
    };

export interface TaskResponse {
  task_id: string;
  type: TaskType;
  status: TaskStatus;
  quota_charged: boolean;
  created_at: string;
  expires_at: string | null;
  style: ProcessingStyle | null;
}

export interface ArtifactManifestEntry {
  name: string;
  media_type: ArtifactMediaType;
  size: number;
  sha256: string;
  demo: boolean;
}

export interface TaskResultResponse {
  manifest_available: boolean;
  summary: JsonObject | null;
  artifacts: string[];
}

export interface TaskDetailResponse {
  id: string;
  type: TaskType;
  status: TaskStatus;
  stage: string | null;
  progress: number;
  style: ProcessingStyle | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  expires_at: string | null;
  error_code: string | null;
  message: string | null;
  retryable: boolean;
  quota_charged: boolean;
  cleanup_pending: boolean;
  result: TaskResultResponse;
  selected_hdu: number | null;
  inspection: JsonObject | null;
}

export interface TaskEventResponse {
  sequence: number;
  level: EventLevel;
  event_type: string;
  payload: JsonObject;
  created_at: string;
}

export interface TaskEventsResponse {
  events: TaskEventResponse[];
  next_after: number;
  has_more: boolean;
}

export interface UsageResponse {
  date: string;
  limit: number;
  used: number;
  remaining: number;
}

export interface ArtifactDownload {
  blob: Blob;
  fileName: string;
  mediaType: string;
  size: number;
}

export interface RequestOptions {
  signal?: AbortSignal;
}
