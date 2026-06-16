"use client";

import { useEffect, useRef, useState } from "react";
import { getApiClient } from "../lib/api/client";
import { zhCN } from "../lib/i18n/zh-CN";

type ArtifactDownloadsProps = {
  artifacts: string[];
  label?: string;
  taskId: string;
};

const SUPPORTED_ARTIFACT = /\.(jpe?g|json|png|tiff?)$/i;

export default function ArtifactDownloads({
  artifacts,
  label,
  taskId,
}: ArtifactDownloadsProps) {
  const copy = zhCN.task11.downloads;
  const [downloading, setDownloading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const downloadable = artifacts.filter((name) => SUPPORTED_ARTIFACT.test(name));
  if (downloadable.length === 0) {
    return null;
  }

  async function download(name: string) {
    setDownloading(name);
    setError(null);
    try {
      const artifact = await getApiClient().downloadArtifact(taskId, name);
      if (!mountedRef.current) return;
      const url = URL.createObjectURL(artifact.blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = artifact.fileName;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (caught) {
      if (mountedRef.current) {
        setError(caught instanceof Error ? caught.message : copy.error);
      }
    } finally {
      if (mountedRef.current) setDownloading(null);
    }
  }

  return (
    <section className="artifact-downloads" aria-labelledby="downloads-title">
      <div>
        <span className="section-kicker">{label ?? copy.label}</span>
        <h2 id="downloads-title">{copy.title}</h2>
      </div>
      <div className="artifact-downloads__actions">
        {downloadable.map((name) => (
          <button
            className="button button--secondary"
            disabled={downloading !== null}
            key={name}
            onClick={() => void download(name)}
            type="button"
          >
            {downloading === name ? copy.preparing : copy.action(name)}
          </button>
        ))}
      </div>
      <p>{copy.authNotice}</p>
      {error ? <p className="form-error">{error}</p> : null}
    </section>
  );
}
