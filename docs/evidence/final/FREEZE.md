# Frozen holdouts

Each of these was scored once, on a seed no experiment had touched, and whatever came out
is what ships. This file is the receipt: the hash of the answer, and the hashes of the code
that produced it as that code stood on the day.

Re-check without trusting any of it:

```bash
cd backend && python scripts/freeze_holdout.py --check
```

`result` moving means a published number was edited after its single shot. That is an error.

`sources` moving means the code underneath changed. That is a reason to look, not a verdict:
one of the two holdouts below has drifted sources and still produces its number exactly, and
the other does not reproduce at all. Only `--reproduce` distinguishes them, and it never
rewrites a published figure -- it compares against it.

## reading

- **Seed** `20260901` — scored 2026-09-01 at commit `356b298`
- **Result** `87887e6032d394d3d8932d9fd2bb5dfb8c6ec37e9c93bb4b8fc33dadad95831e`
  — intact
- **Sources, as they stood at that commit:**

| File | SHA256 at scoring | Now |
|---|---|---|
| `backend/scripts/generate_reading_evidence.py` | `c45fd6553a7f2082…` | unchanged |

## three_source

- **Seed** `20260902` — scored 2026-09-01 at commit `356b298`
- **Result** `09c104724d397ea7e393a6d2be0b495054d90a57745016bc9939a5178cda93b8`
  — intact
- **Sources, as they stood at that commit:**

| File | SHA256 at scoring | Now |
|---|---|---|
| `backend/app/data_gen/three_source.py` | `6fbe920222b4f7ff…` | **changed** |
| `backend/app/resolver/entity_resolution.py` | `d2f629e2cc3a5a9d…` | unchanged |
| `backend/app/resolver/fellegi_sunter.py` | `fab52eac0f7b4f54…` | unchanged |

> The code underneath changed and this holdout **no longer reproduces**. The
> figure stands as a record of what happened on the day it was scored and no
> longer describes what this code would do now. It is not re-scored: replacing
> a held-out answer once it became inconvenient is the exact thing the
> single-shot rule exists to prevent.

