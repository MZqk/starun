import "fake-indexeddb/auto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { IDBFactory } from "fake-indexeddb";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AnalysisPage from "../src/app/analysis/page";
import HistoryPage from "../src/app/history/page";
import ProcessingPage from "../src/app/processing/page";
import TaskStatusPanel from "../src/components/TaskStatusPanel";
import { useTaskPolling } from "../src/hooks/useTaskPolling";
import { zhCN } from "../src/lib/i18n/zh-CN";
import type {
  TaskDetailResponse,
  TaskEventsResponse,
} from "../src/lib/api/types";
import { closeStarunDatabase } from "../src/lib/history/db";
import { TaskHistoryRepository } from "../src/lib/history/repository";

const api = vi.hoisted(() => ({
  buildUploadRequest: vi.fn(),
  cancelTask: vi.fn(),
  createAnalysisTask: vi.fn(),
  createProcessingTask: vi.fn(),
  deleteTask: vi.fn(),
  downloadArtifact: vi.fn(),
  getTask: vi.fn(),
  getTaskEvents: vi.fn(),
  retryTask: vi.fn(),
}));

function futureIso(hours = 24): string {
  return new Date(Date.now() + hours * 60 * 60 * 1000).toISOString();
}

vi.mock("../src/lib/api/client", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("../src/lib/api/client")>();
  return {
    ...original,
    getApiClient: () => api,
  };
});

class SuccessfulUploadXhr {
  static instances: SuccessfulUploadXhr[] = [];

  readonly upload = new EventTarget();
  status = 201;
  responseText = JSON.stringify({
    upload_id: "upload-1",
    status: "ready",
    expires_at: "2026-06-14T12:00:00Z",
    inspection: {
      hdus: [
        {
          index: 0,
          name: "PRIMARY",
          kind: "image",
          shape: [100, 120],
          dtype: "float32",
          supported: true,
        },
        {
          index: 1,
          name: "SCI",
          kind: "image",
          shape: [50, 60],
          dtype: "int16",
          supported: false,
        },
      ],
      selected_hdu: {
        index: 0,
        name: "PRIMARY",
        kind: "image",
        shape: [100, 120],
        dtype: "float32",
        supported: true,
      },
      statistics: {
        minimum: 0,
        maximum: 65535,
        mean: 1120,
        median: 920,
        standard_deviation: 140,
        finite_pixel_count: 12000,
      },
      header: {
        OBJECT: "M42",
        TELESCOP: "RC8",
        EXPTIME: 300,
        CALIBRATED: true,
      },
    },
  });
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;

  constructor() {
    SuccessfulUploadXhr.instances.push(this);
  }

  open() {}

  setRequestHeader() {}

  send() {
    this.upload.dispatchEvent(
      Object.assign(new Event("progress"), {
        lengthComputable: true,
        loaded: 4,
        total: 4,
      }),
    );
    queueMicrotask(() => this.onload?.());
  }

  abort() {
    this.onabort?.();
  }
}

describe("Task 11 flows", () => {
  beforeEach(async () => {
    await closeStarunDatabase();
    vi.stubGlobal("indexedDB", new IDBFactory());
    vi.stubGlobal(
      "XMLHttpRequest",
      SuccessfulUploadXhr as unknown as typeof XMLHttpRequest,
    );
    SuccessfulUploadXhr.instances = [];
    vi.clearAllMocks();
    api.getTask.mockReset();
    api.getTaskEvents.mockReset();
    window.history.replaceState({}, "", "/");

    api.buildUploadRequest.mockResolvedValue({
      url: "http://localhost:8000/api/uploads",
      headers: new Headers({ "X-Starun-Client-Id": "client-1" }),
      body: new FormData(),
    });
    api.getTaskEvents.mockResolvedValue({
      events: [],
      next_after: 0,
      has_more: false,
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("uploads a FITS file, shows validation, and starts queued analysis", async () => {
    const user = userEvent.setup();
    api.createAnalysisTask.mockResolvedValue({
      task_id: "analysis-1",
      type: "analysis",
      status: "queued",
      quota_charged: true,
      created_at: "2026-06-14T04:00:00Z",
      expires_at: "2026-06-15T04:00:00Z",
      style: null,
    });
    api.getTask.mockResolvedValue({
      id: "analysis-1",
      type: "analysis",
      status: "queued",
      stage: null,
      progress: 0,
      style: null,
      created_at: "2026-06-14T04:00:00Z",
      started_at: null,
      finished_at: null,
      expires_at: "2026-06-15T04:00:00Z",
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
      selected_hdu: 0,
      inspection: null,
    });

    render(<AnalysisPage />);
    const input = screen.getByLabelText("选择 FITS/XISF 文件");
    await user.upload(input, new File(["fits"], "m42.fits"));

    expect((await screen.findAllByText("HDU 0")).length).toBeGreaterThan(0);
    expect(screen.getByText(/刷新页面会中断上传/)).toBeVisible();
    expect(screen.getByText(/创建任务前不会扣除额度/)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "开始专业分析" }));

    expect(api.createAnalysisTask).toHaveBeenCalledWith({
      upload_id: "upload-1",
    });
    expect(await screen.findByText("排队中")).toBeVisible();
  });

  it("retains an analysis task when local history persistence fails", async () => {
    const user = userEvent.setup();
    vi.spyOn(TaskHistoryRepository.prototype, "upsert").mockRejectedValue(
      new Error("history unavailable"),
    );
    api.createAnalysisTask.mockResolvedValue(taskCreated("analysis-local-fail"));
    api.getTask.mockResolvedValue(
      taskDetail({ id: "analysis-local-fail", status: "queued" }),
    );

    render(<AnalysisPage />);
    await user.upload(
      screen.getByLabelText(zhCN.task11.upload.inputLabel),
      new File(["fits"], "persist.fits"),
    );
    await user.click(screen.getByRole("button", { name: copyAnalysisCreate() }));

    expect(await screen.findByText(zhCN.task11.status.labels.queued)).toBeVisible();
    expect(screen.getByText(/history unavailable/)).toBeVisible();
    expect(api.createAnalysisTask).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByRole("button", { name: copyAnalysisCreate() }),
    ).not.toBeInTheDocument();
  });

  it("clears the file input so the same FITS can retry after failure", async () => {
    const user = userEvent.setup();
    api.buildUploadRequest
      .mockRejectedValueOnce(new Error("temporary upload failure"))
      .mockResolvedValueOnce({
        url: "http://localhost:8000/api/uploads",
        headers: new Headers({ "X-Starun-Client-Id": "client-1" }),
        body: new FormData(),
      });
    render(<AnalysisPage />);
    const input = screen.getByLabelText(zhCN.task11.upload.inputLabel);
    const file = new File(["fits"], "retry.fits");

    await user.upload(input, file);
    expect(await screen.findByText("temporary upload failure")).toBeVisible();
    await user.upload(input, file);

    expect(api.buildUploadRequest).toHaveBeenCalledTimes(2);
    expect(await screen.findByText(zhCN.task11.upload.ready)).toBeVisible();
  });

  it("renders every server HDU, FITS header field, and basic statistic as real data", async () => {
    const user = userEvent.setup();
    render(<AnalysisPage />);

    await user.upload(
      screen.getByLabelText(zhCN.task11.upload.inputLabel),
      new File(["fits"], "full.fits"),
    );

    const realData = await screen.findByRole("region", {
      name: zhCN.task11.analysis.realDataAriaLabel,
    });
    expect(within(realData).getByText("PRIMARY")).toBeVisible();
    expect(within(realData).getByText("SCI")).toBeVisible();
    expect(within(realData).getByText("不支持")).toBeVisible();
    expect(within(realData).getByText("TELESCOP")).toBeVisible();
    expect(within(realData).getByText("RC8")).toBeVisible();
    expect(within(realData).getByText("EXPTIME")).toBeVisible();
    expect(within(realData).getByText("300")).toBeVisible();
    expect(within(realData).getByText("CALIBRATED")).toBeVisible();
    expect(within(realData).getByText("true")).toBeVisible();
    expect(within(realData).getByText("标准差")).toBeVisible();
    expect(within(realData).getByText("140")).toBeVisible();
    expect(within(realData).getByText("有限像素")).toBeVisible();
    expect(within(realData).getByText("12,000")).toBeVisible();
    expect(
      within(realData).queryByText(zhCN.task11.common.mock),
    ).not.toBeInTheDocument();
  });

  it("shows completed expiry and a live remaining countdown without leaking timers", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-14T10:00:00Z"));
    const view = render(
      <TaskStatusPanel
        task={taskDetail({
          status: "completed",
          expires_at: "2026-06-14T10:00:05Z",
        })}
      />,
    );

    expect(screen.getByText("2026/6/14 18:00:05")).toBeVisible();
    expect(screen.getByText("剩余 5 秒")).toBeVisible();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(screen.getByText("剩余 3 秒")).toBeVisible();

    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps countdown updates out of the live region and uses one stable timer", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-14T10:00:00Z"));
    const view = render(
      <TaskStatusPanel
        task={taskDetail({
          status: "completed",
          expires_at: "2026-06-14T10:00:03Z",
        })}
      />,
    );

    expect(screen.getByText("剩余 3 秒").closest("[aria-live]")).toBeNull();
    expect(
      screen.getByText(zhCN.task11.status.labels.completed).closest("[aria-live]"),
    ).toHaveAttribute("aria-live", "polite");
    expect(vi.getTimerCount()).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(screen.getByText("剩余 2 秒")).toBeVisible();
    expect(vi.getTimerCount()).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(screen.getByText(zhCN.task11.status.expiredNow)).toBeVisible();
    expect(vi.getTimerCount()).toBe(0);
    view.unmount();
  });

  it("shows expired when completed task data first arrives after expiry without starting a timer", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-14T10:00:00Z"));
    const view = render(<TaskStatusPanel task={null} />);

    vi.setSystemTime(new Date("2026-06-14T10:00:10Z"));
    view.rerender(
      <TaskStatusPanel
        task={taskDetail({
          status: "completed",
          expires_at: "2026-06-14T10:00:05Z",
        })}
      />,
    );
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByText(zhCN.task11.status.expiredNow)).toBeVisible();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("shows failed structured error, retryability, and quota charge detail", () => {
    render(
      <TaskStatusPanel
        task={taskDetail({
          status: "failed",
          error_code: "resource_exhausted",
          message: "The task could not acquire the required resources.",
          retryable: true,
          quota_charged: false,
        })}
      />,
    );

    expect(screen.getByText("resource_exhausted")).toBeVisible();
    expect(screen.getByText("可重试")).toBeVisible();
    expect(screen.getByText("未扣除额度")).toBeVisible();
    expect(
      screen.getByText("The task could not acquire the required resources."),
    ).toBeVisible();
  });

  it("centralizes representative Task 11 copy and status labels", () => {
    expect(zhCN.task11.status.labels.queued).toBe("排队中");
    expect(zhCN.task11.analysis.realDataTitle).toBe("FITS / HDU / 基础统计");
    expect(zhCN.task11.processing.styleLegend).toBe("处理风格");

    const taskStatusSource = readFileSync(
      resolve(process.cwd(), "src/components/TaskStatusPanel.tsx"),
      "utf8",
    );
    const analysisSource = readFileSync(
      resolve(process.cwd(), "src/app/analysis/page.tsx"),
      "utf8",
    );
    expect(taskStatusSource).toContain("zhCN.task11.status");
    expect(taskStatusSource).not.toContain('queued: "排队中"');
    expect(analysisSource).toContain("zhCN.task11.analysis");
  });

  it("splits analysis advice into general, Siril, PixInsight, and Photoshop sections", async () => {
    window.history.replaceState({}, "", "/analysis?task=analysis-advice");
    await new TaskHistoryRepository().upsert({
      taskId: "analysis-advice",
      type: "analysis",
      fileName: "m42.fits",
      lastStatus: "completed",
      createdAt: "2026-06-14T09:00:00Z",
      expiresAt: "2036-06-15T11:00:00Z",
      resultAvailable: true,
    });
    api.getTask.mockResolvedValue(
      taskDetail({
        id: "analysis-advice",
        status: "completed",
        expires_at: "2036-06-15T11:00:00Z",
        result: {
          manifest_available: true,
          summary: {
            model: "kimi-k2.6",
            analysis: {
              overview: "整体可进入后期，但需要保护星云弱信号。",
              image_quality: {
                rating: "good",
                summary: "背景较干净，核心区域动态范围较高。",
                confidence: 0.82,
              },
              observations: {
                target: "目标居中。",
                background: "轻微梯度。",
                stars: "星点基本圆。",
                noise: "暗部噪声可控。",
                color: "整体偏暖。",
              },
              issues: [],
              workflow: [
                {
                  order: 1,
                  step: "数据阶段与通用策略",
                  purpose: "先确认数据阶段和目标类型。",
                  guidance: "保护真实弱信号，避免过度背景提取。",
                },
                {
                  order: 2,
                  step: "Siril 校准与叠加",
                  purpose: "建立干净的集成母版。",
                  guidance: "在 Siril 中检查序列、剔除异常子帧并导出母版。",
                },
                {
                  order: 3,
                  step: "PixInsight 线性处理",
                  purpose: "完成背景、校色、降噪与拉伸。",
                  guidance: "在 PixInsight 中使用受控拉伸并保护星云结构。",
                },
                {
                  order: 4,
                  step: "Photoshop 最终润色",
                  purpose: "完成非线性阶段的局部调整与输出。",
                  guidance: "在 Photoshop 中使用可逆调整图层输出成片。",
                },
              ],
              caveats: ["显示预览不等同于线性数据。"],
              preview_metadata: {},
            },
          },
          artifacts: [],
        },
      }),
    );

    render(<AnalysisPage />);

    expect(
      await screen.findByRole("heading", { name: "专业解读与后期建议" }),
    ).toBeVisible();
    expect(screen.getByRole("heading", { name: "深空天体后期处理建议" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Siril 软件的后期关键步骤" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "PixInsight 软件的后期关键步骤" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Photoshop 软件的后期关键步骤" })).toBeVisible();
    expect(screen.getByText("在 Siril 中检查序列、剔除异常子帧并导出母版。")).toBeVisible();
    expect(screen.getByText("在 PixInsight 中使用受控拉伸并保护星云结构。")).toBeVisible();
    expect(screen.getByText("在 Photoshop 中使用可逆调整图层输出成片。")).toBeVisible();
  });

  it("uses analysis-result markdown as the analysis page output when available", async () => {
    window.history.replaceState({}, "", "/analysis?task=analysis-result");
    await new TaskHistoryRepository().upsert({
      taskId: "analysis-result",
      type: "analysis",
      fileName: "ic434.fits",
      lastStatus: "completed",
      createdAt: "2026-06-14T09:00:00Z",
      expiresAt: "2036-06-15T11:00:00Z",
      resultAvailable: true,
    });
    api.getTask.mockResolvedValue(
      taskDetail({
        id: "analysis-result",
        status: "completed",
        expires_at: "2036-06-15T11:00:00Z",
        result: {
          manifest_available: true,
          summary: {
            model: "deterministic-skill-v1",
            analysis: {
              overview: "旧结构化摘要不应作为主显示。",
              image_quality: {
                rating: "good",
                summary: "fallback",
                confidence: 0.8,
              },
              observations: {
                target: "fallback",
                background: "fallback",
                stars: "fallback",
                noise: "fallback",
                color: "fallback",
              },
              issues: [],
              workflow: [
                {
                  order: 1,
                  step: "旧工作流",
                  purpose: "fallback",
                  guidance: "fallback",
                },
              ],
              caveats: ["fallback"],
            },
          },
          artifacts: ["analysis-result.json"],
        },
      }),
    );
    api.downloadArtifact.mockResolvedValue({
      blob: new Blob(
        [
          JSON.stringify({
            markdown: [
              "# 深空天体后期处理建议",
              "",
              "## 1. 整体后期处理建议",
              "",
              "IC 434 markdown output",
              "",
              "## 2. Siril 软件的后期关键步骤",
              "",
              "Siril 分档内容",
              "",
              "## 3. PixInsight 软件的后期关键步骤",
              "",
              "PixInsight 分档内容",
              "",
              "## 4. Photoshop 软件中的后期关键步骤",
              "",
              "Photoshop 分档内容",
            ].join("\n"),
          }),
        ],
        { type: "application/json" },
      ),
      fileName: "analysis-result.json",
      mediaType: "application/json",
      size: 72,
    });

    render(<AnalysisPage />);

    expect(await screen.findByText(/IC 434 markdown output/)).toBeVisible();
    expect(screen.getByRole("heading", { name: "深空天体后期处理建议" })).toBeVisible();
    const softwareColumns = screen.getByLabelText("Siril 与 PixInsight 后期关键步骤");
    expect(within(softwareColumns).getByRole("heading", { name: "Siril 软件的后期关键步骤" })).toBeVisible();
    expect(within(softwareColumns).getByRole("heading", { name: "PixInsight 软件的后期关键步骤" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Photoshop 软件的后期关键步骤" })).toBeVisible();
    expect(screen.getByText("Siril 分档内容")).toBeVisible();
    expect(screen.getByText("PixInsight 分档内容")).toBeVisible();
    expect(screen.getByText("Photoshop 分档内容")).toBeVisible();
    await waitFor(() => {
      expect(screen.queryByRole("heading", { name: "旧工作流" })).not.toBeInTheDocument();
    });
    expect(api.downloadArtifact).toHaveBeenCalledWith(
      "analysis-result",
      "analysis-result.json",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("requires exactly one processing style and defaults to balanced", async () => {
    const user = userEvent.setup();
    render(<ProcessingPage />);

    const styles = screen.getByRole("radiogroup", { name: "处理风格" });
    const balanced = within(styles).getByRole("radio", { name: /平衡/ });
    expect(within(styles).getAllByRole("radio")).toHaveLength(3);
    expect(balanced).toBeChecked();

    await user.click(within(styles).getByRole("radio", { name: /写实/ }));
    expect(within(styles).getByRole("radio", { name: /写实/ })).toBeChecked();
    expect(balanced).not.toBeChecked();
  });

  it("retains a processing task when local history persistence fails", async () => {
    const user = userEvent.setup();
    vi.spyOn(TaskHistoryRepository.prototype, "upsert").mockRejectedValue(
      new Error("processing history unavailable"),
    );
    api.createProcessingTask.mockResolvedValue(
      taskCreated("processing-local-fail", "processing", "balanced"),
    );
    api.getTask.mockResolvedValue(
      taskDetail({
        id: "processing-local-fail",
        type: "processing",
        status: "queued",
        style: "balanced",
      }),
    );

    render(<ProcessingPage />);
    await user.upload(
      screen.getByLabelText(zhCN.task11.upload.inputLabel),
      new File(["fits"], "processing.fits"),
    );
    await user.click(
      screen.getByRole("button", { name: zhCN.task11.processing.create }),
    );

    expect(await screen.findByText(zhCN.task11.status.labels.queued)).toBeVisible();
    expect(screen.getByText(/processing history unavailable/)).toBeVisible();
    expect(api.createProcessingTask).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByRole("button", { name: zhCN.task11.processing.create }),
    ).not.toBeInTheDocument();
  });

  it("reads local history summaries and supports resume, retry, and delete", async () => {
    const repository = new TaskHistoryRepository();
    await repository.upsert({
      taskId: "failed-1",
      type: "analysis",
      fileName: "m31.fits",
      lastStatus: "failed",
      createdAt: "2026-06-14T04:00:00Z",
      expiresAt: futureIso(),
      resultAvailable: false,
      summary: { retryable: true },
    });
    api.retryTask.mockResolvedValue({
      task_id: "retry-1",
      type: "analysis",
      status: "queued",
      quota_charged: false,
      created_at: "2026-06-14T05:00:00Z",
      expires_at: futureIso(),
      style: null,
    });

    render(<HistoryPage />);

    expect(await screen.findByText("m31.fits")).toBeVisible();
    expect(screen.getByRole("link", { name: "打开并继续" })).toHaveAttribute(
      "href",
      "/analysis?task=failed-1",
    );

    const failedCard = screen
      .getByRole("link", { name: "打开并继续" })
      .closest("article");
    expect(failedCard).not.toBeNull();

    fireEvent.click(within(failedCard!).getByRole("button", { name: "重试" }));
    await waitFor(() => expect(api.retryTask).toHaveBeenCalledWith("failed-1"));

    await waitFor(() =>
      expect(
        within(failedCard!).getByRole("button", { name: "删除记录" }),
      ).not.toBeDisabled(),
    );
    fireEvent.click(
      within(failedCard!).getByRole("button", { name: "删除记录" }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("link", {
          name: "打开并继续",
          description: undefined,
        }),
      ).toHaveAttribute("href", "/analysis?task=retry-1"),
    );
  });

  it("shows the retried task even when persisting its history fails", async () => {
    const repository = new TaskHistoryRepository();
    await repository.upsert({
      taskId: "retry-source",
      type: "analysis",
      fileName: "retry-source.fits",
      lastStatus: "failed",
      createdAt: "2026-06-14T04:00:00Z",
      expiresAt: futureIso(),
      resultAvailable: false,
      summary: { retryable: true },
    });
    api.retryTask.mockResolvedValue({
      ...taskCreated("retry-local-fail"),
      expires_at: futureIso(),
    });
    vi.spyOn(TaskHistoryRepository.prototype, "upsert").mockRejectedValue(
      new Error("retry history unavailable"),
    );

    render(<HistoryPage />);
    const sourceCard = (await screen.findByText("retry-source.fits")).closest(
      "article",
    );
    fireEvent.click(
      within(sourceCard!).getByRole("button", { name: zhCN.task11.history.retry }),
    );

    await waitFor(() =>
      expect(
        screen
          .getAllByRole("link", { name: zhCN.task11.history.open })
          .some(
            (link) =>
              link.getAttribute("href") ===
              "/analysis?task=retry-local-fail",
          ),
      ).toBe(true),
    );
    expect(screen.getByText(/retry history unavailable/)).toBeVisible();
    expect(api.retryTask).toHaveBeenCalledTimes(1);
  });

  it("hides a server-deleted history item when local cleanup fails", async () => {
    const repository = new TaskHistoryRepository();
    await repository.upsert({
      taskId: "delete-local-fail",
      type: "analysis",
      fileName: "delete-local-fail.fits",
      lastStatus: "completed",
      createdAt: "2026-06-14T04:00:00Z",
      expiresAt: "2026-06-15T04:00:00Z",
      resultAvailable: true,
    });
    api.deleteTask.mockResolvedValue(undefined);
    vi.spyOn(TaskHistoryRepository.prototype, "remove").mockRejectedValue(
      new Error("local cleanup unavailable"),
    );

    render(<HistoryPage />);
    const card = (await screen.findByText("delete-local-fail.fits")).closest(
      "article",
    );
    fireEvent.click(
      within(card!).getByRole("button", { name: zhCN.task11.history.remove }),
    );

    await waitFor(() =>
      expect(screen.queryByText("delete-local-fail.fits")).not.toBeInTheDocument(),
    );
    expect(screen.getByText(/local cleanup unavailable/)).toBeVisible();
    expect(api.deleteTask).toHaveBeenCalledWith("delete-local-fail");
  });

  it("removes the analysis processing action when the source expires", async () => {
    window.history.replaceState({}, "", "/analysis?task=analysis-expiring");
    await new TaskHistoryRepository().upsert({
      taskId: "analysis-expiring",
      type: "analysis",
      fileName: "expiring.fits",
      lastStatus: "completed",
      createdAt: "2026-06-14T09:00:00Z",
      expiresAt: "2026-06-14T10:00:01Z",
      resultAvailable: true,
    });
    vi.useFakeTimers();
    const now = new Date("2026-06-14T10:00:00Z");
    vi.setSystemTime(now);
    api.getTask.mockResolvedValue(
      taskDetail({
        id: "analysis-expiring",
        type: "analysis",
        status: "completed",
        expires_at: "2026-06-14T10:00:01Z",
      }),
    );

    render(<AnalysisPage />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(
      screen.getByRole("link", { name: "使用此文件自动出图" }),
    ).toBeVisible();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_001);
    });

    expect(
      screen.queryByRole("link", { name: "使用此文件自动出图" }),
    ).not.toBeInTheDocument();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("offers history retry only for retryable, unexpired, non-deleting failures", async () => {
    const repository = new TaskHistoryRepository();
    await Promise.all([
      repository.upsert({
        taskId: "allowed",
        type: "analysis",
        fileName: "allowed.fits",
        lastStatus: "failed",
        createdAt: "2026-06-14T09:00:00Z",
        expiresAt: "2026-06-14T11:00:00Z",
        resultAvailable: false,
        summary: { retryable: true, cleanupPending: false },
      }),
      repository.upsert({
        taskId: "expired",
        type: "analysis",
        fileName: "expired.fits",
        lastStatus: "failed",
        createdAt: "2026-06-14T09:00:00Z",
        expiresAt: "2026-06-14T09:30:00Z",
        resultAvailable: false,
        summary: { retryable: true, cleanupPending: false },
      }),
      repository.upsert({
        taskId: "deleting",
        type: "analysis",
        fileName: "deleting.fits",
        lastStatus: "failed",
        createdAt: "2026-06-14T09:00:00Z",
        expiresAt: "2026-06-14T11:00:00Z",
        resultAvailable: false,
        summary: { retryable: true, cleanupPending: true },
      }),
    ]);
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-14T10:00:00Z"));

    render(<HistoryPage />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(
      within(screen.getByText("allowed.fits").closest("article")!).getByRole(
        "button",
        { name: "重试" },
      ),
    ).toBeVisible();
    expect(
      within(screen.getByText("expired.fits").closest("article")!).queryByRole(
        "button",
        { name: "重试" },
      ),
    ).not.toBeInTheDocument();
    expect(
      within(screen.getByText("deleting.fits").closest("article")!).queryByRole(
        "button",
        { name: "重试" },
      ),
    ).not.toBeInTheDocument();
  });

  it("renders exact backend agent steps, persistent Mock labels, and PNG preview lifecycle", async () => {
    window.history.replaceState({}, "", "/processing?task=processing-1");
    await new TaskHistoryRepository().upsert({
      taskId: "processing-1",
      type: "processing",
      fileName: "m42.fits",
      style: "balanced",
      lastStatus: "completed",
      createdAt: "2026-06-14T09:00:00Z",
      expiresAt: "2036-06-15T11:00:00Z",
      resultAvailable: true,
    });
    api.getTask.mockResolvedValue(
      taskDetail({
        id: "processing-1",
        type: "processing",
        status: "completed",
        style: "balanced",
        expires_at: "2036-06-15T11:00:00Z",
        result: {
          manifest_available: true,
          summary: {
            demo: true,
            reference_artifact: "preview-demo.png",
            result_artifact: "result-demo.tiff",
          },
          artifacts: ["preview-demo.png", "result-demo.tiff"],
        },
        inspection: {
          selected_hdu: {
            index: 0,
            name: "PRIMARY",
            kind: "image",
            shape: [100, 120],
            dtype: "float32",
            supported: true,
          },
          statistics: {
            minimum: 0,
            maximum: 1,
            mean: 0.5,
            median: 0.5,
            standard_deviation: 0.1,
            finite_pixel_count: 12000,
          },
          hdus: [],
          header: { OBJECT: "M42" },
        },
      }),
    );
    const tools = [
      "mock.inspect",
      "mock.stretch",
      "mock.denoise",
      "mock.sharpen",
      "mock.color",
      "mock.evaluate",
      "mock.export",
    ];
    api.getTaskEvents.mockResolvedValue({
      events: tools.map((tool_name, index) => ({
        sequence: index + 1,
        level: "info",
        event_type: "agent_tool_started",
        payload: {
          agent_sequence: index + 1,
          step_id: String(index + 1).padStart(2, "0"),
          tool_name,
          tool_version: "v1",
        },
        created_at: "2026-06-14T09:00:00Z",
      })),
      next_after: 7,
      has_more: false,
    });
    api.downloadArtifact.mockResolvedValue({
      blob: new Blob(["png"], { type: "image/png" }),
      fileName: "preview-demo.png",
      mediaType: "image/png",
      size: 3,
    });
    const createObjectUrl = vi.fn(() => "blob:preview-1");
    const revokeObjectUrl = vi.fn();
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: createObjectUrl,
      revokeObjectURL: revokeObjectUrl,
    });

    const view = render(<ProcessingPage />);

    const preview = await screen.findByRole("img", {
      name: "AI 生成处理后预览",
    });
    expect(preview).toHaveStyle({
      backgroundImage: 'url("blob:preview-1")',
    });
    const plan = screen.getByRole("region", { name: "AI Agent 处理计划" });
    expect(
      within(plan).getAllByRole("listitem").map((item) => item.textContent),
    ).toEqual([
      "01mock.inspect",
      "02mock.stretch",
      "03mock.denoise",
      "04mock.sharpen",
      "05mock.color",
      "06mock.evaluate",
      "07mock.export",
    ]);
    expect(screen.getByText("实时事件")).toBeVisible();
    expect(screen.getByText("AI 生成成片")).toBeVisible();
    expect(screen.getByText("导出文件")).toBeVisible();
    expect(screen.getByText(/M42/)).toBeVisible();
    expect(api.downloadArtifact).toHaveBeenCalledWith(
      "processing-1",
      "preview-demo.png",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );

    view.unmount();
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:preview-1");
  });

  it("expires completed processing results, revokes preview, and hides downloads", async () => {
    window.history.replaceState({}, "", "/processing?task=processing-expiring");
    await new TaskHistoryRepository().upsert({
      taskId: "processing-expiring",
      type: "processing",
      fileName: "expiring.fits",
      style: "balanced",
      lastStatus: "completed",
      createdAt: "2026-06-14T09:00:00Z",
      expiresAt: "2026-06-14T10:00:02Z",
      resultAvailable: true,
    });
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-14T10:00:00Z"));
    api.getTask.mockResolvedValue(
      taskDetail({
        id: "processing-expiring",
        type: "processing",
        status: "completed",
        style: "balanced",
        expires_at: "2026-06-14T10:00:02Z",
        result: {
          manifest_available: true,
          summary: {
            demo: true,
            reference_artifact: "preview.png",
            result_artifact: "result.tiff",
          },
          artifacts: ["preview.png", "result.tiff"],
        },
      }),
    );
    api.downloadArtifact.mockResolvedValue({
      blob: new Blob(["png"], { type: "image/png" }),
      fileName: "preview.png",
      mediaType: "image/png",
      size: 3,
    });
    const revokeObjectUrl = vi.fn();
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:expiring-preview"),
      revokeObjectURL: revokeObjectUrl,
    });
    const markExpired = vi.spyOn(
      TaskHistoryRepository.prototype,
      "markExpired",
    ).mockResolvedValue(null);

    render(<ProcessingPage />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(
      screen.getByRole("img", { name: zhCN.task11.processing.previewAriaLabel }),
    ).toBeVisible();
    expect(screen.getByText(zhCN.task11.downloads.title)).toBeVisible();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_001);
    });

    expect(
      screen.queryByRole("img", { name: zhCN.task11.processing.previewAriaLabel }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(zhCN.task11.downloads.title)).not.toBeInTheDocument();
    expect(screen.getByText(zhCN.task11.processing.resultUnavailable)).toBeVisible();
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:expiring-preview");
    expect(markExpired).toHaveBeenCalledWith("processing-expiring");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("uses the exact three-step fallback before tool events arrive", async () => {
    window.history.replaceState({}, "", "/processing?task=processing-fallback");
    await new TaskHistoryRepository().upsert({
      taskId: "processing-fallback",
      type: "processing",
      fileName: "fallback.fits",
      style: "balanced",
      lastStatus: "queued",
      createdAt: "2026-06-14T09:00:00Z",
      expiresAt: "2026-06-14T11:00:00Z",
      resultAvailable: false,
    });
    api.getTask.mockResolvedValue(
      taskDetail({
        id: "processing-fallback",
        type: "processing",
        status: "queued",
      }),
    );

    render(<ProcessingPage />);

    const plan = await screen.findByRole("region", {
      name: "AI Agent 处理计划",
    });
    expect(within(plan).getAllByRole("listitem")).toHaveLength(3);
    expect(within(plan).getByText("生成 FITS 参考预览")).toBeVisible();
  });

  it("keeps polling cadence when history persistence rejects and stops terminal tasks", async () => {
    vi.useFakeTimers();
    const persistence = vi
      .spyOn(TaskHistoryRepository.prototype, "updateStatus")
      .mockRejectedValue(new Error("storage blocked"));
    api.getTask
      .mockResolvedValueOnce(taskDetail({ id: "poll-1", status: "running" }))
      .mockResolvedValueOnce(taskDetail({ id: "poll-1", status: "completed" }));

    render(<PollingProbe taskId="poll-1" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("persistence:storage blocked")).toBeVisible();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(api.getTask).toHaveBeenCalledTimes(2);
    expect(screen.getByText("status:completed")).toBeVisible();
    expect(vi.getTimerCount()).toBe(0);
    expect(persistence).toHaveBeenCalledTimes(2);
  });

  it("resets task state and event cursor and ignores stale prior-task responses", async () => {
    const firstTask = deferred<TaskDetailResponse>();
    const firstEvents = deferred<TaskEventsResponse>();
    api.getTask.mockImplementation((taskId: string) =>
      taskId === "task-1"
        ? firstTask.promise
        : Promise.resolve(taskDetail({ id: "task-2", status: "completed" })),
    );
    api.getTaskEvents.mockImplementation((taskId: string, after: number) => {
      if (taskId === "task-1") return firstEvents.promise;
      expect(after).toBe(0);
      return Promise.resolve({
        events: [
          {
            sequence: 1,
            level: "info",
            event_type: "task_completed",
            payload: {},
            created_at: "2026-06-14T09:00:00Z",
          },
        ],
        next_after: 1,
        has_more: false,
      });
    });

    const view = render(<PollingProbe taskId="task-1" />);
    view.rerender(<PollingProbe taskId="task-2" />);
    expect(await screen.findByText("status:completed")).toBeVisible();
    expect(screen.getByText("task:task-2")).toBeVisible();
    expect(screen.getByText("events:1")).toBeVisible();

    firstTask.resolve(taskDetail({ id: "task-1", status: "running" }));
    firstEvents.resolve({
      events: [
        {
          sequence: 99,
          level: "info",
          event_type: "stale",
          payload: {},
          created_at: "2026-06-14T09:00:00Z",
        },
      ],
      next_after: 99,
      has_more: false,
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText("task:task-2")).toBeVisible();
    expect(screen.getByText("events:1")).toBeVisible();
    expect(screen.queryByText("status:running")).not.toBeInTheDocument();
  });
});

function taskDetail(
  overrides: Partial<TaskDetailResponse> = {},
): TaskDetailResponse {
  return {
    id: "task-1",
    type: "analysis",
    status: "queued",
    stage: null,
    progress: 0,
    style: null,
    created_at: "2026-06-14T09:00:00Z",
    started_at: null,
    finished_at: null,
    expires_at: "2026-06-14T11:00:00Z",
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
    selected_hdu: 0,
    inspection: null,
    ...overrides,
  };
}

function taskCreated(
  taskId: string,
  type: "analysis" | "processing" = "analysis",
  style: "realistic" | "balanced" | "artistic" | null = null,
) {
  return {
    task_id: taskId,
    type,
    status: "queued" as const,
    quota_charged: true,
    created_at: "2026-06-14T09:00:00Z",
    expires_at: "2026-06-15T09:00:00Z",
    style,
  };
}

function copyAnalysisCreate() {
  return zhCN.task11.analysis.create;
}

function PollingProbe({ taskId }: { taskId: string }) {
  const result = useTaskPolling(taskId);
  return (
    <div>
      <span>task:{result.task?.id ?? "none"}</span>
      <span>status:{result.task?.status ?? "none"}</span>
      <span>events:{result.events.length}</span>
      <span>persistence:{result.persistenceError?.message ?? "none"}</span>
    </div>
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
}
