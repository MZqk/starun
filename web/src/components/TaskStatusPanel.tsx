"use client";

import { useEffect, useState } from "react";
import type { TaskDetailResponse, TaskStatus } from "../lib/api/types";
import { zhCN } from "../lib/i18n/zh-CN";

type TaskStatusPanelProps = {
  busy?: boolean;
  initialStatus?: TaskStatus | null;
  onCancel?: () => void;
  task: TaskDetailResponse | null;
};

function remainingSeconds(expiresAt: string, now: number): number {
  return Math.max(0, Math.ceil((new Date(expiresAt).getTime() - now) / 1000));
}

export default function TaskStatusPanel({
  busy = false,
  initialStatus = null,
  onCancel,
  task,
}: TaskStatusPanelProps) {
  const copy = zhCN.task11.status;
  const status = task?.status ?? initialStatus;
  const [currentTime, setCurrentTime] = useState(() => Date.now());

  useEffect(() => {
    if (
      !task ||
      !["completed", "review_required"].includes(task.status) ||
      task.expires_at === null
    ) {
      return;
    }
    const expiryTime = new Date(task.expires_at).getTime();
    const initialTime = Date.now();
    let active = true;
    queueMicrotask(() => {
      if (active) {
        setCurrentTime(initialTime);
      }
    });
    if (expiryTime <= initialTime) {
      return () => {
        active = false;
      };
    }
    const timer = setInterval(() => {
      const now = Date.now();
      setCurrentTime(now);
      if (now >= expiryTime) {
        clearInterval(timer);
      }
    }, 1_000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [task]);

  if (!status) {
    return null;
  }
  const cancellable = status === "queued" || status === "running";
  const completedExpiry =
    task &&
    ["completed", "review_required"].includes(task.status) &&
    task.expires_at !== null
      ? {
          formatted: new Date(task.expires_at).toLocaleString("zh-CN", {
            hour12: false,
          }),
          seconds: remainingSeconds(task.expires_at, currentTime),
        }
      : null;

  return (
    <section className="task-status-panel">
      <div className="border-mask" aria-hidden="true" />
      <div>
        <span className={`status-dot status-dot--${status}`} aria-hidden="true" />
        <div aria-atomic="true" aria-live="polite">
          <span className="section-kicker">{copy.kicker}</span>
          <h2>{copy.labels[status]}</h2>
        </div>
      </div>
      <div className="task-status-panel__progress">
        <span>{task?.stage ?? copy.waiting}</span>
        <strong>{task?.progress ?? 0}%</strong>
        <progress max={100} value={task?.progress ?? 0}>
          {task?.progress ?? 0}%
        </progress>
      </div>
      {completedExpiry ? (
        <dl className="task-terminal-details">
          <div>
            <dt>{copy.expiryLabel}</dt>
            <dd>{completedExpiry.formatted}</dd>
          </div>
          <div>
            <dt>{copy.remainingLabel}</dt>
            <dd>
              {completedExpiry.seconds > 0
                ? copy.remaining(completedExpiry.seconds)
                : copy.expiredNow}
            </dd>
          </div>
        </dl>
      ) : null}
      {task?.status === "failed" ? (
        <dl className="task-terminal-details">
          <div>
            <dt>{copy.errorCode}</dt>
            <dd>{task.error_code ?? zhCN.task11.common.unavailable}</dd>
          </div>
          <div>
            <dt>{copy.retryability}</dt>
            <dd>{task.retryable ? copy.retryable : copy.notRetryable}</dd>
          </div>
          <div>
            <dt>{copy.quota}</dt>
            <dd>
              {task.quota_charged ? copy.quotaCharged : copy.quotaNotCharged}
            </dd>
          </div>
        </dl>
      ) : null}
      {task?.message ? (
        <p className="form-error" role="alert">
          {task.message}
        </p>
      ) : null}
      {cancellable && onCancel ? (
        <button
          className="button button--secondary"
          disabled={busy}
          onClick={onCancel}
          type="button"
        >
          {busy ? copy.cancelling : copy.cancel}
          {!busy && (
            <kbd aria-hidden="true" className="shortcut-kbd">
              Esc
            </kbd>
          )}
        </button>
      ) : null}
    </section>
  );
}
