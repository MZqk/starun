import type {
  ProcessingStyle,
  TaskStatus,
  TaskType,
} from "../api/types";

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue =
  | JsonPrimitive
  | { [key: string]: JsonValue }
  | JsonValue[];

export interface TaskHistorySummary {
  taskId: string;
  type: TaskType;
  fileName: string;
  style: ProcessingStyle | null;
  lastStatus: TaskStatus;
  createdAt: string;
  expiresAt: string | null;
  summary: JsonValue | null;
  resultAvailable: boolean;
  updatedAt: string;
}

export type TaskHistoryUpsert = Omit<
  TaskHistorySummary,
  "style" | "summary" | "updatedAt"
> & {
  style?: ProcessingStyle | null;
  summary?: JsonValue | null;
  updatedAt?: string;
};

export interface ClientSetting {
  key: string;
  value: string;
}
