# Market Intelligence Data Dictionary

All timestamps are ISO 8601 UTC. Synthetic records are versioned under `data/fixtures/v1` and are never labeled as live internal operations.

| Dataset | Key | Important fields | Decision use |
|---|---|---|---|
| Stores | Store ID | State, County, FIPS, Latitude, Longitude, Nearby DC ID | Polygon/FIPS/text geography mapping |
| Products | UPC | Category, Case Pack, MOQ, Sellable Unit | Executable order rounding |
| Store-SKU-DC lanes | Store ID + UPC + Rank | DC ID, Service Level, Transit Hours, Active | Product-specific primary/backup sourcing |
| Store inventory | Store ID + UPC | On Hand, Inbound, ATP components, Captured At | Forecast shortfall and Store readiness |
| DC inventory | DC ID + UPC | On Hand, Committed, Safety Stock, Eligible Inbound, ATP, Captured At | DC allocation and freshness gate |
| Routes | DC ID + Store ID | ETA Hours, Eligible, Route Counties, Captured At | Arrival feasibility and route risk |
| Staffing | Store ID + Employee Token | Shift, Scheduled Hours, On-call Eligible | Store operating-capacity assessment |
| Weather assumptions | Event Type + Region + Event ID + Phase | Demand Uplift, Traffic Delta, Status | Synthetic POC scenario calculations only |
| Observed event history | Event ID | NOAA source ID, FIPS, baseline/event windows, POS units, coverage, sample size | Historical uplift only after evidence gates pass |
| Scenario runs | Scenario ID | Assumption Version, Created By/At, Assumptions, Result | Isolated POC workflow testing |

Core formulas:

- Forecast shortfall = `max(0, projected demand - Store on hand - eligible inbound)`.
- Order quantity = forecast shortfall rounded up to sellable units, then case pack and MOQ.
- ATP = `max(0, on hand - committed - safety stock + eligible inbound before cutoff)`.
- Residual = `order quantity - primary supply - backup supply`.
- Observed uplift = `(mean event POS / mean baseline POS - 1) × 100`.
