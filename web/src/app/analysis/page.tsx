"use client";

import Image from "next/image";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import TaskEventLog from "../../components/TaskEventLog";
import TaskStatusPanel from "../../components/TaskStatusPanel";
import UploadZone from "../../components/UploadZone";
import { useTaskPolling } from "../../hooks/useTaskPolling";
import { getApiClient } from "../../lib/api/client";
import type {
  FitsInspection,
  TaskStatus,
  UploadResponse,
} from "../../lib/api/types";
import { TaskHistoryRepository } from "../../lib/history/repository";
import { zhCN } from "../../lib/i18n/zh-CN";
import { fileTransfer } from "../../lib/transfer";

const historyRepository = new TaskHistoryRepository();

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function asRecordList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.map(asRecord).filter((item): item is Record<string, unknown> => item !== null)
    : [];
}

function asStringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function readBlobText(blob: Blob): Promise<string> {
  if (typeof blob.text === "function") {
    return blob.text();
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      resolve(typeof reader.result === "string" ? reader.result : "");
    });
    reader.addEventListener("error", () => {
      reject(reader.error ?? new Error("Failed to read artifact."));
    });
    reader.readAsText(blob);
  });
}

function resumedTaskId(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("task");
}

const OBSERVATION_EXPLANATIONS: Record<string, string> = {
  "主体与构图": "分析星系、星云等星体目标的信噪比及在画幅中的位置与裁剪建议。",
  "背景与梯度": "评估天光背景的平整度，检测是否存在光害梯度、暗角或平场校准残留。",
  "星点": "检测星点的半专宽 (FWHM)、椭圆率 (星点变型) 以及反卷积极限。",
  "噪声": "评估图像中高频噪声与低频噪声的分布，指导后期降噪的强度与算法选择。",
  "色彩": "通过恒星色彩校准 (PCC) 指标，分析红绿蓝通道的平衡及发射星云的色彩表现。"
};

type WorkflowGroupKey = "general" | "siril" | "pixinsight" | "photoshop";
type AnalysisMarkdownSectionKey = "general" | "siril" | "pixinsight" | "photoshop";

type AnalysisMarkdownSection = {
  key: AnalysisMarkdownSectionKey;
  title: string;
  body: string;
};

const ANALYSIS_MARKDOWN_SECTION_TITLES: Record<AnalysisMarkdownSectionKey, string> = {
  general: "深空天体后期处理建议",
  siril: "Siril 软件的后期关键步骤",
  pixinsight: "PixInsight 软件的后期关键步骤",
  photoshop: "Photoshop 软件的后期关键步骤",
};

function normalizeMarkdownHeading(line: string): string {
  return line
    .replace(/^#{1,6}\s+/, "")
    .replace(/^\d+[.、]\s*/, "")
    .replace(/[：:]$/, "")
    .trim();
}

function classifyMarkdownHeading(line: string): AnalysisMarkdownSectionKey | null {
  if (!/^#{1,6}\s+\S/.test(line)) return null;
  const heading = normalizeMarkdownHeading(line);
  if (heading.includes("Siril 软件") && heading.includes("后期关键步骤")) {
    return "siril";
  }
  if (heading.includes("PixInsight 软件") && heading.includes("后期关键步骤")) {
    return "pixinsight";
  }
  if (heading.includes("Photoshop 软件") && heading.includes("后期关键步骤")) {
    return "photoshop";
  }
  if (heading.includes("深空天体后期处理建议")) {
    return "general";
  }
  return null;
}

function splitAnalysisMarkdown(markdown: string): AnalysisMarkdownSection[] {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  const markers = lines
    .map((line, index) => ({ key: classifyMarkdownHeading(line), index }))
    .filter(
      (marker): marker is { key: AnalysisMarkdownSectionKey; index: number } =>
        marker.key !== null,
    );

  const firstMarkerByKey = new Map<AnalysisMarkdownSectionKey, number>();
  for (const marker of markers) {
    if (!firstMarkerByKey.has(marker.key)) {
      firstMarkerByKey.set(marker.key, marker.index);
    }
  }

  const firstSoftwareMarker = markers.find((marker) => marker.key !== "general");
  const generalStart = firstMarkerByKey.get("general") ?? -1;
  const generalEnd = firstSoftwareMarker?.index ?? lines.length;
  const bodyFor = (key: AnalysisMarkdownSectionKey): string => {
    if (key === "general") {
      return lines.slice(generalStart + 1, generalEnd).join("\n").trim();
    }
    const start = firstMarkerByKey.get(key);
    if (start === undefined) return "";
    const next = markers.find((marker) => marker.index > start)?.index ?? lines.length;
    return lines.slice(start + 1, next).join("\n").trim();
  };

  const sections = (["general", "siril", "pixinsight", "photoshop"] as const).map(
    (key) => ({
      key,
      title: ANALYSIS_MARKDOWN_SECTION_TITLES[key],
      body: bodyFor(key),
    }),
  );

  if (sections.every((section) => section.body.length === 0)) {
    return [
      {
        key: "general",
        title: ANALYSIS_MARKDOWN_SECTION_TITLES.general,
        body: markdown.trim(),
      },
    ];
  }

  return sections.filter((section) => section.body.length > 0);
}

function parseMarkdownInline(text: string): React.ReactNode[] {
  const parts = text.split(/(\*\*.*?\*\*|`.*?`)/g);
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={index}>{part.slice(1, -1)}</code>;
    }
    return part;
  });
}

function MiniMarkdown({ content }: { content: string }) {
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  const elements: React.ReactNode[] = [];
  let currentList: React.ReactNode[] = [];
  let listType: "ul" | "ol" | null = null;
  let keyCounter = 0;

  const flushList = () => {
    if (currentList.length > 0 && listType) {
      const Tag = listType;
      elements.push(
        <Tag key={`list-${keyCounter++}`}>
          {currentList}
        </Tag>
      );
      currentList = [];
      listType = null;
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) {
      flushList();
      continue;
    }

    if (line.startsWith("#### ")) {
      flushList();
      elements.push(<h5 key={`h5-${keyCounter++}`}>{parseMarkdownInline(line.slice(5))}</h5>);
    } else if (line.startsWith("### ")) {
      flushList();
      elements.push(<h4 key={`h4-${keyCounter++}`}>{parseMarkdownInline(line.slice(4))}</h4>);
    } else if (line.startsWith("- ") || line.startsWith("* ")) {
      if (listType !== "ul") {
        flushList();
        listType = "ul";
      }
      currentList.push(
        <li key={`li-${keyCounter++}`}>
          {parseMarkdownInline(line.slice(2))}
        </li>
      );
    } else if (/^\d+\.\s/.test(line)) {
      if (listType !== "ol") {
        flushList();
        listType = "ol";
      }
      const match = line.match(/^(\d+)\.\s(.*)/);
      const text = match ? match[2] : line;
      currentList.push(
        <li key={`li-${keyCounter++}`}>
          {parseMarkdownInline(text)}
        </li>
      );
    } else {
      flushList();
      elements.push(<p key={`p-${keyCounter++}`}>{parseMarkdownInline(line)}</p>);
    }
  }
  flushList();

  return <>{elements}</>;
}

function AnalysisMarkdownCard({
  section,
  hideTitle = false,
  noCardFrame = false,
}: {
  section: AnalysisMarkdownSection;
  hideTitle?: boolean;
  noCardFrame?: boolean;
}) {
  const [copied, setCopied] = useState(false);

  const content = (
    <div className="analysis-markdown">
      <MiniMarkdown content={section.body} />
    </div>
  );

  if (noCardFrame) {
    return content;
  }

  return (
    <section className="analysis-advice-card" style={{ position: "relative" }}>
      <button
        type="button"
        className={`copy-card-button ${copied ? "is-copied" : ""}`}
        onClick={() => {
          void navigator.clipboard.writeText(section.body);
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        }}
        title="复制此步骤内容"
      >
        <svg fill="none" height="12" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" width="12">
          {copied ? (
            <path d="M20 6L9 17l-5-5" />
          ) : (
            <>
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
            </>
          )}
        </svg>
        <span>{copied ? "已复制" : "复制"}</span>
      </button>
      {!hideTitle ? <h4 style={{ paddingRight: "3.5rem" }}>{section.title}</h4> : null}
      {content}
    </section>
  );
}



function classifyWorkflowStep(step: Record<string, unknown>): WorkflowGroupKey {
  const haystack = [
    asString(step.step),
    asString(step.purpose),
    asString(step.guidance),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  if (haystack.includes("photoshop")) return "photoshop";
  if (haystack.includes("pixinsight")) return "pixinsight";
  if (haystack.includes("siril")) return "siril";
  return "general";
}

function groupWorkflowSteps(workflow: Record<string, unknown>[]) {
  return workflow.reduce<Record<WorkflowGroupKey, Record<string, unknown>[]>>(
    (groups, step) => {
      groups[classifyWorkflowStep(step)].push(step);
      return groups;
    },
    {
      general: [],
      siril: [],
      pixinsight: [],
      photoshop: [],
    },
  );
}

function renderConfidenceGauge(confidence: number): string {
  const filledCount = Math.round(confidence * 10);
  const emptyCount = 10 - filledCount;
  return `${Math.round(confidence * 100)}% [${"▰".repeat(filledCount)}${"▱".repeat(emptyCount)}]`;
}

export default function AnalysisPage() {
  const copy = zhCN.task11.analysis;
  const [initialFile, setInitialFile] = useState<File | null>(null);
  const [upload, setUpload] = useState<UploadResponse | null>(null);
  const [fileName, setFileName] = useState<string>(copy.unnamedFile);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [userActiveTab, setUserActiveTab] = useState<"ai" | "fits" | null>(null);
  const [fitsFilter, setFitsFilter] = useState("");
  const [copiedReport, setCopiedReport] = useState(false);

  useEffect(() => {
    const transferFile = fileTransfer.get();
    if (transferFile) {
      queueMicrotask(() => setInitialFile(transferFile));
    }
  }, []);
  const [initialStatus, setInitialStatus] = useState<TaskStatus | null>(null);
  const [creating, setCreating] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [localPersistenceError, setLocalPersistenceError] = useState<
    string | null
  >(null);
  const [currentTime, setCurrentTime] = useState(() => Date.now());
  const [preview, setPreview] = useState<{
    taskId: string;
    url: string | null;
    error: string | null;
  } | null>(null);
  const [analysisResult, setAnalysisResult] = useState<{
    taskId: string;
    markdown: string | null;
    error: string | null;
  } | null>(null);
  const {
    task,
    events,
    error,
    persistenceError,
    loading,
    refresh,
  } = useTaskPolling(taskId);

  useEffect(() => {
    const resumed = resumedTaskId();
    if (!resumed) return;
    let active = true;
    void historyRepository.get(resumed).then((entry) => {
      if (!active) return;
      setTaskId(resumed);
      if (entry) {
        setFileName(entry.fileName);
        setInitialStatus(entry.lastStatus);
      }
    });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (task?.status !== "completed" || task.expires_at === null) {
      return;
    }
    const expiryTime = new Date(task.expires_at).getTime();
    if (expiryTime <= currentTime) {
      return;
    }
    const remaining = expiryTime - Date.now();
    if (remaining <= 0) {
      queueMicrotask(() => setCurrentTime(Date.now()));
      return;
    }
    const timer = setTimeout(
      () => setCurrentTime(Date.now()),
      Math.min(remaining + 1, 2_147_000_000),
    );
    return () => clearTimeout(timer);
  }, [task?.expires_at, task?.status, currentTime]);

  const handleUploaded = useCallback(
    (nextUpload: UploadResponse, file: File) => {
      setUpload(nextUpload);
      setFileName(file.name);
      setActionError(null);
    },
    [],
  );

  const createTask = useCallback(async () => {
    if (!upload) return;
    setCreating(true);
    setActionError(null);
    setLocalPersistenceError(null);
    let created;
    try {
      created = await getApiClient().createAnalysisTask({
        upload_id: upload.upload_id,
      });
    } catch (caught) {
      setActionError(
        caught instanceof Error ? caught.message : copy.createError,
      );
      setCreating(false);
      return;
    }

    setInitialStatus(created.status);
    setTaskId(created.task_id);
    try {
      await historyRepository.upsert({
        taskId: created.task_id,
        type: created.type,
        fileName,
        style: created.style,
        lastStatus: created.status,
        createdAt: created.created_at,
        expiresAt: created.expires_at,
        summary: null,
        resultAvailable: false,
      });
    } catch (caught) {
      setLocalPersistenceError(
        caught instanceof Error
          ? caught.message
          : zhCN.task11.common.historyPersistenceError,
      );
    } finally {
      setCreating(false);
    }
  }, [copy.createError, fileName, upload]);

  const cancelTask = useCallback(async () => {
    if (!taskId) return;
    setCancelling(true);
    setActionError(null);
    try {
      await getApiClient().cancelTask(taskId);
      refresh();
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : copy.cancelError);
    } finally {
      setCancelling(false);
    }
  }, [copy.cancelError, refresh, taskId]);

  const resetToUpload = useCallback(() => {
    setTaskId(null);
    setUpload(null);
    setInitialFile(null);
    setInitialStatus(null);
    setActionError(null);
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      url.searchParams.delete("task");
      window.history.replaceState({}, "", url.toString());
    }
  }, []);

  const retryCurrentTask = useCallback(async () => {
    if (!taskId) return;
    setRetrying(true);
    setActionError(null);
    try {
      const created = await getApiClient().retryTask(taskId);
      setInitialStatus(created.status);
      setTaskId(created.task_id);
      if (typeof window !== "undefined") {
        const url = new URL(window.location.href);
        url.searchParams.set("task", created.task_id);
        window.history.replaceState({}, "", url.toString());
      }
      try {
        await historyRepository.upsert({
          taskId: created.task_id,
          type: created.type,
          fileName,
          style: created.style,
          lastStatus: created.status,
          createdAt: created.created_at,
          expiresAt: created.expires_at,
          summary: null,
          resultAvailable: false,
        });
      } catch (caught) {
        setLocalPersistenceError(
          caught instanceof Error
            ? caught.message
            : zhCN.task11.common.historyPersistenceError,
        );
      }
    } catch (caught) {
      setActionError(
        caught instanceof Error ? caught.message : zhCN.task11.history.retryError,
      );
    } finally {
      setRetrying(false);
    }
  }, [taskId, fileName]);

  const inspection = useMemo(() => {
    if (upload) return upload.inspection;
    return task?.inspection as unknown as FitsInspection | null;
  }, [task?.inspection, upload]);



  const filteredHeader = useMemo(() => {
    if (!inspection?.header) return [];
    const entries = Object.entries(inspection.header);
    if (!fitsFilter.trim()) return entries;
    const query = fitsFilter.toLowerCase();
    return entries.filter(([key, value]) =>
      key.toLowerCase().includes(query) || String(value).toLowerCase().includes(query)
    );
  }, [inspection?.header, fitsFilter]);
  const summary = asRecord(task?.result.summary);
  const analysis = asRecord(summary?.analysis);
  const imageQuality = asRecord(analysis?.image_quality);
  const observations = asRecord(analysis?.observations);
  const issues = asRecordList(analysis?.issues);
  const workflow = asRecordList(analysis?.workflow);
  const workflowGroups = groupWorkflowSteps(workflow);
  const caveats = asStringList(analysis?.caveats);
  const sourceValid =
    task?.status === "completed" &&
    task.expires_at !== null &&
    new Date(task.expires_at).getTime() > currentTime;
  const previewName =
    sourceValid && task
      ? task.result.artifacts.find((name) => name === "analysis-preview.png") ?? null
      : null;
  const resultName =
    sourceValid && task
      ? task.result.artifacts.find((name) => name === "analysis-result.json") ?? null
      : null;

  useEffect(() => {
    if (!taskId || !previewName) return;
    let active = true;
    let objectUrl: string | null = null;
    const controller = new AbortController();
    void getApiClient()
      .downloadArtifact(taskId, previewName, { signal: controller.signal })
      .then((artifact) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(artifact.blob);
        setPreview({ taskId, url: objectUrl, error: null });
      })
      .catch((caught) => {
        if (!active || controller.signal.aborted) return;
        setPreview({
          taskId,
          url: null,
          error: caught instanceof Error ? caught.message : copy.previewError,
        });
      });
    return () => {
      active = false;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [copy.previewError, previewName, taskId]);

  useEffect(() => {
    if (!taskId || !resultName) return;
    let active = true;
    const controller = new AbortController();
    void getApiClient()
      .downloadArtifact(taskId, resultName, { signal: controller.signal })
      .then(async (artifact) => {
        const parsed = asRecord(JSON.parse(await readBlobText(artifact.blob)));
        const markdown = asString(parsed?.markdown);
        if (!active) return;
        setAnalysisResult({
          taskId,
          markdown,
          error: markdown ? null : "分析结果缺少可显示的报告内容。",
        });
      })
      .catch((caught) => {
        if (!active || controller.signal.aborted) return;
        setAnalysisResult({
          taskId,
          markdown: null,
          error: caught instanceof Error ? caught.message : "分析结果没有读取成功。",
        });
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [resultName, taskId]);



  const activePreview =
    preview?.taskId === taskId ? preview : null;
  const activeResult =
    analysisResult?.taskId === taskId ? analysisResult : null;

  const activeResultMarkdown = activeResult?.markdown;

  const markdownSections = useMemo(() => {
    if (activeResultMarkdown) {
      return splitAnalysisMarkdown(activeResultMarkdown);
    }
    return [];
  }, [activeResultMarkdown]);

  const markdownSectionByKey = useMemo(() => {
    return new Map(markdownSections.map((section) => [section.key, section]));
  }, [markdownSections]);

  const overviewTitle = useMemo(() => {
    const generalSection = markdownSectionByKey.get("general");
    if (generalSection) {
      return generalSection.title;
    }
    return copy.overviewTitle;
  }, [markdownSectionByKey, copy.overviewTitle]);

  const activeTab = (() => {
    if (userActiveTab !== null) {
      return userActiveTab;
    }
    if (task?.status === "completed" || activeResult?.markdown) {
      return "ai";
    }
    if (inspection && !analysis && !activeResult?.markdown) {
      return "fits";
    }
    return "ai";
  })();

  return (
    <main className="workflow-main">
      <div className="page-shell workflow-shell">
        <header className="workflow-hero">
          <span className="section-kicker">{copy.kicker}</span>
          <h1>{copy.title}</h1>
          <p>{copy.description}</p>
          <p className="workflow-start-note">{copy.firstRunNote}</p>
        </header>

        {!taskId ? (
          <section className="workflow-setup" aria-label={copy.setupAriaLabel}>
            <div className={`setup-step ${upload ? "is-complete" : ""}`}>
              <span className="setup-step__marker" aria-hidden="true">1</span>
              <div className="setup-step__body">
                <div className="setup-step__intro">
                  <strong>{copy.setupSourceTitle}</strong>
                  <span>{copy.setupSourceHint}</span>
                </div>
                <UploadZone initialFile={initialFile} onUploaded={handleUploaded} />
              </div>
            </div>

            <div className={`setup-step ${upload ? "is-complete" : ""}`}>
              <span className="setup-step__marker" aria-hidden="true">2</span>
              <div className="setup-step__body">
                <div className="setup-step__intro">
                  <strong>{copy.setupCheckTitle}</strong>
                  <span>{upload ? copy.setupCheckReady : copy.setupCheckHint}</span>
                </div>
              </div>
            </div>

            <div className={`setup-step setup-step--action ${upload ? "is-complete" : ""}`}>
              <span className="setup-step__marker" aria-hidden="true">3</span>
              <div className="setup-step__body">
                <div className="setup-step__intro">
                  <strong>{copy.setupCreateTitle}</strong>
                  <span>{copy.setupCreateHint}</span>
                </div>
                <div className="workflow-action-row">
                  <button
                    className="button button--primary"
                    disabled={!upload || creating}
                    onClick={() => void createTask()}
                    type="button"
                  >
                    {creating ? copy.creating : copy.create}
                  </button>
                  <div className="workflow-action-help" aria-live="polite">
                    <strong>{upload ? copy.readyToCreate : copy.uploadRequired}</strong>
                    <span>{copy.quotaNotice}</span>
                  </div>
                </div>
              </div>
            </div>
          </section>
        ) : null}

        <TaskStatusPanel
          busy={cancelling}
          initialStatus={initialStatus}
          onCancel={() => void cancelTask()}
          task={task}
        />

        {((task && task.status === "failed") || (!task && initialStatus === "failed") || (error && error.name === "PollingTimeoutError")) && (
          <div className="recovery-panel">
            <div className="border-mask" aria-hidden="true" />
            <div className="recovery-header">
              <span className="recovery-badge">可以重试</span>
              <h3>这次分析没有完成</h3>
            </div>
            <p className="recovery-desc">
              还没有生成可用的分析结果。当前页面会保留已选择的文件；先重试一次，如果仍失败，再重新选择文件。
            </p>
            <div className="recovery-actions">
              <button
                className="button button--secondary"
                onClick={resetToUpload}
                type="button"
              >
                重新选择文件
              </button>
              <button
                className="button button--primary"
                disabled={retrying}
                onClick={() => void retryCurrentTask()}
                type="button"
              >
                {retrying ? "正在重新排队…" : "重试分析"}
              </button>
            </div>
          </div>
        )}
        {loading && !task ? <p className="empty-copy">{copy.restoring}</p> : null}
        {actionError ? (
          <p className="form-error" role="alert">
            {actionError}
          </p>
        ) : null}
        {error ? (
          <p className="form-error" role="alert">
            {error.message}
          </p>
        ) : null}
        {persistenceError ? (
          <p className="storage-warning" role="status">
            {zhCN.task11.common.storageWarning(persistenceError.message)}
          </p>
        ) : null}
        {localPersistenceError ? (
          <p className="storage-warning" role="status">
            {zhCN.task11.common.storageWarning(localPersistenceError)}
          </p>
        ) : null}

        {/* 数据报告 Tabs 容器 */}
        {(inspection || analysis || activeResult?.markdown) ? (
          <div className="analysis-results-container">
            <div className="analysis-tabs" role="tablist" aria-label="数据分析与报告">
              {(analysis || activeResult?.markdown) ? (
                <button
                  role="tab"
                  aria-selected={activeTab === "ai"}
                  aria-controls="tabpanel-ai"
                  id="tab-ai"
                  className={`tab-button ${activeTab === "ai" ? "is-active" : ""}`}
                  onClick={() => setUserActiveTab("ai")}
                  type="button"
                >
                  AI 智能分析报告
                </button>
              ) : null}
              {inspection ? (
                <button
                  role="tab"
                  aria-selected={activeTab === "fits"}
                  aria-controls="tabpanel-fits"
                  id="tab-fits"
                  className={`tab-button ${activeTab === "fits" ? "is-active" : ""}`}
                  onClick={() => setUserActiveTab("fits")}
                  type="button"
                >
                  FITS 原始元数据
                </button>
              ) : null}
            </div>

            {/* AI 智能分析报告 Tab Panel */}
            {(analysis || activeResult?.markdown) ? (
              <div
                id="tabpanel-ai"
                role="tabpanel"
                aria-labelledby="tab-ai"
                style={{ display: activeTab === "ai" ? "block" : "none" }}
              >
                <section className="result-panel ai-analysis-panel">
                  <div className="border-mask" aria-hidden="true" />
                  <div className="panel-heading">
                    <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
                      <h2>{copy.aiTitle}</h2>
                      {activeResultMarkdown && (
                        <button
                          type="button"
                          className={`copy-report-button ${copiedReport ? "is-copied" : ""}`}
                          onClick={() => {
                            void navigator.clipboard.writeText(activeResultMarkdown);
                            setCopiedReport(true);
                            setTimeout(() => setCopiedReport(false), 2000);
                          }}
                          title="复制完整报告为 Markdown"
                        >
                          <svg fill="none" height="14" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" width="14">
                            {copiedReport ? (
                              <path d="M20 6L9 17l-5-5" />
                            ) : (
                              <>
                                <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                              </>
                            )}
                          </svg>
                          <span>{copiedReport ? "已复制" : "复制完整报告"}</span>
                        </button>
                      )}
                    </div>
                    <span style={{ fontSize: "0.82rem", opacity: 0.65 }}>{String(summary?.model ?? "kimi-k2.6")}</span>
                  </div>
                  <div className="analysis-preview-layout">
                    <div>
                      <h3>{copy.previewTitle}</h3>
                      {activePreview?.url ? (
                        <Image
                          alt={copy.previewAriaLabel}
                          className="analysis-preview-image"
                          height={1200}
                          src={activePreview.url}
                          unoptimized
                          width={1600}
                        />
                      ) : (
                        <div className="analysis-preview-placeholder">
                          {activePreview?.error ?? copy.previewLoading}
                        </div>
                      )}
                      <p className="analysis-disclaimer">{copy.previewDisclaimer}</p>
                    </div>
                    {activeResultMarkdown ? (
                      <div className="analysis-overview">
                        <h3>{overviewTitle}</h3>
                        {markdownSectionByKey.get("general") ? (
                          <AnalysisMarkdownCard
                            section={markdownSectionByKey.get("general")!}
                            hideTitle={true}
                            noCardFrame={true}
                          />
                        ) : null}
                      </div>
                    ) : analysis ? (
                      <div className="analysis-overview">
                        <h3>{overviewTitle}</h3>
                        <p>{asString(analysis.overview)}</p>
                        {imageQuality ? (
                          <dl className="analysis-quality">
                            <div>
                              <dt>{copy.qualityRating}</dt>
                              <dd style={{ marginTop: "0.4rem" }}>
                                <span className={`rating-tag rating-tag--${asString(imageQuality.rating) ?? "fair"}`}>
                                  {copy.qualityLabels[asString(imageQuality.rating) ?? "fair"] ?? asString(imageQuality.rating)}
                                </span>
                              </dd>
                            </div>
                            <div>
                              <dt>{copy.confidence}</dt>
                              <dd style={{ fontFamily: "var(--type-mono)", fontSize: "0.82rem", letterSpacing: "-0.01em", marginTop: "0.42rem" }}>
                                {typeof imageQuality.confidence === "number"
                                  ? renderConfidenceGauge(imageQuality.confidence)
                                  : zhCN.task11.common.unavailable}
                              </dd>
                            </div>
                          </dl>
                        ) : null}
                        <p>{asString(imageQuality?.summary)}</p>
                      </div>
                    ) : null}
                  </div>

                  {/* 软件后期处理步骤通栏区域 */}
                  {activeResultMarkdown && (markdownSectionByKey.get("siril") || markdownSectionByKey.get("pixinsight") || markdownSectionByKey.get("photoshop")) ? (
                    <section className="analysis-section" style={{ display: "grid", gap: "1rem" }}>
                      {markdownSectionByKey.get("siril") || markdownSectionByKey.get("pixinsight") ? (
                        <div className="analysis-advice-columns" aria-label="Siril 与 PixInsight 后期关键步骤">
                          {markdownSectionByKey.get("siril") ? (
                            <AnalysisMarkdownCard section={markdownSectionByKey.get("siril")!} />
                          ) : null}
                          {markdownSectionByKey.get("pixinsight") ? (
                            <AnalysisMarkdownCard section={markdownSectionByKey.get("pixinsight")!} />
                          ) : null}
                        </div>
                      ) : null}
                      {markdownSectionByKey.get("photoshop") ? (
                        <AnalysisMarkdownCard section={markdownSectionByKey.get("photoshop")!} />
                      ) : null}
                    </section>
                  ) : null}

                  {activeResult?.error ? (
                    <p className="form-error" role="alert">
                      {activeResult.error}
                    </p>
                  ) : null}

                  {!activeResult?.markdown && observations ? (
                    <section className="analysis-section">
                      <h3>{copy.observationsTitle}</h3>
                      <dl className="analysis-observations">
                        {[
                          [copy.target, observations.target],
                          [copy.background, observations.background],
                          [copy.stars, observations.stars],
                          [copy.noise, observations.noise],
                          [copy.color, observations.color],
                        ].map(([label, value]) => (
                          <div key={String(label)}>
                            <dt>
                              {String(label)}
                              {OBSERVATION_EXPLANATIONS[String(label)] && (
                                <span
                                  className="tooltip-trigger"
                                  style={{ marginLeft: "4px" }}
                                  tabIndex={0}
                                  aria-describedby={`tooltip-${String(label)}`}
                                >
                                  <svg fill="none" height="12" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" width="12" style={{ opacity: 0.5, verticalAlign: "middle" }}>
                                    <circle cx="12" cy="12" r="10" />
                                    <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
                                    <line x1="12" x2="12.01" y1="17" y2="17" />
                                  </svg>
                                  <span id={`tooltip-${String(label)}`} className="tooltip-card" role="tooltip">
                                    {OBSERVATION_EXPLANATIONS[String(label)]}
                                  </span>
                                </span>
                              )}
                            </dt>
                            <dd>{asString(value) ?? zhCN.task11.common.unavailable}</dd>
                          </div>
                        ))}
                      </dl>
                    </section>
                  ) : null}

                  {!activeResult?.markdown && issues.length > 0 ? (
                    <section className="analysis-section">
                      <h3>{copy.issuesTitle}</h3>
                      <div className="analysis-issue-list">
                        {issues.map((issue, index) => (
                          <article key={`${asString(issue.title)}-${index}`}>
                            <div>
                              <h4>{asString(issue.title)}</h4>
                              <span data-severity={asString(issue.severity)}>
                                {copy.severityLabels[asString(issue.severity) ?? "low"] ?? asString(issue.severity)}
                              </span>
                            </div>
                            <p>{asString(issue.evidence)}</p>
                            <strong>{asString(issue.recommendation)}</strong>
                          </article>
                        ))}
                      </div>
                    </section>
                  ) : null}

                  {!activeResult?.markdown && workflow.length > 0 ? (
                    <section className="analysis-section">
                      <h3>{copy.workflowTitle}</h3>
                      <div className="analysis-workflow-groups">
                        {workflowGroups.general.length > 0 ? (
                          <section className="analysis-workflow-group">
                            <h4>{copy.workflowGroupTitles.general}</h4>
                            <ol className="analysis-workflow">
                              {workflowGroups.general.map((step, index) => (
                                <li key={`${asString(step.step)}-${index}`}>
                                  <h5>{asString(step.step)}</h5>
                                  <p>{asString(step.purpose)}</p>
                                  <strong>{asString(step.guidance)}</strong>
                                </li>
                              ))}
                            </ol>
                          </section>
                        ) : null}

                        {workflowGroups.siril.length > 0 || workflowGroups.pixinsight.length > 0 ? (
                          <div className="analysis-workflow-software-grid">
                            {(["siril", "pixinsight"] as const).map((group) =>
                              workflowGroups[group].length > 0 ? (
                                <section className="analysis-workflow-group" key={group}>
                                  <h4>{copy.workflowGroupTitles[group]}</h4>
                                  <ol className="analysis-workflow">
                                    {workflowGroups[group].map((step, index) => (
                                      <li key={`${asString(step.step)}-${index}`}>
                                        <h5>{asString(step.step)}</h5>
                                        <p>{asString(step.purpose)}</p>
                                        <strong>{asString(step.guidance)}</strong>
                                      </li>
                                    ))}
                                  </ol>
                                </section>
                              ) : null,
                            )}
                          </div>
                        ) : null}

                        {workflowGroups.photoshop.length > 0 ? (
                          <section className="analysis-workflow-group">
                            <h4>{copy.workflowGroupTitles.photoshop}</h4>
                            <ol className="analysis-workflow">
                              {workflowGroups.photoshop.map((step, index) => (
                                <li key={`${asString(step.step)}-${index}`}>
                                  <h5>{asString(step.step)}</h5>
                                  <p>{asString(step.purpose)}</p>
                                  <strong>{asString(step.guidance)}</strong>
                                </li>
                              ))}
                            </ol>
                          </section>
                        ) : null}
                      </div>
                    </section>
                  ) : null}

                  {!activeResult?.markdown && caveats.length > 0 ? (
                    <section className="analysis-section analysis-caveats">
                      <h3>{copy.caveatsTitle}</h3>
                      <ul>
                        {caveats.map((caveat) => <li key={caveat}>{caveat}</li>)}
                      </ul>
                    </section>
                  ) : null}
                </section>
              </div>
            ) : null}

            {/* FITS 原始元数据 Tab Panel */}
            {inspection ? (
              <div
                id="tabpanel-fits"
                role="tabpanel"
                aria-labelledby="tab-fits"
                style={{ display: activeTab === "fits" ? "block" : "none" }}
              >
                <section aria-label={copy.realDataAriaLabel} className="result-panel">
                  <div className="border-mask" aria-hidden="true" />
                  <div className="panel-heading">
                    <div>
                      <h2>{copy.realDataTitle}</h2>
                    </div>
                    <span style={{ fontSize: "0.82rem", opacity: 0.65 }}>
                      {copy.hduIndex} {inspection.selected_hdu.index}
                    </span>
                  </div>
                  <div className="fits-data-sections">
                    <section aria-labelledby="hdu-list-title">
                      <h3 id="hdu-list-title">{copy.hduListTitle}</h3>
                      <div className="hdu-card-grid">
                        {inspection.hdus.map((hdu) => (
                          <dl className="hdu-card" key={hdu.index}>
                            <div>
                              <dt>{copy.hduIndex}</dt>
                              <dd>{hdu.index}</dd>
                            </div>
                            <div>
                              <dt>{copy.hduName}</dt>
                              <dd>{hdu.name}</dd>
                            </div>
                            <div>
                              <dt>{copy.hduKind}</dt>
                              <dd>{hdu.kind}</dd>
                            </div>
                            <div>
                              <dt>{copy.shape}</dt>
                              <dd>
                                {hdu.shape?.join(" × ") ??
                                  zhCN.task11.common.unavailable}
                              </dd>
                            </div>
                            <div>
                              <dt>{copy.dtype}</dt>
                              <dd>{hdu.dtype ?? zhCN.task11.common.unavailable}</dd>
                            </div>
                            <div>
                              <dt>{copy.support}</dt>
                              <dd>{hdu.supported ? copy.supported : copy.unsupported}</dd>
                            </div>
                          </dl>
                        ))}
                      </div>
                    </section>
                    <section aria-labelledby="fits-header-title">
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "1rem", marginBottom: "1rem" }}>
                        <h3 id="fits-header-title" style={{ margin: 0 }}>{copy.headerTitle}</h3>
                        <div className="fits-filter-wrapper" style={{ position: "relative" }}>
                          <input
                            type="text"
                            className="fits-filter-input"
                            placeholder="搜索 Header 字段 (如 EXPTIME, FILTER)"
                            value={fitsFilter}
                            onChange={(e) => setFitsFilter(e.target.value)}
                            style={{ paddingRight: fitsFilter ? "2rem" : "0.85rem" }}
                            aria-label="搜索 FITS Header 字段"
                          />
                          {fitsFilter && (
                            <button
                              type="button"
                              onClick={() => setFitsFilter("")}
                              style={{
                                position: "absolute",
                                right: "0.75rem",
                                background: "transparent",
                                border: "none",
                                color: "var(--color-space-text-muted)",
                                cursor: "pointer",
                                fontSize: "1.1rem",
                                padding: "2px",
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                transition: "color 150ms ease"
                              }}
                              title="清空搜索内容"
                              aria-label="清空搜索内容"
                            >
                              &times;
                            </button>
                          )}
                        </div>
                      </div>
                      {filteredHeader.length > 0 ? (
                        <dl className="fits-header-grid">
                          {filteredHeader.map(([key, value]) => (
                            <div key={key}>
                              <dt>{key}</dt>
                              <dd>{String(value)}</dd>
                            </div>
                          ))}
                        </dl>
                      ) : (
                        <p className="empty-copy" style={{ padding: "2rem 0", textAlign: "center", opacity: 0.6 }}>
                          没有找到匹配的 Header 字段。
                        </p>
                      )}
                    </section>
                    <section aria-labelledby="statistics-title">
                      <h3 id="statistics-title">{copy.statisticsTitle}</h3>
                      <dl className="metric-cards metric-cards--real">
                        {[
                          [copy.minimum, inspection.statistics.minimum],
                          [copy.maximum, inspection.statistics.maximum],
                          [copy.mean, inspection.statistics.mean],
                          [copy.median, inspection.statistics.median],
                          [
                            copy.standardDeviation,
                            inspection.statistics.standard_deviation,
                          ],
                          [
                            copy.finitePixelCount,
                            inspection.statistics.finite_pixel_count.toLocaleString(),
                          ],
                        ].map(([label, value]) => (
                          <div key={String(label)}>
                            <dt>{label}</dt>
                            <dd>{value}</dd>
                          </div>
                        ))}
                      </dl>
                    </section>
                  </div>
                </section>
              </div>
            ) : null}
          </div>
        ) : null}

            {/* 天体物理学与后期指南折叠面板 */}
            {copy.astroGuideItems && (
              <section className="analysis-section astro-guide-section">
                <details className="astro-guide-details">
                  <summary className="astro-guide-summary">
                    <h3>
                      {copy.astroGuideTitle}
                      <svg className="astro-guide-summary__icon" fill="none" height="16" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" width="16" style={{ marginLeft: "8px", verticalAlign: "middle", transition: "transform 200ms ease" }}>
                        <path d="M19 9l-7 7-7-7" />
                      </svg>
                    </h3>
                  </summary>
                  <div className="astro-guide-content">
                    {copy.astroGuideItems.map((item, index) => (
                      <article key={index} className="astro-guide-card">
                        <h4>{item.title}</h4>
                        {item.desc.split("\n").map((line, lIdx) => (
                          <p key={lIdx}>{line}</p>
                        ))}
                      </article>
                    ))}
                  </div>
                </details>
              </section>
            )}

            {sourceValid && taskId ? (
              <div className="next-action">
                <div className="border-mask" aria-hidden="true" />
                <div>
                  <h2>{copy.sourceValid}</h2>
                </div>
            <Link
              className="button button--primary"
              href={`/processing?source_task_id=${encodeURIComponent(taskId)}`}
            >
              {copy.processAction}
            </Link>
          </div>
        ) : null}

        {taskId ? <TaskEventLog events={events} /> : null}
      </div>
    </main>
  );
}
