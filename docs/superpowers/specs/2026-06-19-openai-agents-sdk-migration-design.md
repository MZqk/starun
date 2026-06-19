# OpenAI Agents SDK Migration Design

## 1. Objective

Replace Starun's custom Agent orchestration with the OpenAI Agents SDK while
preserving the existing task infrastructure, public APIs, frontend behavior,
and stored task results.

The two website features use separate Agents:

- Professional analysis uses `deep-sky-advisor/`.
- AI automatic image generation uses `deep-sky-processor/`.

Both skill directories are treated as opaque dependencies. This migration does
not inspect, redesign, or modify their internal implementation.

## 2. Confirmed Decisions

1. Keep the existing FastAPI task APIs, SQLite task state, serial executor,
   cancellation, timeout, quota, event, artifact, cleanup, and history
   infrastructure.
2. Introduce a shared Starun integration layer around the OpenAI Agents SDK.
3. Use one fixed Analysis Agent and one fixed Processing Agent. Do not add a
   routing or supervisor Agent.
4. Execute the two local skills inside the Starun API container.
5. Restrict each Agent to its own skill and the current task workspace.
6. Prefer the Responses API, with an explicitly configured Chat Completions
   compatibility mode for providers that only implement that API.
7. Preserve the current frontend-facing `result_manifest`, task events, and
   artifact behavior.
8. Remove the old Agent runtime after the migration passes its contract and
   regression tests. Do not maintain a permanent old/new runtime switch.

## 3. Current State

Professional analysis currently renders a FITS preview and calls
`KimiAnalysisClient` directly. AI automatic image generation currently uses the
custom `AgentRunner`, `ToolRegistry`, a fixed three-step plan, and local
processing tools.

The existing task layer already provides the stable application boundary:

- `SerialTaskExecutor` leases and runs tasks.
- `AnalysisTaskHandler` and `ProcessingTaskHandler` prepare task input and
  translate failures.
- `TaskEventService` persists user-visible progress.
- `ArtifactStore` validates and publishes task artifacts.
- Task APIs and frontend polling consume stable task and result contracts.

The migration replaces only the AI execution internals behind the task
handlers.

## 4. Architecture

Add an `app/agent_sdk/` package with the following responsibilities:

### 4.1 Model Provider Factory

Build the Agents SDK model integration from explicit configuration:

- base URL;
- API key;
- model name;
- protocol: `responses` or `chat_completions`;
- request timeout and optional provider-specific metadata.

The protocol is never inferred by sending a request. Explicit selection avoids
duplicate requests, unexpected fallback behavior, and duplicate provider
charges.

Responses is the default protocol. Chat Completions is a compatibility mode,
not a second application runtime. Both modes expose the same Starun Agent and
skill contracts.

Analysis and processing share the default model configuration. The settings
model may support per-feature overrides, but the initial migration will not add
dynamic multi-provider routing.

### 4.2 Sandbox Agent Factories

Create two independent factories:

- `build_analysis_agent(...)`
- `build_processing_agent(...)`

Each factory supplies:

- a feature-specific system instruction;
- the configured model;
- one native Agents SDK `Skills` capability containing only the assigned local
  skill;
- a minimal sandbox capability set;
- SDK limits and guardrails;
- a structured final-output model.

The Analysis Agent can invoke only `deep-sky-advisor`. The Processing Agent can
invoke only `deep-sky-processor`. Neither Agent can call the other.

### 4.3 Native Sandbox Skill Execution

Use the Agents SDK `SandboxAgent`, `Skills`, `Manifest`, and
`UnixLocalSandboxClient` APIs. This is the SDK's native local `SKILL.md`
execution model and preserves each skill's own instructions, scripts,
references, and assets without Starun reimplementing skill semantics.

The Sandbox Agent API is currently beta. Starun accepts that dependency and
will pin the SDK to a reviewed minor-version range. Upgrading that range
requires rerunning provider, workspace, skill-isolation, cancellation, and
artifact contract tests.

Each Agent receives a synthetic skills root containing only its assigned skill,
for example a `Dir` with one `LocalDir` child. Starun does not point the SDK at
the repository root because that would expose both skills to both Agents.

The sandbox enforces:

- a fresh workspace for every task;
- one copied skill tree under the SDK auto-discovery root;
- copied task inputs under `input/`;
- a writable `output/` directory;
- the minimum capabilities required by the selected skill;
- session teardown on cancellation or timeout;
- no persistent session, snapshot, or memory between Starun tasks.

Starun does not add an unrestricted host function tool. All model-facing file
and command execution occurs through the SDK sandbox session. Provider-hosted
shell execution is not part of the design.

`UnixLocalSandboxClient` is a workspace and lifecycle abstraction, not a
strong operating-system security boundary. Shell commands execute inside the
Starun API container and may inherit that container's readable filesystem and
environment. The API container is therefore the security boundary: it runs as
an unprivileged user, drops Linux capabilities, uses a read-only root
filesystem except for `/data` and `/tmp`, mounts skills read-only, and receives
only deployment-required secrets. A future requirement for hostile or
third-party skills would require a separate Docker or remote sandbox worker.

### 4.4 Run Bridge

The run bridge connects one Agents SDK sandbox run to one Starun task. It:

- starts the appropriate Agent;
- creates a fresh sandbox session from the task manifest;
- supplies the task input and single-skill manifest;
- translates SDK lifecycle items into Starun task events;
- checks task cancellation between streamed SDK items;
- closes and deletes the sandbox session when cancellation is requested;
- applies model and tool-call limits;
- validates the final structured output;
- reads declared output files through the sandbox session before cleanup;
- registers verified artifacts through `ArtifactStore`;
- returns the existing handler-facing result shape.

The bridge does not reimplement planning, tool selection, evaluation, retries,
or conversation management. Those belong to the Agents SDK or the selected
skill.

## 5. Task Workspace and Data Contracts

Every task receives a fresh SDK sandbox workspace. A run never reuses model
context, conversation state, sandbox session state, snapshots, memory, or
writable files from another task.

### 5.1 Common Inputs

The task handler supplies these manifest entries:

- `input/source.fits`: the task source, exposed read-only to the skill;
- `input/inspection.json`: Starun-validated HDU information, FITS headers, and
  basic statistics;
- `input/request.json`: contract version, task ID, task type, locale, output
  directory, and feature-specific parameters.

For processing tasks, `request.json` also includes the selected processing
style.

The Agent instruction receives stable workspace-relative paths to these files.
FITS headers, filenames, and other user-controlled content are data, never
system instructions.

### 5.2 Common Outputs

Each skill writes only below `output/` and must create one fixed result file:

- analysis: `output/analysis-result.json`;
- processing: `output/processing-result.json`.

Both documents use a Starun-owned, versioned envelope such as
`starun.skill-result/v1`. They may reference artifacts only by paths relative
to `output/`.

The result schemas are owned by Starun and form the integration boundary. Skill
internals may evolve independently as long as they continue to satisfy the
schema.

### 5.3 Analysis Result Mapping

The validated analysis result maps to the existing frontend-facing fields:

- preview metadata and preview artifact;
- structured professional analysis;
- report artifact;
- provider/model metadata;
- `demo: false`.

The exact domain model remains the existing `ProfessionalAnalysis` contract
unless a separately approved product change modifies it.

### 5.4 Processing Result Mapping

The validated processing result maps to the existing fields:

- processing style;
- reference artifact;
- generated result artifact;
- dimensions and media type;
- quality score where supplied;
- generation or processing record;
- model/provider metadata;
- the existing generative-processing disclaimer;
- `demo: false`.

### 5.5 Artifact Publication

Starun publishes only artifacts declared in the validated result document.

The task fails if an artifact:

- is missing;
- escapes the output directory;
- traverses a symbolic link;
- has a disallowed media type;
- exceeds configured size or count limits;
- conflicts with another artifact name;
- differs from the metadata claimed by the skill.

Partial or undeclared files remain unpublished and are removed by normal task
cleanup.

## 6. Task Flows

### 6.1 Professional Analysis

1. `AnalysisTaskHandler` loads the task and validated FITS inspection.
2. The handler creates the isolated workspace and input manifests.
3. The run bridge starts the Analysis Agent.
4. The Agent invokes the restricted `deep-sky-advisor` tool.
5. The skill writes its result JSON and artifacts.
6. The bridge validates the result and publishes artifacts.
7. The handler returns the existing analysis `result_manifest`.

The old direct `KimiAnalysisClient` path is removed from production.

### 6.2 AI Automatic Image Generation

1. `ProcessingTaskHandler` loads the task, source, inspection, and style.
2. The handler creates the isolated workspace and input manifests.
3. The run bridge starts the Processing Agent.
4. The Agent invokes the restricted `deep-sky-processor` tool.
5. The skill writes its result JSON and artifacts.
6. The bridge validates the result and publishes artifacts.
7. The handler returns the existing processing `result_manifest`.

The old fixed processing plan and its production tool registry are removed.

## 7. Events, Progress, Cancellation, and Timeouts

The existing task event API remains the user-visible progress source.

The run bridge maps SDK activity into stable Starun events. Event payloads may
include:

- Agent run started;
- skill tool started and finished;
- artifact validation started and finished;
- Agent run completed;
- sanitized provider run or request identifiers.

Raw prompts, API keys, unrestricted model output, and full FITS headers are not
written to task events.

`SerialTaskExecutor` retains the overall analysis and processing timeouts. The
run bridge additionally propagates cancellation to the active SDK run and
tears down the sandbox session, including active shell processes. A cancelled
or timed-out task publishes no partial result.

Progress percentages remain coarse lifecycle values controlled by the
handlers. They are not derived from model-generated estimates.

## 8. Error Model

The adapter converts internal exceptions into stable `TaskHandlerError`
categories:

| Code | Meaning | Retryable |
| --- | --- | --- |
| `agent_not_configured` | Required model configuration is absent or invalid | No |
| `agent_provider_error` | Provider timeout, network failure, 429, or provider 5xx | Depends on cause |
| `agent_guardrail` | SDK limit, tool restriction, or guardrail rejected the run | No |
| `skill_execution_failed` | The fixed skill launcher failed or produced no result | Depends on classified exit |
| `skill_output_invalid` | Result schema or artifact declaration is invalid | No |
| `task_timeout` | Existing task execution timeout expired | Yes |

The API returns stable public messages. Detailed SDK exceptions, sanitized
subprocess diagnostics, and provider request/run identifiers are recorded only
in server logs and safe task-event fields.

## 9. Configuration

Replace Kimi-specific Agent settings with provider-neutral settings using the
existing `STARUN_` settings prefix:

- `STARUN_AGENT_BASE_URL`
- `STARUN_AGENT_API_KEY`
- `STARUN_AGENT_MODEL`
- `STARUN_AGENT_PROTOCOL`
- `STARUN_AGENT_TIMEOUT_SECONDS`
- `STARUN_AGENT_MAX_TURNS`
- `STARUN_ANALYSIS_SKILL_PATH`
- `STARUN_PROCESSING_SKILL_PATH`
- optional analysis and processing overrides if required by deployment

Image-provider settings currently used directly by Starun are removed only if
the opaque processing skill fully owns that provider integration. No skill
internal configuration is assumed in this design. Deployment must expose only
the environment variables required by Starun and the trusted local skills.

Agents SDK tracing is disabled by default unless it can be configured not to
upload sensitive task input. Starun task events and server logs remain the
primary operational audit trail.

## 10. Compatibility

The migration does not change:

- frontend routes or page behavior;
- task creation, polling, cancellation, and artifact download APIs;
- task types and status transitions;
- quota and expiration behavior;
- browser history storage;
- existing completed task records;
- database schema.

Existing completed tasks remain readable. There is no migration or rewrite of
historical `result_manifest` values.

New SDK metadata may be added only as optional result fields that existing
frontend code can ignore.

## 11. Removal and Migration Scope

The implementation will:

1. add the OpenAI Agents SDK dependency;
2. add `app/agent_sdk/`;
3. add the native Sandbox Agent, single-skill manifest, and session lifecycle
   integration;
4. migrate both task handlers to the new bridge;
5. add skill result schemas and workspace preparation;
6. update Docker configuration so both skills are present read-only in the API
   container;
7. update provider-neutral environment configuration and operations
   documentation;
8. remove production dependencies on `KimiAnalysisClient`, custom
   `AgentRunner`, `ToolRegistry`, fixed processing plans, and production
   processing tools;
9. remove obsolete mocks, tests, configuration, and modules after confirming
   that no production or test call sites remain.

There is no permanent feature flag for the old runtime. Unit and contract tests
provide the migration safety boundary.

## 12. Test Strategy

### 12.1 Provider Tests

- Responses configuration creates the expected SDK model integration.
- Chat Completions configuration creates the compatibility integration.
- The protocol is explicit and never auto-probed.
- Authentication, timeout, 429, 4xx, 5xx, malformed response, and network
  failures map correctly.

### 12.2 Run Bridge Tests

- SDK lifecycle items map to ordered Starun events.
- Each Agent manifest contains only its assigned skill.
- Each task receives a fresh sandbox session.
- Cancellation closes and deletes the session and active processes.
- Task timeout stops execution and publishes no result.
- Maximum turns and tool-call limits are enforced.
- SDK and tool failures map to stable task errors.
- The pinned SDK minor range passes the complete sandbox contract suite.

### 12.3 Skill Contract Tests

Use fake fixed launchers that produce deterministic result JSON and artifacts.
Tests do not read or analyze the real skill directories.

Cover:

- valid analysis result;
- valid processing result;
- missing result document;
- unsupported contract version;
- malformed result schema;
- nonzero and classified launcher exits.

### 12.4 Artifact and Security Tests

Reject:

- path traversal;
- absolute artifact paths;
- symbolic-link escapes;
- undeclared artifacts;
- missing declared artifacts;
- output count and size violations;
- unsupported file types;
- manifests that expose the other feature's skill;
- output claims that reference files outside `output/`.

### 12.5 Handler and API Regression Tests

- Analysis and processing handlers produce the existing `result_manifest`.
- Task events remain visible while work is running.
- Upload, source reuse, cancellation, expiry, quota, deletion, and download
  behavior remain unchanged.
- Existing API and frontend tests continue to pass.
- Key end-to-end flows cover upload to analysis, upload or analysis reuse to
  processing, cancellation, failure display, artifact download, and history
  restoration.

## 13. Acceptance Criteria

1. `/analysis` invokes only the Analysis Agent and `deep-sky-advisor`.
2. `/processing` invokes only the Processing Agent and `deep-sky-processor`.
3. The default Responses provider and configured Chat Completions provider both
   pass provider and skill-contract tests.
4. A cancellation tears down the active sandbox session and its processes and
   publishes no partial artifacts.
5. Invalid skill output cannot escape the task workspace or enter
   `result_manifest`.
6. The frontend works without a required contract change.
7. Existing completed tasks remain readable.
8. The old custom Agent runtime is absent from production call paths.
9. Backend tests, frontend tests, and critical end-to-end flows pass.

## 14. Explicit Non-Goals

- Inspecting or modifying the internal contents of either skill.
- Adding a conversational supervisor or dynamic Agent router.
- Supporting cross-task Agent memory or persistent SDK sessions.
- Redesigning the frontend or public task APIs.
- Replacing the SQLite serial task executor.
- Adding automatic provider capability detection.
- Maintaining two production Agent runtimes after migration.
