# FITS AI Professional Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace mock professional analysis with Kimi multimodal interpretation of a locally generated FITS preview and measured metadata.

**Architecture:** Add a focused analysis package for preview rendering, response schemas, and the Kimi HTTP client. Keep task orchestration in the existing analysis handler and expose its artifacts through the existing authenticated artifact API.

**Tech Stack:** FastAPI, httpx, Astropy, NumPy, Pillow, Pydantic, Next.js/React

---

### Task 1: Analysis Backend

**Files:**
- Create: `api/app/analysis/models.py`
- Create: `api/app/analysis/preview.py`
- Create: `api/app/analysis/kimi.py`
- Modify: `api/app/config.py`
- Modify: `api/app/tasks/handlers.py`

- [ ] Define the validated AI report schema.
- [ ] Render a bounded JPEG preview from the selected FITS HDU.
- [ ] Call Kimi chat completions with image, metadata, and JSON Schema output.
- [ ] Persist preview/report artifacts and map provider failures to task errors.

### Task 2: Analysis UI

**Files:**
- Modify: `web/src/app/analysis/page.tsx`
- Modify: `web/src/lib/i18n/zh-CN.ts`
- Modify: `web/src/app/globals.css`

- [ ] Load the authenticated preview artifact.
- [ ] Render the AI overview, observations, issues, recommendations, and caveats.
- [ ] Remove mock professional metrics and mock recommendation labels.

### Task 3: Configuration And Verification

**Files:**
- Create: `api/.env.example`

- [ ] Document the Kimi environment variables without committing a key.
- [ ] Run backend lint/type checks and frontend lint/build.
- [ ] Start the application and verify the analysis page in the in-app browser.
