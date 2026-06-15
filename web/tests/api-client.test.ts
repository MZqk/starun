import { afterEach, describe, expect, it, vi } from "vitest";

import {
  resolveApiBaseUrl,
  StarunApiClient,
  normalizeApiBaseUrl,
} from "../src/lib/api/client";
import { StarunApiError } from "../src/lib/api/errors";
import type {
  ProcessingTaskCreate,
  TaskDetailResponse,
  TaskResponse,
} from "../src/lib/api/types";

const processingFromUpload = {
  upload_id: "upload-1",
} satisfies ProcessingTaskCreate;
const processingFromTask = {
  source_task_id: "task-1",
  style: "artistic",
} satisfies ProcessingTaskCreate;
// @ts-expect-error Exactly one processing source is required.
const processingWithoutSource: ProcessingTaskCreate = {};
// @ts-expect-error Two non-null processing sources are forbidden.
const processingWithTwoSources: ProcessingTaskCreate = {
  upload_id: "upload-1",
  source_task_id: "task-1",
};
void [
  processingFromUpload,
  processingFromTask,
  processingWithoutSource,
  processingWithTwoSources,
];

const taskResponse: TaskResponse = {
  task_id: "task-1",
  type: "analysis",
  status: "queued",
  quota_charged: true,
  created_at: "2026-06-13T00:00:00Z",
  expires_at: null,
  style: null,
};

const taskDetail: TaskDetailResponse = {
  id: "task-1",
  type: "analysis",
  status: "running",
  stage: "inspect",
  progress: 25,
  style: null,
  created_at: "2026-06-13T00:00:00Z",
  started_at: null,
  finished_at: null,
  expires_at: null,
  error_code: null,
  message: null,
  retryable: false,
  quota_charged: true,
  cleanup_pending: false,
  result: {
    manifest_available: false,
    summary: null,
    artifacts: [],
  },
  selected_hdu: null,
  inspection: null,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("StarunApiClient", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("normalizes the configured base URL", () => {
    expect(normalizeApiBaseUrl("https://api.example.test///")).toBe(
      "https://api.example.test",
    );
  });

  it.each([
    ["path", "https://api.example.test/v1"],
    ["query", "https://api.example.test?tenant=one"],
    ["fragment", "https://api.example.test#api"],
    ["userinfo", "https://user:secret@api.example.test"],
  ])("rejects a base URL containing %s data", (_label, value) => {
    expect(() => normalizeApiBaseUrl(value)).toThrow(/origin only/);
  });

  it("fails clearly when production configuration is missing", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "");

    expect(() => resolveApiBaseUrl()).toThrow(/NEXT_PUBLIC_API_BASE_URL/);
  });

  it("uses localhost in browser development when configuration is missing", () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "");

    expect(resolveApiBaseUrl()).toBe("http://localhost:8000");
  });

  it("calls the default global fetch with the global receiver", async () => {
    const receiverSensitiveFetch = vi.fn(function (
      this: typeof globalThis,
    ): Promise<Response> {
      if (this !== globalThis) {
        throw new TypeError("Illegal invocation");
      }
      return Promise.resolve(
        jsonResponse({ date: "2026-06-13", limit: 5, used: 1, remaining: 4 }),
      );
    });
    vi.stubGlobal("fetch", receiverSensitiveFetch);
    const client = new StarunApiClient({
      baseUrl: "https://api.example.test/",
      getClientId: async () => "client-123",
    });

    await expect(client.getUsage()).resolves.toMatchObject({ remaining: 4 });
    expect(receiverSensitiveFetch).toHaveBeenCalledOnce();
  });

  it("sends the client ID and JSON negotiation on every JSON endpoint", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({
          upload_id: "upload-1",
          status: "ready",
          expires_at: "2026-06-14T00:00:00Z",
          inspection: {
            hdus: [],
            selected_hdu: {
              index: 0,
              name: "PRIMARY",
              kind: "image",
              shape: [10, 10],
              dtype: "float32",
              supported: true,
            },
            statistics: {
              minimum: 0,
              maximum: 1,
              mean: 0.5,
              median: 0.5,
              standard_deviation: 0.1,
              finite_pixel_count: 100,
            },
            header: {},
          },
        }),
      )
      .mockResolvedValueOnce(jsonResponse(taskResponse, 201))
      .mockResolvedValueOnce(
        jsonResponse({ ...taskResponse, type: "processing", style: "balanced" }, 201),
      )
      .mockResolvedValueOnce(jsonResponse(taskDetail))
      .mockResolvedValueOnce(
        jsonResponse({ events: [], next_after: 0, has_more: false }),
      )
      .mockResolvedValueOnce(jsonResponse(taskDetail))
      .mockResolvedValueOnce(jsonResponse(taskResponse, 201))
      .mockResolvedValueOnce(jsonResponse(taskDetail))
      .mockResolvedValueOnce(
        jsonResponse({ date: "2026-06-13", limit: 5, used: 1, remaining: 4 }),
      );
    const client = new StarunApiClient({
      baseUrl: "https://api.example.test/",
      fetch: fetchMock,
      getClientId: async () => "client-123",
    });

    await client.createUpload(new File(["fits"], "m42.fits"));
    await client.createAnalysisTask({ upload_id: "upload-1" });
    await client.createProcessingTask({
      upload_id: "upload-1",
      style: "balanced",
    });
    await client.getTask("task-1");
    await client.getTaskEvents("task-1", 0);
    await client.cancelTask("task-1");
    await client.retryTask("task-1");
    await client.deleteTask("task-1");
    await client.getUsage();

    expect(fetchMock).toHaveBeenCalledTimes(9);
    for (const [, init] of fetchMock.mock.calls) {
      const headers = new Headers(init?.headers);
      expect(headers.get("X-Starun-Client-Id")).toBe("client-123");
      expect(headers.get("Accept")).toBe("application/json");
    }
  });

  it("passes AbortSignal through unchanged", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(taskDetail));
    const client = new StarunApiClient({
      baseUrl: "http://localhost:8000",
      fetch: fetchMock,
      getClientId: async () => "client-123",
    });
    const controller = new AbortController();

    await client.getTask("task-1", { signal: controller.signal });

    expect(fetchMock.mock.calls[0][1]?.signal).toBe(controller.signal);
  });

  it("parses structured API errors", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          error_code: "daily_limit_exceeded",
          message: "Daily limit reached.",
          retryable: false,
          quota_charged: false,
          diagnostic_id: "diag-1",
        },
        429,
      ),
    );
    const client = new StarunApiClient({
      baseUrl: "http://localhost:8000",
      fetch: fetchMock,
      getClientId: async () => "client-123",
    });

    const error = await client.getUsage().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(StarunApiError);
    expect(error).toMatchObject({
      errorCode: "daily_limit_exceeded",
      message: "Daily limit reached.",
      retryable: false,
      quotaCharged: false,
      diagnosticId: "diag-1",
      status: 429,
    });
  });

  it("rejects malformed successful responses", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ status: "queued" }));
    const client = new StarunApiClient({
      baseUrl: "http://localhost:8000",
      fetch: fetchMock,
      getClientId: async () => "client-123",
    });

    await expect(client.getTask("task-1")).rejects.toMatchObject({
      errorCode: "client_invalid_response",
      status: 200,
    });
  });

  it("keeps authentication out of artifact URLs and returns download metadata", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response("artifact", {
        headers: {
          "content-type": "application/json",
          "content-disposition": 'attachment; filename="result.json"',
          "content-length": "8",
        },
      }),
    );
    const client = new StarunApiClient({
      baseUrl: "https://api.example.test/",
      fetch: fetchMock,
      getClientId: async () => "secret-client-id",
    });

    const url = client.getArtifactUrl("task/1", "result.json");
    const request = await client.getArtifactRequest("task/1", "result.json");
    const download = await client.downloadArtifact("task/1", "result.json");

    expect(url).toBe(
      "https://api.example.test/api/tasks/task%2F1/artifacts/result.json",
    );
    expect(url).not.toContain("secret-client-id");
    expect(new URL(url).search).toBe("");
    expect(new Headers(request.headers).get("X-Starun-Client-Id")).toBe(
      "secret-client-id",
    );
    expect(download).toMatchObject({
      fileName: "result.json",
      mediaType: "application/json",
      size: 8,
    });
  });
});
