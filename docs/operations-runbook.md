# Operations Runbook

## Start and configuration

1. Install `requirements.txt` in an isolated environment.
2. Set `MARKET_INTELLIGENCE_DB` to the writable SQLite path.
3. Set `ENABLE_SCENARIO_LAB=true` only where what-if scenarios are permitted.
4. Launch with `streamlit run app.py`.
5. Keep external API keys in environment variables or Streamlit secrets; never store them in fixtures or exports.

## Data modes

- `fixture`: versioned synthetic internals for POC/customer demonstration.
- `connected`: injected Store, product, sourcing, inventory, route, staffing and observed-history adapters. Missing, stale, duplicate, incomplete, or schema-drifted records fail closed as `Internal operational data unavailable`.
- A connected-mode failure must not silently switch to fixtures.

## Operational checks

- Confirm layer-specific provenance and dataset as-of time before approving an action.
- Treat county-name geography as low-confidence; use polygon or FIPS evidence for operational decisions.
- Do not execute replenishment with stale inventory or a nonzero residual without escalation.
- Confirm order quantities respect case pack and MOQ.
- Keep what-if scenario results out of operational incident and decision tables.

## Incident response and rollback

1. Disable affected live connector/run source.
2. Preserve evidence export, source payload ID, error, and timestamp.
3. Revert to the last signed application package and database backup; do not overwrite the current database.
4. Restore service in clearly labeled fixture mode only for testing—not operational decisioning.
5. Re-enable connected mode only after connector contract, freshness, integrity, and end-to-end tests pass.
