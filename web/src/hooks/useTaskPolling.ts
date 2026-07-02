"use client";

import { useCallback, useEffect, useState } from "react";
import { getApiClient } from "../lib/api/client";
import { StarunApiError } from "../lib/api/errors";
import type {
  JsonObject,
  TaskDetailResponse,
  TaskEventResponse,
  TaskStatus,
} from "../lib/api/types";
import { TaskHistoryRepository } from "../lib/history/repository";
import { zhCN } from "../lib/i18n/zh-CN";

const TERMINAL_STATUSES = new Set<TaskStatus>([
  "cancelled",
  "completed",
  "review_required",
  "failed",
  "expired",
]);
const historyRepository = new TaskHistoryRepository();

function historySummary(task: TaskDetailResponse): JsonObject {
  return {
    ...(task.result.summary ?? {}),
    cleanupPending: task.cleanup_pending,
    errorCode: task.error_code,
    retryable: task.retryable,
  };
}

interface PollingState {
  taskId: string | null;
  task: TaskDetailResponse | null;
  events: TaskEventResponse[];
  error: Error | null;
  persistenceError: Error | null;
  loading: boolean;
}

export interface TaskPollingResult {
  task: TaskDetailResponse | null;
  events: TaskEventResponse[];
  error: Error | null;
  persistenceError: Error | null;
  loading: boolean;
  refresh: () => void;
}

export function useTaskPolling(taskId: string | null): TaskPollingResult {
  const [state, setState] = useState<PollingState>({
    taskId,
    task: null,
    events: [],
    error: null,
    persistenceError: null,
    loading: Boolean(taskId),
  });
  const [refreshVersion, setRefreshVersion] = useState(0);

  const refresh = useCallback(() => {
    setRefreshVersion((version) => version + 1);
  }, []);

  useEffect(() => {
    if (!taskId) {
      return;
    }

    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let controller: AbortController | null = null;
    let requestTimeout: ReturnType<typeof setTimeout> | null = null;
    let latestSequence = 0;
    let timedOut = false;
    const queuedSince = Date.now();
    const api = getApiClient();

    queueMicrotask(() => {
      if (!active) return;
      setState({
        taskId,
        task: null,
        events: [],
        error: null,
        persistenceError: null,
        loading: true,
      });
    });

    const reportPersistenceFailure = (caught: unknown) => {
      if (!active) return;
      const persistenceError =
        caught instanceof Error
          ? caught
          : new Error(zhCN.task11.common.historyPersistenceError);
      setState((current) =>
        current.taskId === taskId
          ? { ...current, persistenceError }
          : current,
      );
    };

    const persistTask = (nextTask: TaskDetailResponse) => {
      void historyRepository
        .updateStatus(taskId, nextTask.status, {
          expiresAt: nextTask.expires_at,
          resultAvailable:
            (nextTask.status === "completed" ||
              nextTask.status === "review_required") &&
            nextTask.result.manifest_available,
          summary: historySummary(nextTask),
        })
        .then(() => {
          if (!active) return;
          setState((current) =>
            current.taskId === taskId
              ? { ...current, persistenceError: null }
              : current,
          );
        })
        .catch(reportPersistenceFailure);
    };

    const schedule = (status: TaskStatus) => {
      if (!active || TERMINAL_STATUSES.has(status)) {
        return;
      }
      const delay =
        status === "queued" && Date.now() - queuedSince >= 30_000
          ? 5_000
          : 2_000;
      timer = setTimeout(poll, delay);
    };

    const poll = async () => {
      controller?.abort();
      if (requestTimeout) clearTimeout(requestTimeout);

      controller = new AbortController();
      const currentController = controller;
      timedOut = false;

      // 2. 单次请求超时 (15秒)
      requestTimeout = setTimeout(() => {
        if (currentController === controller) {
          timedOut = true;
          controller.abort();
        }
      }, 15_000);

      try {
        const [nextTask, eventPage] = await Promise.all([
          api.getTask(taskId, { signal: currentController.signal }),
          api.getTaskEvents(taskId, latestSequence, {
            signal: currentController.signal,
          }),
        ]);
        
        if (requestTimeout) clearTimeout(requestTimeout);

        if (!active || currentController.signal.aborted) {
          return;
        }

        let nextEvents = eventPage.events;
        let cursor = eventPage.next_after;
        let hasMore = eventPage.has_more;
        
        while (hasMore && active && !currentController.signal.aborted) {
          if (requestTimeout) clearTimeout(requestTimeout);
          requestTimeout = setTimeout(() => {
            if (currentController === controller) {
              timedOut = true;
              controller.abort();
            }
          }, 15_000);

          const page = await api.getTaskEvents(taskId, cursor, {
            signal: currentController.signal,
          });
          
          if (requestTimeout) clearTimeout(requestTimeout);
          nextEvents = nextEvents.concat(page.events);
          cursor = page.next_after;
          hasMore = page.has_more;
        }
        
        if (!active || currentController.signal.aborted) {
          return;
        }

        latestSequence = cursor;
        setState((current) => {
          if (current.taskId !== taskId) {
            return current;
          }
          const known = new Set(current.events.map((event) => event.sequence));
          return {
            ...current,
            task: nextTask,
            events: current.events.concat(
              nextEvents.filter((event) => !known.has(event.sequence)),
            ),
            error: null,
            loading: false,
          };
        });
        schedule(nextTask.status);
        persistTask(nextTask);
      } catch (caught) {
        if (requestTimeout) clearTimeout(requestTimeout);

        if (!active) {
          return;
        }
        
        if (currentController.signal.aborted && !timedOut) {
          return;
        }

        const nextError = timedOut
          ? new Error(
              zhCN.task11.common.taskPollingTimeout ||
                "任务状态同步暂时变慢，任务会继续运行。"
            )
          : (caught instanceof Error
              ? caught
              : new Error(zhCN.task11.common.taskPollingError));

        if (timedOut) {
          nextError.name = "PollingSyncTimeoutError";
        }

        setState((current) =>
          current.taskId === taskId
            ? { ...current, error: nextError, loading: false }
            : current,
        );

        if (
          nextError instanceof StarunApiError &&
          (nextError.status === 410 || nextError.errorCode === "task_expired")
        ) {
          void historyRepository
            .markExpired(taskId)
            .catch(reportPersistenceFailure);
          return;
        }
        timer = setTimeout(poll, 5_000);
      }
    };

    void poll();

    return () => {
      active = false;
      if (timer) clearTimeout(timer);
      if (requestTimeout) clearTimeout(requestTimeout);
      controller?.abort();
    };
  }, [taskId, refreshVersion]);

  if (state.taskId !== taskId) {
    return {
      task: null,
      events: [],
      error: null,
      persistenceError: null,
      loading: Boolean(taskId),
      refresh,
    };
  }

  return { ...state, refresh };
}
