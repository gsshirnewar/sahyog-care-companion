# Synthetic data inventory

| Dataset | Version | Source | Data classes | Intended use | License/rights | Status |
|---|---|---|---|---|---|---|
| `synthetic/fixtures/foundation.json` | v1 | Generated deterministic placeholders | Synthetic IDs, catalog, slots, sample report text | Local schema/integration checks only | Original project-generated fixture | Approved for synthetic demo only |
| `../evals/golden_sets/red_flags.v1.json` | v1 | Placeholder cases | Synthetic text and expected fail-closed action | Deterministic gate tests only | Original project-generated fixture | Clinical approval pending; not clinical guidance |
| `../evals/golden_sets/specialty.v1.json` | v1 | Placeholder cases | Synthetic text and demo labels | Contract/eval harness tests only | Original project-generated fixture | Clinical approval pending; not clinical guidance |

No real patient data, real clinician identity, or licensed clinical source is included. Do not populate this inventory with a public dataset until its exact version, license, permitted healthcare use, redistribution terms, and provenance have been reviewed by the data steward/legal owner.
