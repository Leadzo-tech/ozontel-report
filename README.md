# ozontel-report

Standalone Lambda (`ozonetel-cdr-proxy-poc`, ap-south-1) that pulls call
records from Ozonetel's `fetchCDRDetails` API and writes them into the
"Ozonetel" tab of the shared KPI Google Sheet.

## Why this exists as its own Lambda, not inside Apps Script

Ozonetel's `fetchCDRDetails` requires a literal GET request carrying a JSON
body. Google Apps Script's `UrlFetchApp` silently rewrites any GET+payload
request into a POST on the wire (confirmed with an echo-server test), and
Ozonetel's route 405s on POST. Python's `requests` library sends the method
exactly as given — confirmed working against the production endpoint via curl:
GET + `apiKey` header + JSON body -> 200 with real data.

## Files

- `handler.py` — Lambda entry point.
- `ozonetel_cdr.py` — pulls CDRs for the last N days, writes to the sheet.
- `ozonetel_cdr.yaml` — declarative config: sheet target + pull window.
  Matches the `scheduled_queries/*.yaml` pattern from data-pipelines — change
  the target spreadsheet/tab/columns here, no code change needed.
- `sheets_client.py` — gspread wrapper (flatten/truncate/batch-write).
- `secrets.py` — reads the Google service account JSON from SSM Parameter Store.

## Config

**`ozonetel_cdr.yaml`** (declarative, checked into the repo):
- `sheet.spreadsheet_id` / `sheet.worksheet_name` — where the data goes.
- `sheet.columns.preferred_order` — column order written to the sheet.
- `pull_window.days_back_routine` / `days_back_full` — how many days back a
  routine run vs. a full backfill pulls.

**Lambda environment variables** (secrets/deployment-specific, not in git):
- `OZONETEL_API_KEY`, `OZONETEL_USERNAME` — Ozonetel account credentials.
- `GOOGLE_SA_JSON_PARAM` — SSM parameter name holding the Google service
  account JSON (`/leadzo/ozonetel-cdr-proxy-poc/poc/google/sa-json`).

## Deploy

Push to `main` — `.github/workflows/deploy.yml` runs `pytest`, builds the
Lambda zip, deploys it via OIDC (role `ozonetel-cdr-proxy-poc-github-actions-role`,
no long-lived AWS keys), and reconciles the `ozonetel-cdr-sync-hourly`
EventBridge rule. Requires the repo secrets `OZONETEL_API_KEY` and
`OZONETEL_USERNAME` to be set (`gh secret set ... --repo Leadzo-tech/ozontel-report`).

`template.yaml` documents the current live shape of the function for
reference — it is **not** deployed by CI or meant to be `sam deploy`'d, since
the real resources were created manually and stay CLI/CI-managed (see the
comment at the top of that file).

Manual deploy (fallback, e.g. for local debugging without waiting on CI):

```bash
pip install --target build -r requirements.txt \
  --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 \
  --only-binary=:all:
cp handler.py ozonetel_cdr.py sheets_client.py secrets.py ozonetel_cdr.yaml build/
(cd build && zip -qr ../function.zip .)
aws lambda update-function-code --function-name ozonetel-cdr-proxy-poc \
  --zip-file fileb://function.zip --region ap-south-1
```

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

## Schedule

EventBridge rule `ozonetel-cdr-sync-hourly` (`rate(5 minutes)`) targets this
function directly, reconciled idempotently by the deploy workflow on every
push to `main`.
