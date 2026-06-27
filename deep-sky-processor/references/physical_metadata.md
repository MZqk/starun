# Physical metadata and astrometry priors

## Capture metadata

The normalized metadata object includes:

- `exposure_seconds`
- `gain`
- `sensor_temperature_c`
- `filter` and `filter_profile`
- `telescope`
- `camera`
- `object`
- `ra` / `dec`
- `date_obs`
- binning, pixel size, focal length, and aperture when available

Treat metadata as evidence, not ground truth. FITS writers use inconsistent
keywords and units.

## Physical interpretation rules

- Warm sensor temperature is direct evidence of elevated dark current.
- Numeric gain is camera-dependent. High gain often reduces highlight headroom;
  it does not universally increase read noise.
- Long subexposures require stronger star-core and highlight protection.
- Short subexposures increase sensitivity to read-noise accumulation.
- Dual-band metadata activates emission color handling, moderate SCNR, and
  H-alpha/OIII preservation.
- Do not claim true HOO/SHO synthesis from one OSC image unless a documented
  channel-separation model and filter response are available.

User overrides take precedence over physical priors. Record both in the result.

## Plate solving and object association

Astrometry.net `solve-field` establishes the image WCS: center coordinates,
pixel scale, orientation, and field size. It does not by itself identify every
object in the field.

Supply a catalog JSON to associate objects:

```json
[
  {
    "name": "NGC6888",
    "type": "emission_nebula",
    "ra_deg": 303.027,
    "dec_deg": 38.354
  }
]
```

The projector returns `pixel` and `normalized_center` for objects inside the
frame. Agent actions may then use:

```json
{
  "operation": "run_step",
  "step": "local_enhance",
  "object_name": "NGC6888",
  "object_radius": 0.12,
  "local_strength": 0.2
}
```

Do not describe the field inventory as complete unless the supplied catalog is
known to be complete to the required magnitude and object classes.

## Astropy Evidence contract

`astro-evidence.json` is the shared facts layer for Header, WCS, units, time,
and coordinate interpretation. FITS Header values must preserve their source
keyword. Ambiguous units use `camera_native` or `unknown` and lower confidence
instead of pretending to be physical units. Incomplete WCS may record present
keywords, but it must not be used for sky-coordinate interpretation.

Default behavior is offline. Do not call `SkyCoord.from_name()`, online
catalogs, remote FITS reads, IERS auto-refresh, or address resolution unless a
future explicit opt-in records the service, sent fields, and `network.used=true`
in the evidence.
