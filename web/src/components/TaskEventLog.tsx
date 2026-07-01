"use client";

import { useEffect, useRef, useState } from "react";
import type { TaskEventResponse } from "../lib/api/types";
import { zhCN } from "../lib/i18n/zh-CN";

type TaskEventLogProps = {
  demo?: boolean;
  events: TaskEventResponse[];
};

function eventTitle(event: TaskEventResponse): string {
  return event.event_type
    .replace(/^agent_/, "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export default function TaskEventLog({
  demo = false,
  events,
}: TaskEventLogProps) {
  const copy = zhCN.task11.events;
  const processingCopy = zhCN.task11.processing;
  const [announcement, setAnnouncement] = useState("");
  const lastAnnouncementRef = useRef<string | null>(null);

  useEffect(() => {
    if (events.length === 0) return;
    const latestEvent = events[events.length - 1];
    let text = "";

    const payload = latestEvent.payload as Record<string, unknown>;
    const toolName = typeof payload.tool_name === "string" ? payload.tool_name : "";
    const translatedTool = processingCopy.toolNames[toolName] || toolName;

    if (latestEvent.event_type === "agent_tool_started") {
      text = `开始处理：${translatedTool}`;
    } else if (latestEvent.event_type === "agent_tool_finished") {
      text = `完成处理：${translatedTool}`;
    } else if (latestEvent.event_type === "task_completed") {
      text = "图像处理已完成";
    } else if (latestEvent.event_type === "task_failed") {
      text = "图像处理没有完成";
    }

    if (text && text !== lastAnnouncementRef.current) {
      lastAnnouncementRef.current = text;
      setAnnouncement(text);
    }
  }, [events, processingCopy.toolNames]);

  return (
    <section className="event-log" aria-labelledby="event-log-title">
      <div className="border-mask" aria-hidden="true" />
      
      {/* 专供屏幕阅读器朗读的低频降噪实时播报区 */}
      <div
        aria-live="polite"
        style={{
          position: "absolute",
          width: "1px",
          height: "1px",
          padding: 0,
          margin: "-1px",
          overflow: "hidden",
          clip: "rect(0, 0, 0, 0)",
          whiteSpace: "nowrap",
          border: 0,
        }}
      >
        {announcement}
      </div>

      <div className="panel-heading">
        <div>
          <span className={demo ? "mock-label" : "section-kicker"}>
            {demo ? copy.mockLabel : copy.liveLabel}
          </span>
          <h2 id="event-log-title">{copy.title}</h2>
        </div>
        <span>{copy.count(events.length)}</span>
      </div>
      {events.length === 0 ? (
        <p className="empty-copy">{copy.empty}</p>
      ) : (
        <ol>
          {events.map((event) => (
            <li key={event.sequence}>
              <span>{String(event.sequence).padStart(2, "0")}</span>
              <div>
                <strong>{eventTitle(event)}</strong>
                <time dateTime={event.created_at}>
                  {new Date(event.created_at).toLocaleTimeString("zh-CN")}
                </time>
                {Object.keys(event.payload).length > 0 ? (
                  <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
