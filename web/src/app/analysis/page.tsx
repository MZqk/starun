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

export default function AnalysisPage() {
  const copy = zhCN.task11.analysis;
  const [initialFile, setInitialFile] = useState<File | null>(null);
  const [upload, setUpload] = useState<UploadResponse | null>(null);
  const [fileName, setFileName] = useState<string>(copy.unnamedFile);
  const [taskId, setTaskId] = useState<string | null>(null);

  useEffect(() => {
    const transferFile = fileTransfer.get();
    if (transferFile) {
      setInitialFile(transferFile);
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

  async function createTask() {
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
  }

  async function cancelTask() {
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
  }

  const inspection = useMemo(() => {
    if (upload) return upload.inspection;
    return task?.inspection as unknown as FitsInspection | null;
  }, [task?.inspection, upload]);
  const summary = asRecord(task?.result.summary);
  const analysis = asRecord(summary?.analysis);
  const imageQuality = asRecord(analysis?.image_quality);
  const observations = asRecord(analysis?.observations);
  const issues = asRecordList(analysis?.issues);
  const workflow = asRecordList(analysis?.workflow);
  const caveats = asStringList(analysis?.caveats);
  const sourceValid =
    task?.status === "completed" &&
    task.expires_at !== null &&
    new Date(task.expires_at).getTime() > currentTime;
  const previewName =
    sourceValid && task
      ? task.result.artifacts.find((name) => name === "analysis-preview.png") ?? null
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
    const handleKeyDown = (event: KeyboardEvent) => {
      const activeEl = document.activeElement;
      const isEditable =
        activeEl &&
        (activeEl.tagName === "INPUT" ||
          activeEl.tagName === "TEXTAREA" ||
          activeEl.getAttribute("contenteditable") === "true");
      if (isEditable) return;

      if (event.key === "Enter" && !taskId && upload && !creating) {
        event.preventDefault();
        void createTask();
      }

      const isActiveTask = task && ["queued", "running"].includes(task.status);
      if (event.key === "Escape" && taskId && isActiveTask && !cancelling) {
        event.preventDefault();
        void cancelTask();
      }

      if ((event.key === "p" || event.key === "P") && sourceValid && taskId) {
        event.preventDefault();
        window.location.href = `/processing?source_task_id=${encodeURIComponent(taskId)}`;
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [taskId, upload, creating, task, cancelling, sourceValid]);

  const activePreview =
    preview?.taskId === taskId ? preview : null;

  return (
    <main className="workflow-main">
      <div className="page-shell workflow-shell">
        <header className="workflow-hero">
          <span className="section-kicker">{copy.kicker}</span>
          <h1>{copy.title}</h1>
          <p>{copy.description}</p>
        </header>

        {!taskId ? (
          <>
            <UploadZone initialFile={initialFile} onUploaded={handleUploaded} />
            <div className="workflow-action-row">
              <button
                className="button button--primary"
                disabled={!upload || creating}
                onClick={() => void createTask()}
                type="button"
              >
                {creating ? copy.creating : copy.create}
                {!creating && upload && <kbd className="shortcut-kbd">↵ Enter</kbd>}
              </button>
              <span>{copy.quotaNotice}</span>
            </div>
          </>
        ) : null}

        <TaskStatusPanel
          busy={cancelling}
          initialStatus={initialStatus}
          onCancel={() => void cancelTask()}
          task={task}
        />
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

        {inspection ? (
          <section
            aria-label={copy.realDataAriaLabel}
            className="result-panel"
          >
            <div className="panel-heading">
              <div>
                <span className="section-kicker">{copy.realDataKicker}</span>
                <h2>{copy.realDataTitle}</h2>
              </div>
              <span>
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
                <h3 id="fits-header-title">{copy.headerTitle}</h3>
                <dl className="fits-header-grid">
                  {Object.entries(inspection.header).map(([key, value]) => (
                    <div key={key}>
                      <dt>{key}</dt>
                      <dd>{String(value)}</dd>
                    </div>
                  ))}
                </dl>
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
        ) : null}

        {analysis ? (
          <section className="result-panel ai-analysis-panel">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">{copy.aiKicker}</span>
                <h2>{copy.aiTitle}</h2>
              </div>
              <span>{String(summary?.model ?? "kimi-k2.6")}</span>
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
              <div className="analysis-overview">
                <h3>{copy.overviewTitle}</h3>
                <p>{asString(analysis.overview)}</p>
                {imageQuality ? (
                  <dl className="analysis-quality">
                    <div>
                      <dt>{copy.qualityRating}</dt>
                      <dd>{copy.qualityLabels[asString(imageQuality.rating) ?? "fair"] ?? asString(imageQuality.rating)}</dd>
                    </div>
                    <div>
                      <dt>{copy.confidence}</dt>
                      <dd>
                        {typeof imageQuality.confidence === "number"
                          ? `${Math.round(imageQuality.confidence * 100)}%`
                          : zhCN.task11.common.unavailable}
                      </dd>
                    </div>
                  </dl>
                ) : null}
                <p>{asString(imageQuality?.summary)}</p>
              </div>
            </div>

            {observations ? (
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
                          <span className="tooltip-trigger" style={{ marginLeft: "4px" }}>
                            <svg fill="none" height="12" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" width="12" style={{ opacity: 0.5, verticalAlign: "middle" }}>
                              <circle cx="12" cy="12" r="10" />
                              <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
                              <line x1="12" x2="12.01" y1="17" y2="17" />
                            </svg>
                            <span className="tooltip-card">
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

            {issues.length > 0 ? (
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

            {workflow.length > 0 ? (
              <section className="analysis-section">
                <h3>{copy.workflowTitle}</h3>
                <ol className="analysis-workflow">
                  {workflow.map((step, index) => (
                    <li key={`${asString(step.step)}-${index}`}>
                      <h4>{asString(step.step)}</h4>
                      <p>{asString(step.purpose)}</p>
                      <strong>{asString(step.guidance)}</strong>
                    </li>
                  ))}
                </ol>
              </section>
            ) : null}

            {caveats.length > 0 ? (
              <section className="analysis-section analysis-caveats">
                <h3>{copy.caveatsTitle}</h3>
                <ul>
                  {caveats.map((caveat) => <li key={caveat}>{caveat}</li>)}
                </ul>
              </section>
            ) : null}
          </section>
        ) : null}

        {sourceValid && taskId ? (
          <div className="next-action">
            <div>
              <span className="section-kicker">{copy.continueKicker}</span>
              <h2>{copy.sourceValid}</h2>
            </div>
            <Link
              className="button button--primary"
              href={`/processing?source_task_id=${encodeURIComponent(taskId)}`}
            >
              {copy.processAction}
              <kbd className="shortcut-kbd">P</kbd>
            </Link>
          </div>
        ) : null}

        {taskId ? <TaskEventLog events={events} /> : null}
      </div>
    </main>
  );
}
