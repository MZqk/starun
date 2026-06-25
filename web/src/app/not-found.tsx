"use client";

import Link from "next/link";
import FuzzyText from "../components/FuzzyText";

export default function NotFound() {
  return (
    <main aria-label="找不到页面" className="not-found-page">
      <div className="not-found-container">

        {/* Fuzzy 404 — 品牌主文字色 + 红色光晕 */}
        <div className="not-found-number">
          <FuzzyText
            ariaLabel="错误代码 404"
            baseIntensity={0.18}
            hoverIntensity={0.55}
            enableHover={true}
            glitchMode={true}
            glitchInterval={4000}
            glitchDuration={150}
            transitionDuration={10}
            color="#e8e4dd"
            fontSize="clamp(4rem, 14vw, 9rem)"
            fontWeight={900}
          >
            404
          </FuzzyText>
        </div>

        {/* 副标题 — h1 承担 heading 层级语义 */}
        <h1 className="not-found-subtitle">页面已坠入星际深处</h1>

        {/* 描述 */}
        <p className="not-found-desc">
          你访问的页面不存在，它可能已被移除或地址输入有误。
        </p>

        {/* 返回按钮 — 复用项目 nav-upload 样式语言 */}
        <Link href="/" className="not-found-btn">
          <span className="not-found-btn-icon" aria-hidden="true">←</span>
          返回主页
        </Link>

        {/* 辅助恢复路径 */}
        <div className="not-found-links">
          <Link href="/analysis" className="not-found-link">
            专业分析
          </Link>
          <span className="not-found-divider" aria-hidden="true">·</span>
          <Link href="/processing" className="not-found-link">
            AI 自动出图
          </Link>
          <span className="not-found-divider" aria-hidden="true">·</span>
          <Link href="/history" className="not-found-link">
            历史记录
          </Link>
        </div>
      </div>
    </main>
  );
}
