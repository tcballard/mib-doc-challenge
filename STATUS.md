# Cloud validation run - COMPLETE (cross-check artifact)

Independent full-corpus run, used to cross-check the local (primary) run.
Disposable branch; the shipping predictions live on `submission/tcballard`.

## Result

- 5000/5000 rows, validator clean (0 missing case ids)
- **0 governor lines** - full depth held across the entire corpus
- Config: x86 4-vCPU, `--cpus 4`, `workers=8`, `MIB_FORCE_FULL_DEPTH=1`
- Pace ~8.4 s/PDF, ~11 h wall clock

## Agreement with the local run

- 53 / 5000 rows differ (1.06%)
- **1 adjudication flip** (NEEDS_REVIEW -> DENIED), i.e. 4999/5000 agree
- The flip is away from approval, so it is not a catastrophic-direction change
- Differing fields are OCR-derived on degraded scans (species_code, home_world,
  sponsor_id, applicant_name). Expected: arm64 vs x86 Tesseract 5.3.0 resolve
  marginal glyphs differently. Both runs were full depth, so this is
  architecture variance, not degradation.
