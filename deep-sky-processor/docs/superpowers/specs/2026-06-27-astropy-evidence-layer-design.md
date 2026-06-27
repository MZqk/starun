# Astropy Evidence Layer Design

## Context

`deep-sky-processor` already uses Astropy-adjacent concepts in several places:

- `fits_io.py` reads FITS headers, extracts capture metadata, and builds basic physical priors.
- `plate_solve.py` summarizes WCS from Astrometry.net output and projects user-provided catalog objects.
- `recognize.py` treats Header/WCS evidence as higher priority than visual classification.
- `quality_assessment.md` defines future-facing quality checks that need WCS, pixel scale, star shape, and catalog context.

The current behavior is useful, but the astronomy-specific interpretation is spread across scripts. FITS Header parsing, WCS handling, target coordinates, time fields, and physical units can diverge as more features are added. Borrowing from the Astropy skill should therefore mean adopting Astropy's scientific object discipline: units, coordinates, time scales, WCS, FITS HDU structure, and explicit network boundaries.

## Goal

Add a shared Astropy Evidence layer that normalizes astronomical metadata into a stable JSON contract. Downstream scripts should consume this evidence instead of re-parsing FITS headers or guessing physical meaning independently.

The evidence layer must improve:

- target recognition and physical priors;
- adaptive processing decisions;
- quality review explanations;
- Starun automation outputs;
- future catalog and WCS-based enhancements.

It must not turn the skill into a general astronomy data-analysis package. The core product remains realistic deep-sky image post-processing.

## Non-Goals

- No online catalog lookup by default.
- No `SkyCoord.from_name()` or other network-backed resolver by default.
- No strict photometric color calibration in the first implementation stage.
- No cosmology workflow in the post-processing path.
- No full pipeline rewrite.
- No generation or repair of image structures from catalog data.

## Architecture

Add `deep-sky-processor/scripts/astro_metadata.py`.

This module owns only astronomy metadata interpretation. It does not modify pixels and does not choose final aesthetic parameters. Its output is an `astro-evidence.json` file with a stable schema.

Existing responsibilities remain separated:

- `fits_io.py`: pixel I/O, base header extraction, image scaling, and write behavior.
- `astro_metadata.py`: FITS/XISF metadata interpretation using Astropy objects and explicit evidence confidence.
- `recognize.py`: target recognition workflow and visual/CV evidence reconciliation.
- `pipeline.py`: image processing and adaptive parameter selection.
- `quality_metrics.py`: numeric and evidence-aware quality review.
- `run_starun_processing.py`: Starun contract integration.

Downstream code reads `astro-evidence.json` as a shared facts layer. It may use evidence for bounded recommendations and explanations, but missing evidence must not be converted into a claim.

## Evidence Contract

The first schema version is `1.0`.

Representative shape:

```json
{
  "schema_version": "1.0",
  "source": "fits_header",
  "fits": {
    "hdu_index": 0,
    "hdu_name": "PRIMARY",
    "bitpix": -32,
    "shape": [3000, 4000],
    "has_bscale_bzero": true
  },
  "capture": {
    "exposure": {
      "value": 300,
      "unit": "s",
      "source_key": "EXPTIME",
      "confidence": "high"
    },
    "gain": {
      "value": 120,
      "unit": "camera_native",
      "source_key": "GAIN",
      "confidence": "medium"
    },
    "date_obs": {
      "value": "2026-01-01T12:00:00",
      "scale": "utc",
      "source_key": "DATE-OBS",
      "confidence": "medium"
    },
    "filter": {
      "raw": "L-eXtreme",
      "class": "dual_band",
      "lines": ["H-alpha", "OIII"]
    }
  },
  "coordinates": {
    "target": {
      "name": "NGC6888",
      "ra_deg": 303.027,
      "dec_deg": 38.354,
      "frame": "icrs",
      "source": "header"
    },
    "wcs": {
      "available": true,
      "pixel_scale_arcsec": 2.1,
      "field_width_deg": 1.8,
      "field_height_deg": 1.2
    }
  },
  "priors": {
    "confidence": "medium",
    "recommendations": ["use_emission_color_mode", "protect_highlights"],
    "parameter_hints": {
      "hdr_strength_min": 0.45
    },
    "warnings": []
  },
  "network": {
    "used": false,
    "services": []
  }
}
```

Fields may be absent when unsupported by the input format. Any ambiguous value must include a confidence level or warning.

## Data Flow

### Phase 1: Generate Evidence

`recognize.py` and `run_starun_processing.py` call `astro_metadata.py` after reading the input. They write `astro-evidence.json` alongside recognition artifacts or Starun output artifacts.

The evidence layer parses:

- FITS HDU index/name, shape, dtype, `BITPIX`, `BSCALE`, and `BZERO`;
- exposure, gain, sensor temperature, filter, telescope, camera, binning, pixel size, focal length, aperture, object, RA, Dec, and `DATE-OBS`;
- WCS availability and pixel scale where valid;
- optional plate-solve results and user-provided catalog projections.

Header and WCS evidence remains higher priority than visual classification. Visual classification can add interpretation, but cannot silently override reliable Header/WCS identity.

### Phase 2: Consume Evidence in Adaptive Processing

`pipeline.py` consumes evidence when present. Existing physical-prior logic can either move into `astro_metadata.py` or become a consumer of the evidence contract.

Examples:

- long subexposures increase star-core and highlight protection;
- dual-band filters recommend emission color handling and H-alpha/OIII preservation;
- sparse metadata lowers confidence and keeps adaptive overrides conservative;
- WCS pixel scale lets FWHM be reported in arcseconds when valid;
- user `--override-params` remains the highest priority.

Evidence can influence bounded parameter hints. It must not create synthetic color or structure claims.

### Phase 3: Consume Evidence in Quality Review

`quality_metrics.py` accepts evidence and optional plate-solve/catalog artifacts.

When valid evidence exists, reports may include:

- pixel scale;
- FWHM in pixels and arcseconds;
- whether the declared target falls inside the frame;
- projected catalog object count;
- coordinate records for region-based SNR checks;
- explicit limits when catalog or WCS information is absent.

Without a local catalog and photometric model, the report must call color review heuristic. It must not claim strict photometric truth.

## Error Handling

Astropy-related failures are recoverable unless the input image itself cannot be read.

Expected behavior:

- Missing Header fields produce warnings and lower confidence.
- Invalid WCS sets `coordinates.wcs.available=false`.
- Failed coordinate parsing preserves the raw values and does not overwrite target identity.
- Multi-HDU FITS files record the selected HDU and selection reason.
- Unit-ambiguous values use `camera_native`, `unknown`, or a warning instead of pretending to be physical units.
- Starun entrypoint keeps running on metadata interpretation failures and records evidence warnings in output.

## Network Safety

Default behavior is offline.

Do not call these without an explicit future opt-in flag:

- `SkyCoord.from_name()`;
- remote FITS reads;
- online catalog queries;
- `EarthLocation.of_address()`;
- IERS auto-refresh or other network cache updates.

If a future option enables network access, the evidence must record:

- `network.used=true`;
- service names;
- which input fields were sent;
- whether the call affected target identity or only enriched context.

## Testing

Add focused tests rather than one broad end-to-end test.

Planned tests:

- `test_astro_metadata.py`: synthetic FITS headers for exposure, filter, time, RA/Dec, WCS, `BSCALE/BZERO`, and multi-HDU selection.
- `test_plate_solve.py`: verify WCS summary and catalog projection can be represented in the evidence schema.
- `test_recognize.py`: verify `recognition.json` references `astro-evidence.json` and Header/WCS precedence is preserved.
- `test_pipeline_recognition.py` or adaptive tests: verify pipeline can consume evidence while user overrides remain highest priority.
- `test_starun_entrypoint.py`: verify Starun output preserves evidence summary, warnings, quality gates, and does not rewrite low-confidence evidence into successful facts.

## Implementation Stages

### Stage 1: Minimal Evidence Loop

- Add `astro_metadata.py`.
- Generate `astro-evidence.json` for FITS and XISF-supported metadata.
- Wire evidence generation into `recognize.py` and `run_starun_processing.py`.
- Add tests for the evidence schema and degradation behavior.

### Stage 2: Adaptive Processing Consumption

- Let adaptive processing read evidence.
- Replace duplicated Header interpretation where practical.
- Preserve existing behavior for image-only formats and missing metadata.

### Stage 3: Quality Review Consumption

- Extend quality reporting with evidence-aware WCS and FWHM fields.
- Add catalog projection summaries when a local catalog is supplied.
- Keep photometric truth claims out of scope unless a calibrated local star catalog workflow is later designed.

## Acceptance Criteria

- FITS inputs produce `astro-evidence.json` with schema version `1.0`.
- Missing or malformed astronomy metadata degrades with warnings instead of crashing the processing flow.
- Header/WCS identity remains higher priority than visual/CV classification.
- Adaptive processing can consume evidence without overriding explicit user parameters.
- Starun output carries evidence warnings and does not convert low-confidence evidence into success claims.
- Network-backed Astropy features remain disabled by default.
