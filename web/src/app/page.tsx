"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import FeatureCard from "../components/FeatureCard";
import {
  ArrowIcon,
  HistoryIcon,
  OrbitIcon,
  SparkIcon,
  UploadIcon,
} from "../components/Icons";
import { zhCN } from "../lib/i18n/zh-CN";
import { fileTransfer } from "../lib/transfer";

export default function HomePage() {
  const { home } = zhCN;
  const router = useRouter();
  const [isDragging, setIsDragging] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (/\.(fits|fit|fts|xisf)$/i.test(file.name)) {
      setUploadError(null);
      fileTransfer.set(file);
      router.push("/analysis");
    } else {
      setUploadError("ERR_INVALID_FILE_TYPE");
      if (e.target) e.target.value = "";
      setTimeout(() => setUploadError(null), 4000);
    }
  };

  const handleUploadCardClick = () => {
    fileInputRef.current?.click();
  };

  useEffect(() => {
    let dragCounter = 0;

    const handleDragEnter = (e: DragEvent) => {
      e.preventDefault();
      dragCounter++;
      if (e.dataTransfer?.items && e.dataTransfer.items.length > 0) {
        setIsDragging(true);
      }
    };

    const handleDragLeave = (e: DragEvent) => {
      e.preventDefault();
      dragCounter--;
      if (dragCounter === 0) {
        setIsDragging(false);
      }
    };

    const handleDragOver = (e: DragEvent) => {
      e.preventDefault();
    };

    const handleDrop = (e: DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      dragCounter = 0;

      const file = e.dataTransfer?.files?.[0];
      if (!file) return;
      if (/\.(fits|fit|fts|xisf)$/i.test(file.name)) {
        setUploadError(null);
        fileTransfer.set(file);
        router.push("/analysis");
      } else {
        setUploadError("ERR_INVALID_FILE_TYPE");
        setTimeout(() => setUploadError(null), 4000);
      }
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setIsDragging(false);
        dragCounter = 0;
      }
    };

    window.addEventListener("dragenter", handleDragEnter);
    window.addEventListener("dragleave", handleDragLeave);
    window.addEventListener("dragover", handleDragOver);
    window.addEventListener("drop", handleDrop);
    window.addEventListener("keydown", handleKeyDown);

    return () => {
      window.removeEventListener("dragenter", handleDragEnter);
      window.removeEventListener("dragleave", handleDragLeave);
      window.removeEventListener("dragover", handleDragOver);
      window.removeEventListener("drop", handleDrop);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [router, setUploadError]);

  return (
    <>
      {isDragging && (
        <div className="drag-overlay">
          <div className="drag-overlay__content">
            <UploadIcon size={48} />
            <p>释放文件以开始天文图像分析</p>
          </div>
        </div>
      )}
      <main className="home-main">
        <section className="hero">
          <div className="page-shell hero-grid">
            <div className="hero-copy">
              <h1 aria-label={home.hero.fullTitle}>
                {home.hero.titlePrefix}
                <span>{home.hero.titleEmphasis}</span>
              </h1>
              <p>{home.hero.description}</p>
              <div className="hero-actions">
                <Link className="button button--primary" href="/analysis">
                  {home.hero.analysisCta}
                  <ArrowIcon />
                </Link>
                <Link className="button button--secondary" href="/processing">
                  {home.hero.processingCta}
                </Link>
              </div>
            </div>

            <aside
              className="upload-signal"
              aria-label={home.uploadSignal.title}
            >
              <div className="border-mask" aria-hidden="true" />
              <input
                type="file"
                ref={fileInputRef}
                onChange={handleFileChange}
                accept=".fits,.fit,.fts,.xisf"
                style={{ display: "none" }}
              />
              <button
                aria-label={home.uploadSignal.action}
                className="upload-signal__hit-area"
                onClick={handleUploadCardClick}
                type="button"
              />
              <div className="upload-signal__orb" aria-hidden="true">
                <UploadIcon size={25} />
              </div>
              <h2>{home.uploadSignal.title}</h2>
              <p>{home.uploadSignal.description}</p>
              <dl className="upload-specs">
                <div>
                  <dt>{home.uploadSignal.formatLabel}</dt>
                  <dd>{home.uploadSignal.formatValue}</dd>
                </div>
                <div>
                  <dt>{home.uploadSignal.sizeLabel}</dt>
                  <dd>{home.uploadSignal.sizeValue}</dd>
                </div>
              </dl>
              <span className="upload-scope">{home.uploadSignal.scope}</span>
              <p className="upload-quota">{home.uploadSignal.quota}</p>
              <div className={`upload-diagnostic-bar ${uploadError ? "has-error" : ""}`} aria-hidden="true">
                <span className="diagnostic-dot"></span>
                <span className="diagnostic-line"></span>
                <span className="diagnostic-text">
                  {uploadError ? "文件格式不支持" : "准备检查天文图像"}
                </span>
              </div>
              {uploadError && (
                <div className="diagnostic-error-desc" role="alert">
                  请选择 .fits、.fit、.fts 或 .xisf 文件。
                </div>
              )}
            </aside>
          </div>
        </section>

        <section className="features-section" aria-label={home.features.ariaLabel}>
          <div className="page-shell">
            <div className="section-heading">
              <h2>{home.features.heading}</h2>
              <p>{home.features.description}</p>
            </div>
            <div className="feature-grid">
              <FeatureCard
                description={home.features.analysis.description}
                href="/analysis"
                icon={<OrbitIcon />}
                linkLabel={home.features.analysis.link}
                title={home.features.analysis.title}
                variant="primary"
              />
              <FeatureCard
                description={home.features.processing.description}
                href="/processing"
                icon={<SparkIcon />}
                linkLabel={home.features.processing.link}
                title={home.features.processing.title}
                variant="secondary"
              />
              <FeatureCard
                description={home.features.history.description}
                href="/history"
                icon={<HistoryIcon />}
                linkLabel={home.features.history.link}
                title={home.features.history.title}
                variant="horizontal"
              />
            </div>
          </div>
        </section>

        {home.faq && (
          <section className="faq-section" aria-label={home.faq.title}>
            <div className="page-shell">
              <details className="faq-details">
                <summary className="faq-summary">
                  <span className="section-kicker">{home.faq.kicker}</span>
                  <h3>
                    {home.faq.title}
                    <svg className="faq-summary__icon" fill="none" height="16" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" width="16">
                      <path d="M19 9l-7 7-7-7" />
                    </svg>
                  </h3>
                </summary>
                <div className="faq-content">
                  {home.faq.items.map((item, index) => (
                    <article key={index} className="faq-card">
                      <h4>{item.question}</h4>
                      <p>{item.answer}</p>
                    </article>
                  ))}
                </div>
              </details>
            </div>
          </section>
        )}

        <section className="privacy-section">
          <div className="page-shell privacy-layout">
            <div className="privacy-card">
              <div className="sandbox-badge">
                <span className="sandbox-badge__dot"></span>
                <span className="sandbox-badge__text">仅保存任务摘要，不记录用户数据</span>
              </div>
              <h2>{home.privacy.heading}</h2>
              <p>{home.privacy.body}</p>
            </div>
            <div className="resource-panel-minimal">
              <div className="resource-header">
                <span className="resource-header__title">资源限制</span>
                <span className="resource-header__status">建议单文件处理</span>
              </div>
              <p className="resource-note">{home.privacy.resource}</p>
            </div>
          </div>
        </section>
      </main>

      <footer className="site-footer">
        <div className="page-shell footer-inner">
          <span>{zhCN.footer.note}</span>
          <span className="footer-boundary">{zhCN.footer.boundary}</span>
        </div>
      </footer>
    </>
  );
}
