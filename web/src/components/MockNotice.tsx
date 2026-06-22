"use client";

import { useEffect, useState } from "react";
import { zhCN } from "../lib/i18n/zh-CN";
import { InfoIcon } from "./Icons";

type MockNoticeProps = {
  compact?: boolean;
};

export default function MockNotice({ compact = false }: MockNoticeProps) {
  const [hidden, setHidden] = useState(true);

  useEffect(() => {
    const isHidden = localStorage.getItem("starun_mock_notice_hidden") === "true";
    if (!isHidden) {
      setHidden(false);
    }
  }, []);

  const handleClose = () => {
    localStorage.setItem("starun_mock_notice_hidden", "true");
    setHidden(true);
  };

  if (hidden) {
    return null;
  }

  return (
    <aside
      aria-label={zhCN.home.mockNotice.title}
      className={compact ? "mock-notice mock-notice--compact" : "mock-notice"}
      role="note"
    >
      <InfoIcon />
      <div className="mock-notice__content">
        <strong>{zhCN.home.mockNotice.title}</strong>
        <p>{zhCN.home.mockNotice.body}</p>
      </div>
      <button
        aria-label="关闭公告"
        className="mock-notice__close"
        onClick={handleClose}
        type="button"
      >
        <svg fill="none" height="14" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" width="14">
          <line x1="18" x2="6" y1="6" y2="18" />
          <line x1="6" x2="18" y1="6" y2="18" />
        </svg>
      </button>
    </aside>
  );
}

