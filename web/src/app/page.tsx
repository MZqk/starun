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

export default function HomePage() {
  const { home } = zhCN;

  return (
    <>
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

            <aside className="upload-signal" aria-label={home.uploadSignal.title}>
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
              {home.steps.items.map((step, index) => (
                <li key={step.title}>
                  <span className="step-number">{index + 1}</span>
                  <div>
                    <h3>{step.title}</h3>
                    <p>{step.description}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="privacy-section">
          <div className="page-shell privacy-layout">
            <div>
              <h2>{home.privacy.heading}</h2>
              <p>{home.privacy.body}</p>
            </div>
            <p className="resource-note">{home.privacy.resource}</p>
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
