"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getApiClient,
  parseApiError,
  parseUploadResponse,
} from "../lib/api/client";
import { StarunApiError } from "../lib/api/errors";
import type { UploadResponse } from "../lib/api/types";
import { zhCN } from "../lib/i18n/zh-CN";
import { UploadIcon } from "./Icons";

const MAX_UPLOAD_BYTES = 500 * 1024 * 1024;
const ACCEPTED_EXTENSION = /\.(fits|fit|fts)$/i;

type UploadZoneProps = {
  disabled?: boolean;
  onUploaded: (upload: UploadResponse, file: File) => void;
};

function errorMessage(error: unknown): string {
  if (error instanceof StarunApiError || error instanceof Error) {
    return error.message;
  }
  return zhCN.task11.upload.genericError;
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

export default function UploadZone({
  disabled = false,
  onUploaded,
}: UploadZoneProps) {
  const copy = zhCN.task11.upload;
  const inputRef = useRef<HTMLInputElement>(null);
  const xhrRef = useRef<XMLHttpRequest | null>(null);
  const mountedRef = useRef(true);
  const [dragging, setDragging] = useState(false);
  const [fileName, setFileName] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [phase, setPhase] = useState<
    "idle" | "uploading" | "validating" | "ready"
  >("idle");
  const [inspection, setInspection] = useState<UploadResponse["inspection"] | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  const resetInput = useCallback(() => {
    if (inputRef.current) {
      inputRef.current.value = "";
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      xhrRef.current?.abort();
    };
  }, []);

  const uploadFile = useCallback(
    async (file: File) => {
      resetInput();
      setError(null);
      setInspection(null);
      setProgress(0);
      setFileName(file.name);

      if (!ACCEPTED_EXTENSION.test(file.name)) {
        setPhase("idle");
        setError(copy.extensionError);
        return;
      }
      if (file.size > MAX_UPLOAD_BYTES) {
        setPhase("idle");
        setError(copy.sizeError);
        return;
      }

      setPhase("uploading");
      try {
        const request = await getApiClient().buildUploadRequest(file);
        if (!mountedRef.current) {
          return;
        }
        const xhr = new XMLHttpRequest();
        xhrRef.current = xhr;
        xhr.open("POST", request.url);
        request.headers.forEach((value, key) => xhr.setRequestHeader(key, value));
        xhr.upload.addEventListener("progress", (event) => {
          if (!mountedRef.current || !event.lengthComputable) {
            return;
          }
          setProgress(Math.round((event.loaded / event.total) * 100));
          if (event.loaded === event.total) {
            setPhase("validating");
          }
        });
        xhr.onload = () => {
          if (!mountedRef.current || xhrRef.current !== xhr) {
            return;
          }
          xhrRef.current = null;
          const body = parseJson(xhr.responseText);
          try {
            if (xhr.status < 200 || xhr.status >= 300) {
              throw parseApiError(body, xhr.status);
            }
            const upload = parseUploadResponse(body, xhr.status);
            setProgress(100);
            setPhase("ready");
            setInspection(upload.inspection);
            onUploaded(upload, file);
          } catch (caught) {
            setPhase("idle");
            setError(errorMessage(caught));
            resetInput();
          }
        };
        xhr.onerror = () => {
          if (mountedRef.current) {
            xhrRef.current = null;
            setPhase("idle");
            setError(copy.networkError);
            resetInput();
          }
        };
        xhr.onabort = () => {
          if (mountedRef.current) {
            xhrRef.current = null;
            setPhase("idle");
            setProgress(0);
            setError(copy.cancelled);
            resetInput();
          }
        };
        xhr.send(request.body);
      } catch (caught) {
        if (mountedRef.current) {
          setPhase("idle");
          setError(errorMessage(caught));
          resetInput();
        }
      }
    },
    [
      copy.cancelled,
      copy.extensionError,
      copy.networkError,
      copy.sizeError,
      onUploaded,
      resetInput,
    ],
  );

  const chooseFiles = useCallback(
    (files: FileList | null) => {
      const file = files?.[0];
      if (file) {
        void uploadFile(file);
      }
    },
    [uploadFile],
  );

  const uploading = phase === "uploading" || phase === "validating";

  return (
    <section className="upload-zone-wrap" aria-labelledby="upload-zone-title">
      <div
        className={dragging ? "upload-zone is-dragging" : "upload-zone"}
        onDragEnter={(event) => {
          event.preventDefault();
          if (!disabled && !uploading) setDragging(true);
        }}
        onDragLeave={(event) => {
          event.preventDefault();
          setDragging(false);
        }}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (!disabled && !uploading) chooseFiles(event.dataTransfer.files);
        }}
      >
        <UploadIcon size={28} />
        <div>
          <h2 id="upload-zone-title">{copy.title}</h2>
          <p>{copy.description}</p>
        </div>
        <input
          accept=".fits,.fit,.fts"
          aria-label={copy.inputLabel}
          disabled={disabled || uploading}
          onChange={(event) => {
            const files = event.currentTarget.files;
            chooseFiles(files);
            event.currentTarget.value = "";
          }}
          ref={inputRef}
          type="file"
        />
        <button
          className="button button--secondary"
          disabled={disabled || uploading}
          onClick={() => {
            resetInput();
            inputRef.current?.click();
          }}
          type="button"
        >
          {copy.choose}
        </button>
      </div>

      <div className="upload-notices" role="note">
        <span>{copy.refreshNotice}</span>
        <span>{copy.quotaNotice}</span>
      </div>

      {fileName ? (
        <div className="upload-progress" aria-live="polite">
          <div>
            <strong>{fileName}</strong>
            <span>
              {phase === "validating"
                ? copy.validating
                : phase === "ready"
                  ? copy.ready
                  : `${progress}%`}
            </span>
          </div>
          <progress max={100} value={progress}>
            {progress}%
          </progress>
          {uploading ? (
            <button
              className="text-button"
              onClick={() => xhrRef.current?.abort()}
              type="button"
            >
              {copy.cancel}
            </button>
          ) : null}
        </div>
      ) : null}

      {error ? (
        <p className="form-error" role="alert">
          {error}
        </p>
      ) : null}

      {inspection ? (
        <section className="inspection-summary" aria-label={copy.validationAriaLabel}>
          <div className="section-kicker">{copy.validationKicker}</div>
          <h3>{copy.validationCount(inspection.hdus.length)}</h3>
          <dl>
            <div>
              <dt>{copy.selected}</dt>
              <dd>{zhCN.task11.common.hduLabel(inspection.selected_hdu.index)}</dd>
            </div>
            <div>
              <dt>{copy.name}</dt>
              <dd>{inspection.selected_hdu.name}</dd>
            </div>
            <div>
              <dt>{copy.shape}</dt>
              <dd>
                {inspection.selected_hdu.shape?.join(" × ") ??
                  zhCN.task11.common.unavailable}
              </dd>
            </div>
            <div>
              <dt>{copy.dtype}</dt>
              <dd>
                {inspection.selected_hdu.dtype ??
                  zhCN.task11.common.unavailable}
              </dd>
            </div>
          </dl>
        </section>
      ) : null}
    </section>
  );
}
