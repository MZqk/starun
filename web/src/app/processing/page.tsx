"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import ArtifactDownloads from "../../components/ArtifactDownloads";
import TaskEventLog from "../../components/TaskEventLog";
import TaskStatusPanel from "../../components/TaskStatusPanel";
import UploadZone from "../../components/UploadZone";
import { useTaskPolling } from "../../hooks/useTaskPolling";
import { getApiClient } from "../../lib/api/client";
import type {
  JsonObject,
  ProcessingStyle,
  TaskEventResponse,
  TaskStatus,
  UploadResponse,
} from "../../lib/api/types";
import { TaskHistoryRepository } from "../../lib/history/repository";
import { zhCN } from "../../lib/i18n/zh-CN";

const historyRepository = new TaskHistoryRepository();
const STYLE_VALUES: ProcessingStyle[] = ["realistic", "balanced", "artistic"];
const FALLBACK_AGENT_STEPS: Record<ProcessingStyle, readonly string[]> = {
  realistic: ["deep-sky-processor"],
  balanced: [
    "processing.prepare_reference",
    "processing.plan_art_direction",
    "processing.generate_artwork",
  ],
  artistic: ["kimi.art_direction", "tencent.hunyuan_image"],
};

function stringPayload(event: TaskEventResponse, key: string): string | null {
  const value = event.payload[key];
  return typeof value === "string" ? value : null;
}

function planSteps(
  events: TaskEventResponse[],
  style: ProcessingStyle,
): Array<{
  id: string;
  toolName: string;
}> {
  const steps = events.flatMap((event) => {
    if (event.event_type !== "agent_tool_started") return [];
    const toolName = stringPayload(event, "tool_name");
    if (!toolName) return [];
    return [
      {
        id: stringPayload(event, "step_id") ?? String(event.sequence),
        toolName,
      },
    ];
  });
  return steps.length > 0
    ? steps
    : FALLBACK_AGENT_STEPS[style].map((toolName, index) => ({
        id: String(index + 1).padStart(2, "0"),
        toolName,
      }));
}

function objectValue(value: unknown): JsonObject | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function queryValue(name: string): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get(name);
}

export default function ProcessingPage() {
  const copy = zhCN.task11.processing;
  const [style, setStyle] = useState<ProcessingStyle>("balanced");
  const [upload, setUpload] = useState<UploadResponse | null>(null);
  const [fileName, setFileName] = useState<string>(copy.unnamedFile);
  const [sourceTaskId, setSourceTaskId] = useState<string | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [initialStatus, setInitialStatus] = useState<TaskStatus | null>(null);
  const [creating, setCreating] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [localPersistenceError, setLocalPersistenceError] = useState<string | null>(null);
  const [currentTime, setCurrentTime] = useState(() => Date.now());
  const [artifactImages, setArtifactImages] = useState<{
    taskId: string;
    referenceUrl: string | null;
    resultUrl: string | null;
    referenceError: string | null;
    resultError: string | null;
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
    const resume = queryValue("task");
    const source = queryValue("source_task_id");
    let active = true;
    void (async () => {
      await Promise.resolve();
      if (source && active) {
        setSourceTaskId(source);
        setFileName(copy.analysisSourceFile);
      }
      if (!resume) return;
      const entry = await historyRepository.get(resume);
      if (!active) return;
      setTaskId(resume);
      if (!entry) return;
      setFileName(entry.fileName);
      setStyle(entry.style ?? "balanced");
      setInitialStatus(entry.lastStatus);
    })();
    return () => {
      active = false;
    };
  }, [copy.analysisSourceFile]);

  const summary = objectValue(task?.result.summary);
  const resultAvailable =
    task?.status === "completed" || task?.status === "review_required";
  const resultFresh =
    resultAvailable &&
    (task.expires_at === null || new Date(task.expires_at).getTime() > currentTime);
  const referenceArtifactName = resultFresh
    ? stringValue(summary?.reference_artifact) ??
      task!.result.artifacts.find((name) => name === "processing-reference.png") ??
      null
    : null;
  const resultArtifactName = resultFresh
    ? stringValue(summary?.result_artifact) ??
      task!.result.artifacts.find((name) => /^generated-artwork\.(png|jpe?g)$/i.test(name)) ??
      null
    : null;

  useEffect(() => {
    if (!taskId || (!referenceArtifactName && !resultArtifactName)) return;
    let active = true;
    const objectUrls: string[] = [];
    const controller = new AbortController();
    const load = async (name: string | null) => {
      if (!name) return { url: null, error: null };
      try {
        const artifact = await getApiClient().downloadArtifact(taskId, name, {
          signal: controller.signal,
        });
        const url = URL.createObjectURL(artifact.blob);
        objectUrls.push(url);
        return { url, error: null };
      } catch (caught) {
        if (controller.signal.aborted) return { url: null, error: null };
        return {
          url: null,
          error: caught instanceof Error ? caught.message : copy.previewError,
        };
      }
    };
    void Promise.all([
      load(referenceArtifactName),
      load(resultArtifactName),
    ]).then(([reference, result]) => {
      if (!active) return;
      setArtifactImages({
        taskId,
        referenceUrl: reference.url,
        resultUrl: result.url,
        referenceError: reference.error,
        resultError: result.error,
      });
    });
    return () => {
      active = false;
      controller.abort();
      objectUrls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [copy.previewError, referenceArtifactName, resultArtifactName, taskId]);

  useEffect(() => {
    if (!resultAvailable || task?.expires_at === null) return;
    const expiryTime = new Date(task.expires_at).getTime();
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
  }, [resultAvailable, task?.expires_at, task?.id]);

  const resultExpired =
    resultAvailable &&
    task.expires_at !== null &&
    new Date(task.expires_at).getTime() <= currentTime;

  useEffect(() => {
    if (!resultExpired || !taskId) return;
    void historyRepository.markExpired(taskId).catch((caught) => {
      setLocalPersistenceError(
        caught instanceof Error
          ? caught.message
          : zhCN.task11.common.historyPersistenceError,
      );
    });
  }, [resultExpired, taskId]);

  const handleUploaded = useCallback((nextUpload: UploadResponse, file: File) => {
    setUpload(nextUpload);
    setFileName(file.name);
    setSourceTaskId(null);
    setActionError(null);
  }, []);

  async function createTask() {
    if (!upload && !sourceTaskId) return;
    setCreating(true);
    setActionError(null);
    setLocalPersistenceError(null);
    let created;
    try {
      created = await getApiClient().createProcessingTask(
        sourceTaskId
          ? { source_task_id: sourceTaskId, style }
          : { upload_id: upload!.upload_id, style },
      );
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : copy.createError);
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
        style,
        lastStatus: created.status,
        createdAt: created.created_at,
        expiresAt: created.expires_at,
        summary: { demo: false },
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

  const resetToUpload = useCallback(() => {
    setTaskId(null);
    setUpload(null);
    setFileName(copy.unnamedFile);
    setSourceTaskId(null);
    setInitialStatus(null);
    setActionError(null);
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      url.searchParams.delete("task");
      url.searchParams.delete("source_task_id");
      window.history.replaceState({}, "", url.toString());
    }
  }, [copy.unnamedFile]);

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
          style,
          lastStatus: created.status,
          createdAt: created.created_at,
          expiresAt: created.expires_at,
          summary: { demo: false },
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
  }, [taskId, fileName, style]);

  const agentSteps = useMemo(() => planSteps(events, style), [events, style]);
  const inspection = objectValue(task?.inspection);
  const selectedHdu = objectValue(inspection?.selected_hdu);
  const statistics = objectValue(inspection?.statistics);
  const header = objectValue(inspection?.header);
  const shape = Array.isArray(selectedHdu?.shape)
    ? selectedHdu.shape.filter((item): item is number => typeof item === "number")
    : [];
  const activeImages = artifactImages?.taskId === taskId ? artifactImages : null;
  const sourceShape = shape.length > 0 ? shape.join(" × ") : "";
  const sourceRange = statistics
    ? `${String(statistics.minimum)}–${String(statistics.maximum)}`
    : "";
  const selectedStyle = copy.styles[style];
  const targetSummary = stringValue(summary?.target_summary);
  const artDirectionSummary = stringValue(summary?.art_direction_summary);
  const disclaimer = stringValue(summary?.disclaimer) ?? copy.disclaimer;

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
            {sourceTaskId ? (
              <section className="source-task-card">
                <span className="section-kicker">{copy.sourceKicker}</span>
                <h2>{sourceTaskId}</h2>
                <p>{copy.sourceDescription}</p>
              </section>
            ) : (
              <UploadZone onUploaded={handleUploaded} />
            )}

            <fieldset className="style-selector">
              <legend>{copy.styleLegend}</legend>
              <div role="radiogroup" aria-label={copy.styleAriaLabel}>
                {STYLE_VALUES.map((value) => (
                  <label key={value}>
                    <input
                      checked={style === value}
                      name="processing-style"
                      onChange={() => setStyle(value)}
                      type="radio"
                      value={value}
                    />
                    <span>
                      <strong>{copy.styles[value].label}</strong>
                      <small>{copy.styles[value].description}</small>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>

            <div className="workflow-action-row">
              <button
                className="button button--primary"
                disabled={(!upload && !sourceTaskId) || creating}
                onClick={() => void createTask()}
                type="button"
              >
                {creating ? copy.creating : copy.create}
              </button>
              <span>{copy.styleNotice}</span>
            </div>
          </>
        ) : null}

        <TaskStatusPanel
          busy={cancelling}
          initialStatus={initialStatus}
          onCancel={() => void cancelTask()}
          task={task}
        />

        {((task && task.status === "failed") || (!task && initialStatus === "failed")) && (
          <div className="recovery-panel">
            <div className="recovery-header">
              <span className="recovery-badge">FAULT_RECOVERY_ENGAGED</span>
              <h3>图像处理任务未成功</h3>
            </div>
            <p className="recovery-desc">
              AI 自动出图任务执行未成功。此任务对应的处理算力节点可能暂时离线或生成超时。您可以重新上传文件、携带原分析结果，或者点击重试当前处理任务。
            </p>
            <div className="recovery-actions">
              <button
                className="button button--secondary"
                onClick={resetToUpload}
                type="button"
              >
                重新上传文件
              </button>
              <button
                className="button button--primary"
                disabled={retrying}
                onClick={() => void retryCurrentTask()}
                type="button"
              >
                {retrying ? "正在重新排队..." : "重试出图任务"}
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

        {taskId ? (
          <section aria-label={copy.planAriaLabel} className="agent-plan">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">{copy.planLabel}</span>
                <h2>{copy.planTitle}</h2>
              </div>
              <span>
                {events.some((event) => event.event_type === "agent_tool_started")
                  ? copy.planUpdated
                  : copy.planWaiting}
              </span>
            </div>
            <ol>
              {agentSteps.map((step) => (
                <li key={`${step.id}-${step.toolName}`}>
                  <span>{step.id.padStart(2, "0")}</span>
                  {copy.toolNames[step.toolName] ?? step.toolName}
                </li>
              ))}
            </ol>
          </section>
        ) : null}

        {taskId ? <TaskEventLog events={events} /> : null}

        {resultAvailable && !resultExpired ? (
          <>
            <section className="comparison-panel" aria-label={copy.comparisonAriaLabel}>
              <div className="comparison-frame comparison-frame--before">
                <span>{copy.before}</span>
                {activeImages?.referenceUrl ? (
                  <div
                    aria-label={copy.sourcePreviewAriaLabel}
                    className="demo-preview-image"
                    role="img"
                    style={{ backgroundImage: `url("${activeImages.referenceUrl}")` }}
                  />
                ) : (
                  <div className="demo-preview-placeholder" aria-live="polite">
                    {activeImages?.referenceError ?? copy.previewLoading}
                  </div>
                )}
                <strong>
                  {typeof header?.OBJECT === "string" ? header.OBJECT : copy.rawFits}
                </strong>
                <small>
                  {copy.sourceMetadata(
                    String(selectedHdu?.index ?? task.selected_hdu ?? 0),
                    sourceShape,
                    sourceRange,
                  )}
                </small>
              </div>
              <div className="comparison-frame comparison-frame--after">
                <span className="section-kicker">{copy.previewLabel}</span>
                {activeImages?.resultUrl ? (
                  <div
                    aria-label={copy.previewAriaLabel}
                    className="demo-preview-image"
                    role="img"
                    style={{ backgroundImage: `url("${activeImages.resultUrl}")` }}
                  />
                ) : (
                  <div className="demo-preview-placeholder" aria-live="polite">
                    {activeImages?.resultError ?? copy.previewLoading}
                  </div>
                )}
                <strong>{selectedStyle.label}</strong>
                <small>{targetSummary ?? disclaimer}</small>
              </div>
            </section>
            {artDirectionSummary ? (
              <section className="source-task-card">
                <span className="section-kicker">{copy.directionKicker}</span>
                <h2>{copy.directionTitle}</h2>
                <p>{artDirectionSummary}</p>
                <small>{disclaimer}</small>
              </section>
            ) : null}
            <ArtifactDownloads
              artifacts={task.result.artifacts}
              label={copy.downloadLabel}
              taskId={task.id}
            />
          </>
        ) : null}
        {resultExpired ? (
          <section className="empty-state" role="status">
            <h2>{copy.expiredTitle}</h2>
            <p>{copy.resultUnavailable}</p>
          </section>
        ) : null}
      </div>
    </main>
  );
}
