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
import MockNotice from "../components/MockNotice";
import { zhCN } from "../lib/i18n/zh-CN";
import { fileTransfer } from "../lib/transfer";

export default function HomePage() {
  const { home } = zhCN;
  const router = useRouter();
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file && /\.(fits|fit|fts)$/i.test(file.name)) {
      fileTransfer.set(file);
      router.push("/analysis");
    }
  };

  const handleUploadCardClick = () => {
    fileInputRef.current?.click();
  };

  const handleUploadCardKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fileInputRef.current?.click();
    }
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
      if (file && /\.(fits|fit|fts)$/i.test(file.name)) {
        fileTransfer.set(file);
        router.push("/analysis");
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
  }, [router]);

  return (
    <>
      {isDragging && (
        <div className="drag-overlay">
          <div className="drag-overlay__content">
            <UploadIcon size={48} />
            <p>释放文件以开始 FITS 分析</p>
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
              role="button"
              tabIndex={0}
              onClick={handleUploadCardClick}
              onKeyDown={handleUploadCardKeyDown}
            >
              <input
                type="file"
                ref={fileInputRef}
                onChange={handleFileChange}
                accept=".fits,.fit,.fts"
                style={{ display: "none" }}
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
              <div className="upload-diagnostic-bar" aria-hidden="true">
                <span className="diagnostic-line"></span>
                <span className="diagnostic-text">READY_TO_PARSE_HDU</span>
              </div>
            </aside>
          </div>
        </section>

        <div className="page-shell notice-wrap">
          <MockNotice />
        </div>

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
                tags={[
                  { label: home.features.analysis.tags[0], tone: "amber" },
                  { label: home.features.analysis.tags[1], tone: "sage" },
                  { label: home.features.analysis.tags[2], tone: "dusty" },
                ]}
                title={home.features.analysis.title}
                variant="primary"
              />
              <FeatureCard
                description={home.features.processing.description}
                href="/processing"
                icon={<SparkIcon />}
                linkLabel={home.features.processing.link}
                tags={[
                  { label: home.features.processing.tags[0], tone: "sage" },
                  { label: home.features.processing.tags[1], tone: "dusty" },
                ]}
                title={home.features.processing.title}
                variant="secondary"
              />
              <FeatureCard
                description={home.features.history.description}
                href="/history"
                icon={<HistoryIcon />}
                linkLabel={home.features.history.link}
                tags={[
                  { label: home.features.history.tags[0], tone: "amber" },
                  { label: home.features.history.tags[1], tone: "sage" },
                ]}
                title={home.features.history.title}
                variant="horizontal"
              />
            </div>
          </div>
        </section>

        <section className="steps-section" aria-label={home.steps.ariaLabel}>
          <div className="page-shell">
            <div className="section-heading section-heading--center">
              <h2>{home.steps.heading}</h2>
            </div>
            <ol className="steps-list">
              {home.steps.items.map((step, index) => {
                const stepMetaCodes = [
                  "INPUT: FITS_RAW_DATA | VERIFY: HDU_STRUCTURE",
                  "METHOD: AI_DEEP_ADVISOR | ENGINE: KIMI_INTELLIGENT",
                  "OUTPUT: L1_CALIBRATED_REPORT | ARTIFACT_PNG_TIFF"
                ];
                return (
                  <li key={step.title}>
                    <div className="step-badge">
                      <span className="step-number">{String(index + 1).padStart(2, '0')}</span>
                      <span className="step-code">SYS_STEP_0{index + 1}</span>
                    </div>
                    <div className="step-content">
                      <h3>{step.title}</h3>
                      <p>{step.description}</p>
                      <div className="step-meta" aria-hidden="true">
                        <code>{stepMetaCodes[index]}</code>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ol>
          </div>
        </section>

        <section className="privacy-section">
          <div className="page-shell privacy-layout">
            <div className="privacy-card">
              <div className="sandbox-badge" aria-hidden="true">
                <span className="sandbox-badge__dot"></span>
                <span className="sandbox-badge__text">LOCAL_SANDBOX_ACTIVE // NO_USER_DATA_LOGGED</span>
              </div>
              <h2>{home.privacy.heading}</h2>
              <p>{home.privacy.body}</p>
            </div>
            <div className="resource-panel-minimal">
              <div className="resource-header">
                <span className="resource-header__title">HARDWARE_CONSTRAINTS</span>
                <span className="resource-header__status">CPU_LIMITED</span>
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
