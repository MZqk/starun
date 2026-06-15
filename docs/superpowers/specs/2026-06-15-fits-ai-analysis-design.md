# FITS AI Professional Analysis Design

## Scope

The first release supports FITS (`.fits`, `.fit`, `.fts`) only. XISF and the
automatic processing agent remain unchanged.

## Flow

1. Reuse the persisted FITS inspection produced during upload.
2. Read the selected image HDU and generate a bounded JPEG preview using
   percentile clipping and a display-only nonlinear stretch.
3. Send the preview, safe FITS header, image shape, and basic statistics to the
   Kimi `kimi-k2.6` multimodal chat-completions endpoint.
4. Require structured JSON describing image quality, background, stars, noise,
   color, prioritized issues, processing recommendations, and uncertainty.
5. Persist the preview and JSON report as authenticated task artifacts.
6. Display the exact preview supplied to the model and the structured AI report
   on the analysis page.

## Accuracy Boundary

Program-derived metadata and statistics are labeled as measured values. Visual
observations and recommendations are labeled as AI interpretation. The system
does not invent SNR, FWHM, ellipticity, or star-count measurements.

## Failure And Security

The API key is read only from `STARUN_AI_API_KEY`. Provider errors fail the task
with a stable error code and never silently fall back to mock output. FITS
header values are treated as untrusted data and included as serialized
observation context, not executable instructions.
