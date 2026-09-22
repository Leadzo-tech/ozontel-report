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
- `sheets_client.py` — gspread wrapper (flatten/truncate/batch-write).
- `secrets.py` — reads the Google service account JSON from SSM Parameter Store.

## Config (Lambda environment variables)

- `OZONETEL_API_KEY`, `OZONETEL_USERNAME` — Ozonetel account credentials.
- `GOOGLE_SA_JSON_PARAM` — SSM parameter name holding the Google service
  account JSON (`/leadzo/ozonetel-cdr-proxy-poc/poc/google/sa-json`).

## Deploy

```bash
pip install --target build -r requirements.txt \
  --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 \
  --only-binary=:all:
cp handler.py ozonetel_cdr.py sheets_client.py secrets.py build/
(cd build && zip -qr ../function.zip .)
aws lambda update-function-code --function-name ozonetel-cdr-proxy-poc \
  --zip-file fileb://function.zip --region ap-south-1
```

## Schedule

EventBridge rule `ozonetel-cdr-sync-hourly` (`rate(1 hour)`) targets this
function directly — no CI/CD wired up yet.
