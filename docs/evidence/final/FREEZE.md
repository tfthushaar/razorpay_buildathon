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
- **Result** `699adba21f86e375d348c6d965dafea7651c4ac38cb13d22e2736b1b81a14e16`
  — intact
- **Sources, as they stood at that commit:**

| File | SHA256 at scoring | Now |
|---|---|---|
| `backend/scripts/generate_reading_evidence.py` | `c45fd6553a7f2082…` | **changed** |

> The code underneath changed, and this holdout **still produces the number**
> above, case for case. A changed hash is a reason to check, not a verdict --
> here the change turned out not to touch the result.

## three_source

- **Seed** `20260902` — scored 2026-09-01 at commit `356b298`
- **Result** `49f87fcecc4bc0464da43835fdd188f1ce31faa3ef49ffc699fa584d6a6fd35a`
  — intact
- **Sources, as they stood at that commit:**

| File | SHA256 at scoring | Now |
|---|---|---|
| `backend/app/data_gen/three_source.py` | `6fbe920222b4f7ff…` | **changed** |
| `backend/app/resolver/entity_resolution.py` | `d2f629e2cc3a5a9d…` | **changed** |
| `backend/app/resolver/fellegi_sunter.py` | `fab52eac0f7b4f54…` | unchanged |

> The code underneath changed and this holdout **no longer reproduces**. The
> figure stands as a record of what happened on the day it was scored and no
> longer describes what this code would do now. It is not re-scored: replacing
> a held-out answer once it became inconvenient is the exact thing the
> single-shot rule exists to prevent.

