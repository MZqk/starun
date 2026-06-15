# AI Art Enhancement Design

## 1. Goal

Replace the deterministic processing demo with a two-stage AI Agent that:

1. derives a high-resolution display preview from the uploaded FITS image;
2. asks Kimi to produce structured art-direction advice for the selected style;
3. sends the preview and the approved prompt to `hy-image-v3.0`;
4. returns one generated image and a complete processing record.

The feature is an **AI art enhancement workflow**, not a scientific
astrophotography processing pipeline.

## 2. Product Boundary

The result must always be labeled as generative AI output. It may contain stars,
nebula structures, colors, lighting, or texture that did not exist in the FITS
data. The product must not describe it as scientifically accurate, structurally
faithful, or suitable for measurement.

The original FITS inspection, the program-generated reference preview, Kimi's
advice, and the generated result remain distinct in storage and presentation.

The first release:

- supports FITS inputs already accepted by Starun;
- generates exactly one image for the selected style;
- uses the full-frame FITS preview as the reference image;
- instructs the image model to preserve composition, subject position, and
  aspect ratio as much as possible;
- does not generate a TIFF master;
- does not automatically run a second generation or model-review pass;
- does not use the `deep-sky-processor` skill or its local scripts.

## 3. User Styles

The existing style selector remains the public contract.

### Realistic

- Preserve the reference composition strictly.
- Use restrained saturation and contrast.
- Keep natural-looking stars and background.
- Minimize invented structure, while still acknowledging that the output is
  generative.

### Balanced

- Preserve composition and subject position.
- Improve subject separation, color harmony, and local contrast moderately.
- Avoid extreme color shifts and excessive glow.

### Artistic

- Preserve the same frame and primary subject position.
- Allow stronger color grading, atmosphere, and dramatic contrast.
- Continue to prohibit changing the target into a different celestial object or
  changing the overall composition.

## 4. Architecture

### 4.1 Processing Orchestration

The existing serial processing task and Agent event infrastructure remain in
place. The mock model and mock tools are replaced for processing tasks by three
bounded tools:

1. `processing.prepare_reference`
2. `processing.plan_art_direction`
3. `processing.generate_artwork`

The Agent does not execute model-provided shell commands, Python code, file
paths, URLs, or arbitrary tool names. Tool order and schemas are controlled by
the application.

### 4.2 Reference Preparation

The application reads the persisted selected FITS HDU and creates a
high-resolution PNG reference preview using the same bounded preview-generation
code as professional analysis.

The preview:

- uses the complete frame;
- preserves aspect ratio;
- uses display-only percentile and nonlinear stretching;
- is limited to a configured maximum edge and artifact size;
- is saved as `processing-reference.png`;
- is the exact image sent to both AI providers.

### 4.3 Kimi Art Direction

Kimi receives:

- the reference preview;
- safe FITS header fields;
- measured FITS statistics;
- the selected user style;
- a fixed truthfulness and composition-preservation system instruction.

Kimi returns a strict structured document containing:

- detected target and scene summary;
- recommended color, contrast, lighting, and background direction;
- composition-preservation instructions;
- a generation prompt;
- a negative prompt;
- uncertainty and generative-risk notes.

Kimi does not return executable tool parameters. The application validates and
length-limits every field before using it.

### 4.4 Image Generation Provider

The image provider uses:

- base URL `https://tokenhub.tencentmaas.com/v1`;
- model `hy-image-v3.0`;
- the reference PNG;
- Kimi's validated prompt;
- the selected style and fixed preservation constraints.

The provider adapter must isolate the external request and response format from
the Agent contracts. It accepts only a bounded in-memory reference image and
validated prompt data, and returns encoded image bytes plus non-secret provider
metadata.

Because public documentation for this TokenHub model is not sufficiently
discoverable, implementation must begin with a minimal synthetic reference-image
probe against the configured endpoint. The adapter is finalized from the
observed supported endpoint, request fields, asynchronous behavior, and response
shape. The probe must not upload a user's FITS file.

If the configured model does not support reference-image conditioning, the task
must fail with `image_reference_not_supported`. It must not silently fall back
to text-to-image generation.

## 5. Data Flow

1. The user uploads a FITS file or selects a still-valid analysis source.
2. The user selects `realistic`, `balanced`, or `artistic`.
3. Starun creates a processing task and copies the source into the task
   directory.
4. The reference tool renders `processing-reference.png`.
5. Kimi generates and validates art direction.
6. Starun writes `art-direction.json`.
7. The image provider generates one reference-conditioned image.
8. Starun validates media type, decoded dimensions, byte size, and aspect-ratio
   drift.
9. Starun writes `generated-artwork.png` or `generated-artwork.jpg`.
10. Starun writes `generation-record.json` without API keys or authorization
    headers.
11. The task completes and exposes the reference, result, advice, and record
    through the existing authenticated artifact API.

## 6. Output Contract

The processing task summary includes:

- `mode`: `generative_art_enhancement`;
- selected style;
- Kimi model identifier;
- image model identifier;
- target summary;
- art-direction summary;
- generation disclaimer;
- reference and result artifact names.

Artifacts:

- `processing-reference.png`;
- `generated-artwork.png` or `generated-artwork.jpg`;
- `art-direction.json`;
- `generation-record.json`.

The generation record contains timestamps, model identifiers, selected style,
prompt hashes, output dimensions, response request identifiers when supplied,
and validation results. It never stores secrets.

## 7. UI

The processing page removes all Mock labels.

Before task creation it retains:

- FITS upload or analysis-source reuse;
- the three style choices;
- one primary generation action.

While running it shows:

- reference preparation;
- Kimi art-direction generation;
- image-provider generation;
- artifact validation and completion.

After completion it shows a side-by-side comparison:

- left: the exact stretched FITS reference sent to the models;
- right: the generated AI artwork.

The result panel must prominently state:

> 生成式 AI 艺术增强图，可能包含原图中不存在的细节、星点、纹理或颜色，
> 不可用于科学测量。

The page also displays Kimi's short art-direction summary and allows downloading
the generated image and JSON records.

## 8. Configuration And Secrets

Server-only environment settings:

- `STARUN_IMAGE_AI_BASE_URL`
- `STARUN_IMAGE_AI_API_KEY`
- `STARUN_IMAGE_AI_MODEL`
- `STARUN_IMAGE_AI_TIMEOUT_SECONDS`

Kimi continues using the existing server-side analysis AI settings.

No provider key is exposed to the browser, stored in task results, written to
logs, or committed to Git. Keys pasted into chat should be rotated before
production deployment.

## 9. Failure Handling

Stable task errors:

- `ai_not_configured`: required Kimi configuration is absent;
- `art_direction_failed`: Kimi request or structured response failed;
- `image_provider_not_configured`: image provider configuration is absent;
- `image_reference_not_supported`: the provider cannot condition on the
  reference image;
- `image_generation_failed`: provider rejected or failed the request;
- `generated_image_invalid`: the returned media cannot be decoded or violates
  size, dimension, or aspect-ratio bounds.

Rate limits, provider timeouts, and provider 5xx responses are retryable.
Authentication, unsupported request formats, invalid outputs, and configuration
errors are not automatically retryable.

No failure path silently returns a mock image, text-to-image result, or previous
task output.

## 10. Resource And Safety Limits

- Continue serial task execution on the current 4-core/4-GB server.
- Limit the reference image's longest edge and encoded size.
- Limit prompts and provider response bodies.
- Decode returned images before accepting them.
- Reject unexpected MIME types, unsafe URLs, and oversized downloads.
- Allow downloads only through the existing authenticated artifact route.
- Keep cancellation checks between provider calls and artifact writes.
- Do not fetch arbitrary URLs supplied by Kimi. Only image-provider response
  URLs with HTTPS and an explicitly configured provider host may be fetched.

## 11. Acceptance Criteria

1. A valid FITS processing task creates a full-frame reference preview.
2. Kimi returns validated style-aware art direction.
3. The image provider receives that preview as a reference image.
4. Exactly one image is generated for the selected style.
5. The generated image and reference are available through authenticated
   artifacts.
6. The processing page clearly labels the result as generative and
   non-scientific.
7. Reference-image unsupported behavior fails explicitly without text-only
   fallback.
8. Provider keys are absent from Git diffs, API responses, artifacts, and logs.
9. Existing upload, analysis, history, cancellation, expiry, and quota behavior
   remains intact.
