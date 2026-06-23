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
  return (
    <section className="event-log" aria-labelledby="event-log-title">
      <div className="border-mask" aria-hidden="true" />
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
