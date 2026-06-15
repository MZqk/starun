import { zhCN } from "../lib/i18n/zh-CN";
import { InfoIcon } from "./Icons";

type MockNoticeProps = {
  compact?: boolean;
};

export default function MockNotice({ compact = false }: MockNoticeProps) {
  return (
    <aside
      aria-label={zhCN.home.mockNotice.title}
      className={compact ? "mock-notice mock-notice--compact" : "mock-notice"}
      role="note"
    >
      <InfoIcon />
      <div>
        <strong>{zhCN.home.mockNotice.title}</strong>
        <p>{zhCN.home.mockNotice.body}</p>
      </div>
    </aside>
  );
}
