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

## Mandatory Image State Contract

Every CV stage must know and preserve the image state it receives. Any intermediate artifact or JSON report that crosses a script boundary should carry these fields when practical:

```json
{
  "array_dtype": "float32",
  "value_range": "0..1",
  "data_domain": "linear|nonlinear|display_preview|mask|unknown",
  "channel_order": "RGB|BGR|gray|alpha|unknown",
  "bit_depth_source": "float32|uint16|uint8|fits_bitpix_-32|unknown",
  "is_preview": false
}
```

Hard rules:

- Linear FITS/XISF data must not be converted to `uint8` or display-stretched arrays for processing input.
- Display previews are review artifacts only. They must never feed later processing stages.
- Masks are scalar float or boolean arrays; masks must not be treated as luminance images.
- Any `uint8` conversion must be output-only, preview-only, or OpenCV algorithm-boundary-only, and the next consumer must know that precision was reduced.
- Master outputs should preserve float TIFF/FITS when requested; JPG/PNG are final display deliverables, not scientific intermediates.

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

OpenCV I/O must record `channel_order="BGR"` at read time and `channel_order="RGB"` after conversion. Direct OpenCV reads of 16-bit or float files must use `cv2.IMREAD_UNCHANGED` unless the caller explicitly wants an 8-bit preview.

### Inpaint Realism Boundary

Telea and Navier-Stokes inpainting may be used only for bounded star-mask repair. It must not be used to synthesize absent astronomical structures.

Inpaint use must be gated by:

- star-mask confidence;
- mask coverage ratio;
- connected-component size distribution;
- before/after difference review;
- checks for nebula filaments, dust lanes, bright cores, or galaxy structure being included in the mask.

Default thresholds for automatic processing:

| Check | Default limit | Action when exceeded |
|---|---:|---|
| `star_mask_confidence` | must be `>= 0.30` | prefer external starless or review |
| full-frame `mask_coverage_ratio` | must be `<= 0.08` for nebula/galaxy, `<= 0.03` for clusters | review required |
| largest component area | must be `<= 1.5%` of frame | reject as likely non-star structure |
| edge-touching masked area | must be `<= 20%` of total mask | review crop/edge artifact first |
| inpaint radius | default `<= 5 px`; `> 8 px` requires review | review required |
| post-inpaint local difference | median absolute difference inside dilated mask should stay within neighboring background statistics | review if outlier |

These are conservative defaults, not astronomy constants. A target-specific workflow may tighten them, but automatic processing should not loosen them without recording the reason.

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

`edge_energy_delta`, contour count, and Canny/Sobel response are risk indicators only. They may support warnings such as halo risk, hard-edge risk, ringing risk, or over-sharpening risk. They must not be phrased as "detail recovered" unless the structure is visible in the linear or pre-enhancement source.

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

Required state labels:

| State | Meaning | Allowed CV operations |
|---|---|---|
| `linear` | unstretched sensor/stack data, possibly normalized with scale metadata | diagnostics, conservative denoise, star metrics, masks, DBE support |
| `nonlinear` | stretched processing image | style, local contrast, final denoise, artifact checks |
| `display_preview` | clipped/gamma/display-safe review image | visual review only |
| `mask` | scalar/boolean selection map | morphology, coverage, component stats |
| `master_output` | float TIFF/FITS or high-quality deliverable | final write/review only |

Hard bit-depth preservation rules:

- Do not feed `img_as_ubyte()` output back into `pipeline.py`, `star_tools.py`, `quality_metrics.py`, `gradient_removal.py`, or `enhance.py` processing steps.
- Do not use preview PNG/JPG files as evidence for faint signal, SNR, FWHM, color truth, or texture existence.
- If an OpenCV operation requires 8-bit input, keep the original float source and return the result to float with an explicit `precision_reduced_for_algorithm=true` note.
- For FITS-derived data, any normalization must preserve enough metadata to explain whether the array is linear and how it was scaled.

### Coordinate Convention

scikit-image uses `(row, col)` coordinates. Visualization, crop arguments, user-facing positions, WCS, and catalog projection usually use `(x, y)`.

Any boundary crossing must explicitly convert:

- `(row, col)` to `(x, y)` for display and user-facing JSON;
- `(x, y)` to `(row, col)` for masks and array indexing;
- normalized centers to pixel coordinates with documented width/height ordering.

This matters for masks, regionprops, local enhancement centers, catalog projection, and review overlays.

Field names must encode convention:

| Field suffix | Convention |
|---|---|
| `_rc` | `[row, col]`, array indexing / scikit-image |
| `_xy` | `[x, y]`, display / OpenCV / user-facing pixels |
| `_norm_xy` | normalized `[x / width, y / height]` |
| `_bbox_rc` | `[row_min, col_min, row_max, col_max]` |
| `_bbox_xywh` | `[x, y, width, height]` |

Generic names such as `center`, `bbox`, `point`, or `pixel` are not acceptable in new JSON contracts unless the parent schema explicitly defines the coordinate convention.

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

Default component-count interpretation:

- Very high component count after thresholding usually indicates noise, compression, or residual gradient, not rich astronomical structure.
- A sudden component-count increase after sharpening, CLAHE, or local enhancement is an artifact risk.
- Component count must be paired with area distribution, circularity/eccentricity, and source-stage comparison before being interpreted.

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

Object-level metrics must be worded as diagnostics. They can say "mask likely overbroad", "star residual risk", "halo risk", or "edge artifact risk". They must not say "real detail increased", "nebula structure recovered", or "resolution improved" without independent support from linear data, WCS/pixel scale, and visual review.

### CLAHE and Local Contrast Review

CLAHE and local contrast can make faint structures more visible, but they can also create halos, hard transitions, plastic texture, and false-looking high-frequency detail.

Any local contrast or CLAHE use should be reviewed with:

- before/after comparison;
- difference image;
- halo and hard-edge checks;
- high-frequency energy interpreted only as a risk indicator, not proof of real detail;
- target-type safety rules, especially for reflection nebulae, dark nebulae, galaxies, and star clusters.

CLAHE / local contrast limits:

- Do not apply CLAHE to display previews and reuse the result for processing.
- Do not apply aggressive CLAHE before star/mask detection unless the mask is explicitly marked as preview-only.
- If `high_frequency_energy_ratio` rises while local SNR or source-stage structure support is absent, report artifact risk rather than detail gain.
- Any visible halo along mask or luminance boundaries requires review or parameter rollback.

## Linear vs Nonlinear CV Boundaries

CV operations have different meanings depending on processing stage.

| Stage | Allowed CV use | Restricted CV use |
|---|---|---|
| Linear input | FITS/XISF diagnostics, background models, star FWHM, conservative denoise, mask seeds | CLAHE/style/local contrast as processing input |
| Safe preview | AI/human visual review, channel inspection | any downstream processing or metric truth |
| Linear starless/star layer | star mask confidence, inpaint validation, layer difference checks | broad inpaint, texture creation, aggressive sharpening |
| Nonlinear image | final color/style/local contrast, artifact detection, JPG review | physical SNR claims, true resolution claims |
| Final output | quality gates, visual review, artifact warnings | sensor/stack quality claims |

When a CV metric is computed on nonlinear or preview data, the report must label it as aesthetic or artifact-review evidence, not physical evidence.

## Target-Type Safety Rules

CV guardrails must vary by target type.

| Target type | OpenCV / scikit-image safety rule |
|---|---|
| Emission nebula | Protect filamentary H-alpha/OIII structure from star-mask overreach; inpaint masks crossing bright filaments require review. |
| Reflection nebula | Avoid aggressive CLAHE and chroma denoise that can destroy soft dust gradients; edge-energy increases are usually risk signals. |
| Galaxy | Do not classify dust lanes, knots, or core structures as stars/noise; largest connected components near the core require review. |
| Globular/open cluster | Stars are the subject; star removal/reduction masks should be disabled or extremely conservative. Mask coverage limit defaults to `<= 0.03`. |
| Dark nebula | Do not treat dark dust lanes as background defects; DBE, thresholding, and local contrast need source-stage comparison. |
| Planetary nebula | Small high-gradient structure is often real; edge/contour metrics must not trigger automatic smoothing without review. |
| Wide-field | Gradients and dense star fields make thresholding fragile; require stronger component filtering and avoid broad inpaint. |

If target type is unknown, use conservative nebula/galaxy limits and require review before broad inpaint, aggressive CLAHE, or star reduction.

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
