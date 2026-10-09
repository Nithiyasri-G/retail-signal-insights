# Running the revised Market Intelligence Agent

Use the entire folder, not only app.py: `app.py` is a thin entry point and the application lives in the `market_intelligence` package (see `ARCHITECTURE.md`). This is the modular edition of the original single-file app and behaves identically to it.

## Windows setup (Python 3.12 recommended)

Open a terminal inside the extracted `Market_Intelligence_Agent` folder:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
notepad .env
.venv\Scripts\python.exe -m streamlit run app.py
```

If using an existing Python environment:

```text
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Add your NVIDIA key to `.env` beside app.py. Keep the following settings:

```dotenv
NVIDIA_API_KEY=your_key_here
NVIDIA_MODEL=nvidia/nemotron-3-super-120b-a12b
NVIDIA_BACKUP_MODEL=meta/llama-3.3-70b-instruct
```

Keep your real key private. No keys, database, previous incidents or runtime caches are included in this ZIP. Existing environment variables override `.env`; restart Streamlit after changing either.

## NVIDIA behavior

- Primary: Nemotron 3 Super 120B A12B, retained as the primary capable model.
- Reasoning is disabled for Nemotron requests because these screens need a final business explanation, not a reasoning trace.
- Output budget: 1,400 tokens initially, 1,800 on retry, instead of the old 260–380 tokens. Each attempt has a 60-second timeout.
- The second attempt keeps all five report sections and includes the previous failure reason. A third attempt uses the configured backup model.
- Invalid credentials stop immediately. Rate limits, service/network errors, unavailable models, truncation and invalid report content remain visible in the audit. No code can bypass account quotas or model access requirements.
- HTTP 410 Gone stops retries on the same endpoint. NVIDIA still documents the hosted chat URL, so check service/account status with NVIDIA before setting `NVIDIA_CHAT_URL` to a different URL supplied for your deployment. A 503 Service Unavailable remains eligible for model retry.
- The deterministic local summary remains an honest final fallback when no validated answer is available. A model response is never relabelled as a success merely to hide fallback.
- Use **Credentials → Run connectivity test** before your rehearsal. If it fails, enable `SHOW_LLM_TRACE=true` and inspect the attempt diagnostics. The provider must permit the model for your API key.
- **Regenerate NVIDIA brief** retries only the brief against saved source evidence; it does not rerun the external collectors. Previous brief revisions are retained.

NVIDIA references checked for this implementation:

- https://build.nvidia.com/nvidia/nemotron-3-super-120b-a12b/modelcard
- https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-super-120b-a12b-infer
- https://build.nvidia.com/meta/llama-3_3-70b-instruct/modelcard

## Rehearsal

1. Confirm Dollar Tree in Retail context. Check NVIDIA connectivity.
2. Run Intelligence with the desired public sources. Apify is never called by this button.
3. In Results, inspect source availability, distinct state labels, signal explanations and Executive Brief.
4. In Operational Impact Center, review a live incident if available. Internal inventory, stores, routing and staffing remain synthetic fixtures.
5. For a repeatable controlled example, open the Operational Impact Center, choose the What-if scenario tab, create a Weather or Recall scenario, and use **Open scenario detail**. Impact, Evidence and Proposed Actions are isolated from live approvals.
6. In Demand Planner, analyze the selected signal/store. Changing inputs invalidates the prior result. Gap sums product shortages; other products' surpluses do not erase them.
7. Download Evidence Audit. Exact request bodies, raw answer content, validation outcomes and accepted-attempt identity are included.
8. Optional, only if ENABLE_SIGNAL_SEARCH=true (needs an Apify account): search a retail product or topic in Signal Search. This makes a paid Apify call and shows regional and related searches without a risk score. Searches remain separate from Run Intelligence and can be restored from local history.

## Operational rules and limits

- Current recall view means **Ongoing**, sorted newest first and limited by configured record count. It is not a complete FDA history archive. Old ongoing recalls remain relevant; terminated recalls do not enter the current signal.
- Confirmed quantities require exact UPC/lot plus a resolvable distribution footprint. Ambiguous identity/geography produces a review state without exposure quantities. Lot quantities are synthetic proportional shares of all known product lots, not actual lot-level inventory.
- Multi-state geography uses all queried states for text fallback and the wider store master for authoritative geometry/geocodes. Valid polygon exclusion is not overridden by county text; missing-coordinate fallback is labelled.
- Shared ATP is reserved across Stores and incidents within one run in deterministic alert-ID/Store order. This is a reproducible POC allocation order, not business-priority optimization or an ERP stock reservation.
- Candidate DCs use the fixture sourcing lanes. Primary route exposure blocks the proposed supply path. No usable alert deadline means no timed allocation. Unknown external route conditions remain a POC limitation.
- Inventory age and inbound arrival are not live-validated. Capture time and synthetic provenance are displayed; inbound availability is an explicit planning-horizon assumption. There are no production connectors activated.
- Operational snapshots are frozen to the source run. A new assessment resets the operational approval status. Decision history records its assessment ID and payload fingerprint.
- The report validator checks structure, source references, unsupported numbers and common unsupported internal-performance claims. These checks do not constitute a general factual guarantee; a planner should review generated interpretations.

## Testing

```text
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The suite (195 tests including architecture-boundary guards and a headless Run Intelligence test) passes in the development environment. All populated navigation views and both incident detail sets were exercised through Streamlit AppTest. Live service availability, account access and browser pixel layout were not certified. Complete one live rehearsal in your deployment environment before client sign-off.
