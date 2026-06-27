# Computer Vision Guardrails Design

## Context

`deep-sky-processor` already uses OpenCV and scikit-image as core image-processing dependencies:

- OpenCV backs safe color conversions in `color_conv.py`, inpainting in `star_tools.py`, and external StarNet++ image loading.
- scikit-image backs image I/O, resize, Otsu thresholding, morphology, CLAHE, restoration, denoising, and object measurements across the scripts.

These libraries are useful, but they are general-purpose computer vision tools. Used without explicit guardrails, they can introduce domain mistakes in deep-sky processing: RGB/BGR swaps, dtype/range corruption, overly broad inpaint masks, halo artifacts from local contrast, noisy segmentation, and object measurements interpreted as physical truth.

This design defines how OpenCV and scikit-image should help the skill as bounded CV infrastructure, not as a generic vision framework.

## Goal

Create a Computer Vision Guardrails layer for `deep-sky-processor` documentation and future implementation work.

The guardrails should:

- reduce engineering errors around dtype, color order, image I/O, and coordinate conventions;
- protect astronomical realism when using inpainting, morphology, segmentation, sharpening, and local contrast;
- improve quality review with object-level measurements instead of only whole-image metrics;
- clarify which OpenCV and scikit-image features are relevant to deep-sky post-processing.

## Non-Goals

- Do not introduce generic object detection, face recognition, video, camera capture, optical flow, panorama stitching, or YOLO/DNN workflows.
- Do not use inpainting to create missing nebula, dust, stars, or catalog-predicted structures.
- Do not replace Astropy Evidence, FITS handling, plate solving, or WCS logic.
- Do not copy medical, microscopy, or particle-counting thresholds into astronomy workflows.
- Do not change algorithms as part of this design document.

## OpenCV Guardrails

OpenCV should be treated as a high-performance CV backend with strict boundaries.

### Color Space Boundaries

Project-internal image arrays should remain RGB-oriented float arrays unless a specific function documents otherwise. OpenCV boundaries must handle BGR/RGB conversion explicitly.

`color_conv.py` is the right pattern: OpenCV is used for robust Lab/HSV conversion, while the public helper names keep RGB semantics. Direct `cv2.cvtColor` calls in other modules should document input and output channel order.

### Image I/O Safety

Every `cv2.imread` call must check for `None`. This is especially important for external StarNet++ or other tool outputs. A missing or unreadable file should produce a structured warning or failure result, not continue with an invalid image.

When reading external outputs, the code should preserve bit depth where possible and convert BGR to RGB before returning data to the project pipeline.

### Inpaint Realism Boundary

Telea and Navier-Stokes inpainting may be used only for bounded star-mask repair. It must not be used to synthesize absent astronomical structures.

Inpaint use should be gated by:

- star-mask confidence;
- mask coverage ratio;
- connected-component size distribution;
- before/after difference review;
- checks for nebula filaments, dust lanes, bright cores, or galaxy structure being included in the mask.

If the mask is too broad or confidence is low, the workflow should prefer external starless input or manual review rather than expanding inpaint.

### Performance Boundary

OpenCV and NumPy vectorized operations should be preferred over Python pixel loops. Large images should use existing tile or low-memory paths when an operation scales poorly with image area.

Performance guidance must not override precision or realism constraints. A fast method that damages faint signal is not acceptable.

### Edge and Contour Diagnostics

Canny, Sobel, contour finding, and related OpenCV capabilities are useful as diagnostics, not primary aesthetic tools.

Good uses:

- locate hard mask edges;
- detect black rings around removed stars;
- flag local-enhancement halos;
- find crop seams or frame-edge artifacts;
- compare before/after edge energy inside masked regions.

These diagnostics should feed warnings, quality gates, or review overlays. They should not be used as proof that more edge contrast means more real detail.

## scikit-image Guardrails

scikit-image should be treated as the scientific image-analysis and measurement layer.

### dtype and Range Semantics

The skill must preserve the distinction between:

- linear FITS/XISF data;
- normalized float working arrays;
- display previews;
- 8-bit JPG/PNG output;
- TIFF/FITS master outputs.

Use `img_as_float32` and `img_as_ubyte` only at documented boundaries. Avoid ad hoc `astype(float)` or `/255` conversions unless the source range is known. A dtype conversion must not silently clip faint linear signal or collapse high-bit-depth data.

### Coordinate Convention

scikit-image uses `(row, col)` coordinates. Visualization, crop arguments, user-facing positions, WCS, and catalog projection usually use `(x, y)`.

Any boundary crossing must explicitly convert:

- `(row, col)` to `(x, y)` for display and user-facing JSON;
- `(x, y)` to `(row, col)` for masks and array indexing;
- normalized centers to pixel coordinates with documented width/height ordering.

This matters for masks, regionprops, local enhancement centers, catalog projection, and review overlays.

### Morphology and Connected Components

White top-hat, disk structuring elements, erosion, dilation, labeling, perimeter, and region measurements are appropriate for star and artifact analysis.

Recommended measurements:

- connected component count;
- area and equivalent diameter;
- perimeter and circularity;
- eccentricity or axis ratio;
- bounding-box fill ratio;
- edge-contact ratio;
- mask coverage ratio;
- center-vs-corner spatial distribution.

These measurements should support star detection confidence, hot-pixel filtering, over-mask detection, and quality review.

### Segmentation Before Measurement

Thresholding and connected components should not run directly on noisy data without preprocessing. Otsu, local thresholding, and saliency masks should be paired with conservative denoising or small-object cleanup.

The purpose is to avoid turning read noise, compression blocks, or residual gradients into thousands of false components.

### Object-Level Quality Review

Quality review should move beyond whole-frame summaries when possible.

Useful object-level checks:

- star-mask component count and area distribution;
- overlarge components that may represent nebula or galaxy structure;
- circularity and eccentricity distribution;
- residual rings or holes after star processing;
- local enhancement halos near mask boundaries;
- spatial trends in FWHM, circularity, and edge energy;
- foreground/background region statistics for SNR-style review.

These checks should be reported as evidence and warnings. They do not replace visual review.

### CLAHE and Local Contrast Review

CLAHE and local contrast can make faint structures more visible, but they can also create halos, hard transitions, plastic texture, and false-looking high-frequency detail.

Any local contrast or CLAHE use should be reviewed with:

- before/after comparison;
- difference image;
- halo and hard-edge checks;
- high-frequency energy interpreted only as a risk indicator, not proof of real detail;
- target-type safety rules, especially for reflection nebulae, dark nebulae, galaxies, and star clusters.

## Integration Points

Future implementation should add a dedicated reference document:

- `deep-sky-processor/references/cv_guardrails.md`

That document should be referenced from:

- `deep-sky-processor/SKILL.md`;
- `deep-sky-processor/references/engine_details.md`;
- `deep-sky-processor/references/quality_assessment.md`;
- any future mask, star-processing, or artifact-review workflow docs.

The reference should not duplicate every OpenCV or scikit-image tutorial. It should document project-specific rules, unsafe patterns, and accepted uses.

## Relationship to Astropy Evidence

Astropy Evidence handles astronomical metadata, units, coordinates, WCS, time, and catalog projection.

CV Guardrails handle pixel-space operations, masks, segmentation, local contrast, inpainting, and object-level image measurements.

The two layers meet when pixel measurements need astronomical context. Example: scikit-image measures FWHM in pixels; Astropy Evidence provides pixel scale so quality review can also report arcseconds.

## Acceptance Criteria

A future implementation should be considered complete when:

- CV rules are documented in `references/cv_guardrails.md`.
- `SKILL.md` points agents to the guardrails before high-risk CV stages.
- OpenCV I/O and color conversions have explicit RGB/BGR and failure handling rules.
- scikit-image dtype/range and `(row, col)` conventions are documented.
- Star/mask workflows document object-level metrics and coverage thresholds.
- Quality review can explain when CV-derived metrics are heuristic rather than physical truth.
- The documented non-goals keep generic CV capabilities out of the deep-sky workflow.
