"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import MockNotice from "../../components/MockNotice";
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

const historyRepository = new TaskHistoryRepository();

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function resumedTaskId(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("task");
}

export default function AnalysisPage() {
  const copy = zhCN.task11.analysis;
  const [upload, setUpload] = useState<UploadResponse | null>(null);
  const [fileName, setFileName] = useState<string>(copy.unnamedFile);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [initialStatus, setInitialStatus] = useState<TaskStatus | null>(null);
  const [creating, setCreating] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [localPersistenceError, setLocalPersistenceError] = useState<
    string | null
  >(null);
  const [currentTime, setCurrentTime] = useState(() => Date.now());
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
  const metrics = asRecord(summary?.professional_metrics);
  const recommendations = Array.isArray(summary?.recommendations)
    ? summary.recommendations.filter(
        (item): item is string => typeof item === "string",
      )
    : [];
  const sourceValid =
    task?.status === "completed" &&
    task.expires_at !== null &&
    new Date(task.expires_at).getTime() > currentTime;

  return (
    <main className="workflow-main">
      <div className="page-shell workflow-shell">
        <header className="workflow-hero">
          <span className="section-kicker">{copy.kicker}</span>
          <h1>{copy.title}</h1>
          <p>{copy.description}</p>
        </header>

        <MockNotice />

        {!taskId ? (
          <>
            <UploadZone onUploaded={handleUploaded} />
            <div className="workflow-action-row">
              <button
                className="button button--primary"
                disabled={!upload || creating}
                onClick={() => void createTask()}
                type="button"
              >
                {creating ? copy.creating : copy.create}
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

        {metrics ? (
          <section className="result-panel result-panel--mock">
            <div className="panel-heading">
              <div>
                <span className="mock-label">{zhCN.task11.common.mock}</span>
                <h2>{copy.mockMetrics}</h2>
              </div>
              <span>{copy.notScientific}</span>
            </div>
            <dl className="metric-cards">
              {Object.entries(metrics).map(([name, value]) => (
                <div key={name}>
                  <dt>{name.replaceAll("_", " ")}</dt>
                  <dd>{String(value)}</dd>
                </div>
              ))}
            </dl>
          </section>
        ) : null}

        {recommendations.length > 0 ? (
          <section className="result-panel result-panel--mock">
            <span className="mock-label">{zhCN.task11.common.mock}</span>
            <h2>{copy.mockRecommendations}</h2>
            <ul className="recommendation-list">
              {recommendations.map((recommendation) => (
                <li key={recommendation}>{recommendation}</li>
              ))}
            </ul>
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
            </Link>
          </div>
        ) : null}

        {taskId ? <TaskEventLog events={events} /> : null}
      </div>
    </main>
  );
}
