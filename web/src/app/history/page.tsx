"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getApiClient } from "../../lib/api/client";
import { StarunApiError } from "../../lib/api/errors";
import { TaskHistoryRepository } from "../../lib/history/repository";
import type { TaskHistorySummary } from "../../lib/history/types";
import { zhCN } from "../../lib/i18n/zh-CN";

const historyRepository = new TaskHistoryRepository();

function isExpired(entry: TaskHistorySummary, now: number): boolean {
  return (
    entry.lastStatus === "expired" ||
    (entry.expiresAt !== null &&
      new Date(entry.expiresAt).getTime() <= now)
  );
}

function summaryFlag(entry: TaskHistorySummary, key: string): unknown {
  if (
    typeof entry.summary !== "object" ||
    entry.summary === null ||
    Array.isArray(entry.summary)
  ) {
    return undefined;
  }
  return entry.summary[key];
}

function canRetry(entry: TaskHistorySummary, now: number): boolean {
  if (
    entry.lastStatus !== "failed" ||
    entry.expiresAt === null ||
    new Date(entry.expiresAt).getTime() <= now
  ) {
    return false;
  }
  return (
    summaryFlag(entry, "retryable") === true &&
    summaryFlag(entry, "cleanupPending") !== true
  );
}

function getHash(str: string): number {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash);
  }
  return Math.abs(hash);
}

function TaskThumbnail({ taskId }: { taskId: string }) {
  const hash = getHash(taskId);
  const hue1 = hash % 360;
  const hue2 = (hue1 + 120) % 360;
  const sat1 = 55 + (hash % 20);
  const sat2 = 45 + (hash % 20);
  const light1 = 18 + (hash % 6);
  const light2 = 14 + (hash % 6);
  
  const star1X = 15 + (hash % 30);
  const star1Y = 20 + ((hash >> 2) % 30);
  const star2X = 55 + ((hash >> 4) % 30);
  const star2Y = 45 + ((hash >> 6) % 30);
  const star3X = 25 + ((hash >> 8) % 45);
  const star3Y = 65 + ((hash >> 10) % 25);

  const bgStyle = {
    background: `
      radial-gradient(circle at ${star1X}% ${star1Y}%, hsla(${hue1}, ${sat1}%, ${light1}%, 0.65), transparent 65%),
      radial-gradient(circle at ${star2X}% ${star2Y}%, hsla(${hue2}, ${sat2}%, ${light2}%, 0.5), transparent 60%),
      #08080a
    `
  };

  return (
    <div className="task-thumbnail" style={bgStyle} aria-hidden="true">
      <div className="task-thumbnail__star" style={{ left: `${star1X}%`, top: `${star1Y}%`, opacity: 0.8 }} />
      <div className="task-thumbnail__star" style={{ left: `${star2X}%`, top: `${star2Y}%`, opacity: 0.9, width: '2px', height: '2px' }} />
      <div className="task-thumbnail__star" style={{ left: `${star3X}%`, top: `${star3Y}%`, opacity: 0.7 }} />
      <div className="task-thumbnail__grid" />
    </div>
  );
}

export default function HistoryPage() {
  const copy = zhCN.task11.history;
  const [entries, setEntries] = useState<TaskHistorySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [localPersistenceError, setLocalPersistenceError] = useState<
    string | null
  >(null);
  const [currentTime, setCurrentTime] = useState(() => Date.now());

  useEffect(() => {
    let active = true;
    void historyRepository
      .list()
      .then((items) => {
        if (active) setEntries(items);
      })
      .catch((caught) => {
        if (active) {
          setError(
            caught instanceof Error ? caught.message : copy.loadError,
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [copy.loadError]);

  useEffect(() => {
    const nextExpiry = entries.reduce<number | null>((nearest, entry) => {
      if (entry.expiresAt === null) return nearest;
      const expiry = new Date(entry.expiresAt).getTime();
      if (expiry <= currentTime) return nearest;
      return nearest === null || expiry < nearest ? expiry : nearest;
    }, null);
    if (nextExpiry === null) return;
    const timer = setTimeout(
      () => setCurrentTime(Date.now()),
      Math.min(nextExpiry - Date.now() + 1, 2_147_000_000),
    );
    return () => clearTimeout(timer);
  }, [currentTime, entries]);

  async function retry(entry: TaskHistorySummary) {
    setBusyId(entry.taskId);
    setError(null);
    setLocalPersistenceError(null);
    let created;
    try {
      created = await getApiClient().retryTask(entry.taskId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : copy.retryError);
      setBusyId(null);
      return;
    }

    const nextEntry: TaskHistorySummary = {
      taskId: created.task_id,
      type: created.type,
      fileName: entry.fileName,
      style: created.style,
      lastStatus: created.status,
      createdAt: created.created_at,
      expiresAt: created.expires_at,
      summary: null,
      resultAvailable: false,
      updatedAt: created.created_at,
    };
    setEntries((current) => [
      nextEntry,
      ...current.filter((item) => item.taskId !== nextEntry.taskId),
    ]);
    try {
      await historyRepository.upsert({
        ...nextEntry,
      });
    } catch (caught) {
      setLocalPersistenceError(
        caught instanceof Error
          ? caught.message
          : zhCN.task11.common.historyPersistenceError,
      );
    } finally {
      setBusyId(null);
    }
  }

  async function remove(entry: TaskHistorySummary) {
    setBusyId(entry.taskId);
    setError(null);
    setLocalPersistenceError(null);
    try {
      await getApiClient().deleteTask(entry.taskId);
    } catch (caught) {
      if (
        !(caught instanceof StarunApiError) ||
        (caught.status !== 404 && caught.status !== 410)
      ) {
        setError(caught instanceof Error ? caught.message : copy.deleteError);
        setBusyId(null);
        return;
      }
    }

    setEntries((current) =>
      current.filter((item) => item.taskId !== entry.taskId),
    );
    try {
      await historyRepository.remove(entry.taskId);
    } catch (caught) {
      setLocalPersistenceError(
        caught instanceof Error
          ? caught.message
          : zhCN.task11.common.historyPersistenceError,
      );
    } finally {
      setBusyId(null);
    }
  }

  return (
    <main className="workflow-main">
      <div className="page-shell workflow-shell">
        <header className="workflow-hero">
          <span className="section-kicker">{copy.kicker}</span>
          <h1>{copy.title}</h1>
          <p>{copy.description}</p>
        </header>

        <aside className="history-notice" role="note">
          {copy.durabilityNotice}
        </aside>

        {loading ? <p className="empty-copy">{copy.loading}</p> : null}
        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}
        {localPersistenceError ? (
          <p className="storage-warning" role="status">
            {zhCN.task11.common.storageWarning(localPersistenceError)}
          </p>
        ) : null}
        {!loading && entries.length === 0 ? (
          <section className="empty-state">
            <div className="border-mask" aria-hidden="true" />
            <h2>{copy.emptyTitle}</h2>
            <p>{copy.emptyDescription}</p>
            <div className="empty-state__actions">
              <Link className="button button--primary" href="/analysis">
                {copy.upload}
              </Link>
              <Link className="button button--secondary" href="/processing">
                {copy.processing}
              </Link>
            </div>
          </section>
        ) : null}

        <div className="history-list">
          {entries.map((entry) => {
            const expired = isExpired(entry, currentTime);
            const destination =
              entry.type === "analysis" ? "/analysis" : "/processing";
            return (
              <article className="history-card" key={entry.taskId}>
                <div className="border-mask" aria-hidden="true" />
                <TaskThumbnail taskId={entry.taskId} />
                <div className="history-card__content">
                  <div className="history-card__header">
                    <div>
                      <span className="section-kicker">
                        {entry.type === "analysis"
                          ? copy.analysisType
                          : copy.processingType}
                      </span>
                      <h2>{entry.fileName}</h2>
                      <div className="history-card__id-row">
                        <span className="history-card__id">ID: #{entry.taskId.slice(-8)}</span>
                        <button
                          className="copy-id-btn"
                          onClick={() => {
                            void navigator.clipboard.writeText(entry.taskId).catch(() => {});
                          }}
                          title="复制完整 Task ID"
                          type="button"
                        >
                          <svg fill="none" height="12" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" width="12">
                            <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
                            <rect height="4" rx="1" ry="1" width="8" x="8" y="2" />
                          </svg>
                        </button>
                      </div>
                    </div>
                    <span className={`history-status history-status--${entry.lastStatus}`}>
                      {expired
                        ? zhCN.task11.status.labels.expired
                        : zhCN.task11.status.labels[entry.lastStatus]}
                    </span>
                  </div>
                  <dl>
                    <div>
                      <dt>{copy.createdAt}</dt>
                      <dd>{new Date(entry.createdAt).toLocaleString("zh-CN")}</dd>
                    </div>
                    <div>
                      <dt>{copy.style}</dt>
                      <dd>
                        {entry.style
                          ? zhCN.task11.processing.styles[entry.style].label
                          : zhCN.task11.common.notApplicable}
                      </dd>
                    </div>
                    <div>
                      <dt>{copy.result}</dt>
                      <dd>
                        {expired || !entry.resultAvailable
                          ? copy.resultUnavailable
                          : copy.resultAvailable}
                      </dd>
                    </div>
                  </dl>
                  <div className="history-card__actions">
                    {!expired ? (
                      <Link
                        className="button button--secondary"
                        href={`${destination}?task=${encodeURIComponent(entry.taskId)}`}
                      >
                        {copy.open}
                      </Link>
                    ) : null}
                    {canRetry(entry, currentTime) ? (
                      <button
                        className="button button--secondary"
                        disabled={busyId === entry.taskId}
                        onClick={() => void retry(entry)}
                        type="button"
                      >
                        {copy.retry}
                      </button>
                    ) : null}
                    <button
                      className="text-button text-button--danger"
                      disabled={busyId === entry.taskId}
                      onClick={() => void remove(entry)}
                      type="button"
                    >
                      {copy.remove}
                    </button>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      </div>
    </main>
  );
}
