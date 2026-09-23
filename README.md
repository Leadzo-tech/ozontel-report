# Ozonetel Report

Schedule-driven Ozonetel CDR → Google Sheets exporter running on AWS Lambda.

Each scheduled report is a YAML file in `scheduled_reports/`. The Lambda pulls
call detail records from Ozonetel's `fetchCDRDetails` API and overwrites a
worksheet with them. On every push to `main`, CI builds the Lambda code,
deploys it, and reconciles EventBridge rules so the schedules declared in YAML
match what's live in AWS. Push to `main` is the only step you need.

Currently one report ships: `ozonetel-cdr-sync` — pulls the last 2 days plus
today and merges them (by `CallID`) into the full call history in the
`Ozonetel` tab of the call-KPI sheet, every 5 minutes.

Before changing anything, read two sections: **"Know your target: the Ozonetel
API"** (the API's contract is unusual and its error messages lie) and
**"The target sheet, and what else writes to it"** (a second implementation of
this sync exists in another repo).

## How to add or change a scheduled report

1. Drop a new file in `scheduled_reports/<report-name>.yaml`. Minimum shape:

   ```yaml
   report_name: my-new-report
   enabled: true
   schedule:
     # EventBridge: rate(...) or cron(...). cron is UTC — 07:15 UTC = 12:45 IST.
     expression: rate(15 minutes)
     timezone: Asia/Kolkata          # documentation only; EventBridge cron is UTC
   ozonetel:
     endpoint: https://in1-ccaas-api.ozonetel.com/ca_reports/fetchCDRDetails
     days_back_routine: 2
     days_back_full: 15
     rate_limit_sleep_seconds: 31
     projection:                      # header text: Ozonetel field
       Call ID: CallID
       Call Date: CallDate
       Talk Time: TalkTime
   sheet:
     spreadsheet_id: 1WsTggC...      # from /d/<id>/edit in the sheet URL
     worksheet_name: Ozonetel
     start_cell: A1
     include_headers: true
     clear_before_write: true

   notifications:
     slack_webhook_param: /leadzo/ozonetel-cdr-proxy-poc/poc/slack/default-webhook-url
   ```

2. **Share the target sheet** with the service-account email
   (`query-scheduler@leadzo-497107.iam.gserviceaccount.com`) as **Editor**.
   Without this the Lambda gets a 403 from gspread.

3. Commit + push to `main`. CI deploys the code and creates an EventBridge rule
   `oz-prod-<report-name>` pointing at the Lambda.

To change the cadence of an existing report, edit `schedule.expression` and
push — `src/eventbridge_sync.py` updates the live rule to match. To turn one
off, set `enabled: false` (or delete the file); the rule is removed.

## The ship cadence (read this first)

Follow this every time you add or change a report. Written so a teammate — or
an AI agent — can do it end-to-end without guessing.

1. **Check the field names you want actually exist** in the API response. Pull
   one day by hand (see "Verify before trusting the schedule") and look at the
   keys. Ozonetel returns 47 columns; anything you point `projection` at that
   the API doesn't return is silently written as an empty column.
2. **Write the spec** in `scheduled_reports/<name>.yaml`.
3. **Mind the pull window.** Ozonetel retains **15 days**. `days_back_routine`
   is what a normal run pulls; `days_back_full` is the ceiling for a backfill.
   Each day costs one API call plus a ~31s sleep (rate limit, below), so a
   15-day backfill takes ~8 min (CI sets the Lambda timeout to 900s so it
   fits). Keep the scheduled window small; run backfills as a deliberate
   one-off.
4. **Share the sheet** with the service account as Editor (one-time per sheet).
5. **Lint locally:** `python -m src.validate_specs` — catches bad schedule
   expressions, windows beyond retention, missing sheet fields.
6. **Commit + push.** CI deploys the code and reconciles the EventBridge rule.
7. **Invoke it for real immediately** — do *not* wait for the schedule. See
   below. If the sheet isn't shared, or a field name is wrong, you find out in
   seconds.
8. **Confirm the sheet actually changed**, not just that the run returned 200.

Skip step 7 and the failure mode is quiet: the schedule fires, the run errors,
the sheet keeps showing stale data, and unless the Slack webhook param is set,
nobody is told.

## Know your target: the Ozonetel API

This is the part that cost the most time. All of it was verified by curl
against production, not inferred from docs — the docs are wrong or misleading
on two of these points.

### `fetchCDRDetails` needs a literal GET carrying a JSON body

```
GET /ca_reports/fetchCDRDetails
apiKey: <key>
Content-Type: application/json

{"fromDate":"2026-09-21 00:00:00","toDate":"2026-09-21 23:59:59","userName":"leadzo"}
```

GET-with-a-body is unusual but it is what the route requires. Sending the same
fields as query-string parameters returns
`{"status":"false","message":"Invalid Json Pass Valid Json"}`. Sending them as
a POST body returns a 401 or a bare `405 Method Not Allowed`, depending on
which headers are present.

### Auth is the `apiKey` header — **not** a Bearer token

The account *can* mint a token:

```bash
curl -X POST https://in1-ccaas-api.ozonetel.com/ca_reports/CAToken/generateToken \
  -H 'apiKey: <key>' -H 'Content-Type: application/json' \
  -d '{"userName":"leadzo"}'
# -> 200 {"token":"eyJhbGciOiJIUzUxMiJ9..."}
```

That token is real and valid. `fetchCDRDetails` still rejects it:

```
401 {"status":"false","message":"Missing userName or apiKey"}
```

…even though `userName` is plainly in the body. **That error message is a
generic auth failure, not a literal statement about missing fields.** Reading
it literally sends you on a long hunt for a malformed request. Send the
`apiKey` header and drop the `Authorization` header entirely and it works.

### Why this is a Lambda and not a Google Apps Script

The original version of this was an Apps Script bound to the sheet. It could
never work, for a reason that has nothing to do with the code:

**`UrlFetchApp` silently rewrites any GET request carrying a payload into a
POST.** Proven by sending an identical request to an echo server:

```javascript
UrlFetchApp.fetch('https://httpbin.org/anything', {
  method: 'get',                       // explicitly GET
  payload: JSON.stringify({...}),
  contentType: 'application/json',
});
// echo server reports:  "method": "POST"
```

Since Ozonetel's route requires a true GET and 405s on POST, Apps Script cannot
call this endpoint at all. Python's `requests` sends the method exactly as
given (`requests.request("GET", url, json=payload)`), which is why the sync
lives here. Don't "simplify" this back into Apps Script.

### Rate limit, retention, and day boundaries

- **2 requests/minute** on `fetchCDRDetails`. `rate_limit_sleep_seconds: 31`
  in the spec is the gap between per-day calls. Lower it and runs start failing.
- **15 days** of retention. Older dates return empty, not an error.
- **`fromDate` and `toDate` must be the same calendar day.** The API serves one
  day per call; that's why the code loops day-by-day rather than passing a range.

### The response shape

47 columns per record, in this order:

```
CallID, CallDate, StartTime, EndTime, Duration, TalkTime, HandlingTime,
CallerID, DialedNumber, E164, DID, Location, AgentID, AgentName, Skill,
CampaignName, CallFlow, Type, Status, DialStatus, AgentDialStatus,
CustomerDialStatus, Disposition, Comments, HangupBy, QueueTime, HoldDuration,
WrapupDuration, TimeToAnswer, DialCount, UCID, UUI, CallAudio,
CallerConfAudioFile, ConferenceDuration, CustomerRingTime, DialOutName,
DynamicDID, Event, PickupTime, Rating, RatingComments, TransferType,
TransferredTo, VideoRecordingURL, WrapUpEndTime, WrapUpStartTime
```

Durations are `HH:MM:SS` strings, not seconds — a sheet formula summing them
needs to parse, not add. `Status` is `Answered` / `Unanswered`; the finer-grained
outcome is in `DialStatus` / `AgentDialStatus` / `CustomerDialStatus`.
`Disposition` is only populated if agents actually set one in CloudAgent.

## Writing the spec

### `ozonetel` block

| Field | Meaning |
|---|---|
| `endpoint` | Full URL. Domestic: `in1-ccaas-api.ozonetel.com`; international: `api.ccaas.ozonetel.com`. |
| `projection` | The sheet's columns — see below. |
| `days_back_routine` | Days pulled on a normal run. 2 picks up late-arriving/updated records without re-pulling everything. |
| `days_back_full` | Ceiling for `full_backfill: true` invokes. Cannot exceed 15 (validator rejects it). |
| `rate_limit_sleep_seconds` | Gap between per-day calls. Keep ≥31 for the 2 req/min limit. |

Credentials are **not** in the spec — `OZONETEL_API_KEY` / `OZONETEL_USERNAME`
are Lambda environment variables, injected by CI from GitHub secrets.

### `sheet` block

| Field | Meaning |
|---|---|
| `spreadsheet_id` | From the sheet URL: `/spreadsheets/d/<id>/edit`. |
| `worksheet_name` | Tab name. Created if missing. |
| `start_cell` | Top-left of the written range, usually `A1`. |
| `include_headers` | Write a header row from the resolved column list. |
| `write_mode` | `overwrite` (default) = rewrite the tab with just this run's rows. `merge` = keep every row already on the tab and upsert this run's rows by `merge_key`, so history accumulates past Ozonetel's 15-day retention. Columns dropped from the projection are dropped from old rows too. |
| `merge_key` | Column (projection output name) that identifies a record in `merge` mode, e.g. `CallID`. |
| `clear_before_write` | `overwrite` mode only: `true` = wipe the tab first. |
| `columns.preferred_order` | Only for specs with no `projection`: orders these fields first, then appends every other field the API returned, alphabetically. Setting both is rejected. |

### Columns: adding, removing, renaming

`ozonetel.projection` is the column list. Header text on the left, the Ozonetel
API field it reads from on the right:

```yaml
ozonetel:
  projection:
    Call ID: CallID
    Call Date: CallDate
    Talk Time: TalkTime
    Agent: AgentName
```

| To do this | Do that |
|---|---|
| **Add** a column | Add a line. The right side must be one of the 47 field names in "The response shape". |
| **Remove** a column | Delete the line. Unlisted API fields are dropped. |
| **Rename** a header | Change the left side. The right side keeps the data mapping intact, so renaming can't break anything. |
| **Reorder** | Move the line. Projection order *is* column order. |

This mirrors how the query-scheduler specs do it — all 27 of them shape their
sheet in a single `$project` stage (`seller_name: "$company.name"`) rather than
with separate select/rename/order settings. Same idea, minus the Mongo.

A projected field Ozonetel doesn't return writes an empty column rather than
failing, so the sheet's shape stays fixed whatever a given day's data contains.

Changing columns needs a **deploy** (the spec ships inside the Lambda zip) —
unlike `schedule.expression`, which the reconciler applies on its own.

**Don't edit headers in the sheet directly** — every run
rewrites row 1 from this config every run, so manual edits last ~5 minutes.

Cells are clamped to 50,000 characters (`src/sheets_client.py`) because Sheets
rejects the entire write if one cell exceeds it.

## Verify before trusting the schedule

Step 7 of the cadence, and the habit that matters most. A real invoke runs the
whole path including the sheet write — there is no dry-run mode here (unlike
query-scheduler, there's no expensive database to protect, and the thing you
actually need to verify *is* the sheet write).

```bash
aws lambda invoke --profile leadzo --region ap-south-1 \
  --function-name ozonetel-cdr-proxy-poc \
  --cli-binary-format raw-in-base64-out \
  --payload '{"report_name":"ozonetel-cdr-sync"}' \
  /tmp/out.json && cat /tmp/out.json
```

Reading the result:

- `{"status":"success","rows_read":N,"rows_written":N}` → working. Check the
  tab actually changed.
- `Ozonetel API error ... Missing userName or apiKey` → auth. `OZONETEL_API_KEY`
  is wrong/unset, or something re-added an `Authorization` header.
- `Ozonetel HTTP 429` / repeated failures partway through a multi-day pull →
  rate limit; raise `rate_limit_sleep_seconds`.
- `gspread.exceptions.APIError: [403]` → the sheet isn't shared with the
  service account as Editor.
- `rows_read: 0` for every day → check the dates. Beyond 15 days back returns
  empty rather than erroring.

Backfill the full retention window:

```bash
aws lambda invoke --profile leadzo --region ap-south-1 \
  --function-name ozonetel-cdr-proxy-poc \
  --cli-binary-format raw-in-base64-out \
  --payload '{"report_name":"ozonetel-cdr-sync","full_backfill":true}' \
  /tmp/out.json && cat /tmp/out.json
```

15 days × ~31s of sleeping is ~8 minutes; CI sets the Lambda timeout to 900s
so this fits. With `write_mode: merge` it adds to the history, never replaces it.

Read logs for a failed scheduled run:

```bash
aws logs filter-log-events --profile leadzo --region ap-south-1 \
  --log-group-name /aws/lambda/ozonetel-cdr-proxy-poc \
  --filter-pattern '"failure"' \
  --start-time $(( ($(date +%s) - 3600) * 1000 )) \
  --query 'events[].message' --output text
```

## The target sheet, and what else writes to it

Spreadsheet `1WsTggCbZbSBV4DeAzznebcH51o8pJUFLOdVJMFMrhE0`
([open](https://docs.google.com/spreadsheets/d/1WsTggCbZbSBV4DeAzznebcH51o8pJUFLOdVJMFMrhE0/edit)),
tab **`Ozonetel`**. That tab is machine-owned: every run reads it back, merges
in the last `days_back_routine` days plus today by `CallID`, and rewrites it.
It is the only copy of calls older than Ozonetel's 15 days — don't delete rows
there. Hand-edited cells are overwritten on the next run — build derived views in *other* tabs that reference
it.

**Known issue, not caused by this service:** the `KPI Dashboard` tab in the
same spreadsheet is full of `#REF!` / `#VALUE!` errors and reads
"Data through Dec 30, 1899". Those formulas came from an earlier Google Apps
Script (`buildDashboard()`) and reference a column layout that no longer
matches. This service only ever writes the `Ozonetel` tab and never touches
`KPI Dashboard`; fixing those formulas is separate work.

### ⚠️ A second implementation exists — don't let both run

The same Ozonetel sync also lives in the **`Leadzo-tech/data-pipelines`** repo
(the query-scheduler), as `src/ozonetel_cdr.py` dispatched by
`handler.py::_NON_MONGO_HANDLERS["ozonetel-cdr-sync"]`. The
`leadzo-prod-query-scheduler-worker` Lambda still carries `OZONETEL_API_KEY`
and `OZONETEL_USERNAME` env vars and **writes to this same spreadsheet and
tab**.

Current state (verified): it is **deployed but not scheduled** — no EventBridge
rule invokes its Ozonetel path, so in normal operation only this repo writes
the sheet. But it can still be fired by hand via the `Ozonetel CDR Sync
(manual)` `workflow_dispatch` action in that repo, which would overwrite the
tab from a separate codebase with its own (possibly drifted) column list and
pull window.

If you're changing how this sheet is written, check that repo too. The clean
end-state is deleting the Ozonetel path from `data-pipelines` so there's one
owner — that hasn't been done, and it isn't this repo's call to make.

## One-time AWS setup (already done — for reference)

Account `018100542607`, region `ap-south-1`. CLI examples use
`--profile leadzo`; drop it if your default profile is already this account.

### SSM parameters

All under the prefix `/leadzo/ozonetel-cdr-proxy-poc/poc/`.

| Path | Type | Exists? | Purpose |
|---|---|---|---|
| `…/google/sa-json` | SecureString | yes | Google service account JSON (whole file). Copied verbatim from `/leadzo/query-scheduler/prod/google/sa-json` — deliberately the *same* SA, so sheets only need sharing with one Google identity. |
| `…/slack/default-webhook-url` | SecureString | **no** | Slack webhook for failure alerts. Until it's created, alerts no-op silently (by design — `get_optional_parameter` treats a missing param as "not configured"). |

```bash
# create / rotate
aws ssm put-parameter --profile leadzo --region ap-south-1 \
  --name /leadzo/ozonetel-cdr-proxy-poc/poc/slack/default-webhook-url \
  --type SecureString --value "file://<file>" --overwrite
```

### Where every credential lives (rotation checklist)

There are only three secrets in this system. Rotating any of them means
updating **every** place listed, not just one:

| Secret | Lives in | Rotate by |
|---|---|---|
| Ozonetel API key | Lambda env var `OZONETEL_API_KEY` **and** GitHub repo secret `OZONETEL_API_KEY` | `gh secret set OZONETEL_API_KEY --repo Leadzo-tech/ozontel-report` **and** `aws lambda update-function-configuration --environment ...` (env update replaces *all* vars — re-send the full set, don't send one key). Note the same key is also on `leadzo-prod-query-scheduler-worker` — see "Related systems" below. |
| Google service account JSON | SSM `…/google/sa-json` here, and `/leadzo/query-scheduler/prod/google/sa-json` in the other repo | Regenerate the key in GCP, `put-parameter --overwrite` on **both** paths. Nothing caches it beyond a warm Lambda container. |
| Slack webhook | SSM `…/slack/default-webhook-url` (not yet created) | `put-parameter --overwrite`. |

The Ozonetel account itself is `userName: leadzo`; the key comes from the
CloudAgent admin panel. The CloudAgent setting **API Authentication must be
`TOKEN_AUTH`** for the reports API to be reachable at all.

### Lambda

`ozonetel-cdr-proxy-poc` — python3.12, handler `src.handler.handle`, 512 MB,
900s timeout, **reserved concurrency 1** (every run reads and rewrites the
same worksheet; two concurrent runs would race on the same range).

Environment variables:

| Variable | Required | Purpose |
|---|---|---|
| `OZONETEL_API_KEY` | yes | Ozonetel API key. Injected by CI from a GitHub secret. |
| `OZONETEL_USERNAME` | yes | CloudAgent account name (`leadzo`). |
| `GOOGLE_SA_JSON_PARAM` | yes | Full SSM path of the service account JSON. |
| `SSM_PARAMETER_PREFIX` | no | Only used as a fallback by `parameter_name()` when an explicit `*_PARAM` var is absent. Not set today, because `GOOGLE_SA_JSON_PARAM` is. |
| `ENVIRONMENT` | no | Stamped into logs; defaults to `prod`, and EventBridge passes it in the event anyway. |
| `GIT_SHA` | no | Stamped into logs. Set by CI; reads `unknown` on manual deploys. |

### IAM identities

| Identity | What it is | Access |
|---|---|---|
| `ozonetel-cdr-proxy-poc-role` | The function's **execution role** — what the code runs as | Managed `AWSLambdaBasicExecutionRole` (CloudWatch logs) + inline `read-google-sa-ssm-param`: `ssm:GetParameter` on `…/google/sa-json` only, and `kms:Decrypt` on the default `alias/aws/ssm` key. Deliberately cannot read any other parameter. |
| `ozonetel-cdr-proxy-poc-github-actions-role` | **CI deploy role**, assumed via OIDC — no long-lived keys | Inline `ozonetel-cdr-proxy-poc-deploy`: Lambda code/config update + `AddPermission` on this one function ARN, and EventBridge rule management on `oz-*`. No IAM, no CloudFormation, no S3. |
| `meenal_lambda_only` | IAM **user** (a teammate), not a role | Inline `ozonetel-cdr-proxy-poc-full-access`: `lambda:*` scoped to this function's ARN and nothing else in the account. She can deploy/invoke/edit this function directly. (She also holds `QuerySchedulerLambdaAdminOnly` for a different function.) |

Deliberately *not* granted anywhere: VPC access, database access, or any
`iam:*`. This function talks to exactly two things — Ozonetel over the public
internet and Google Sheets — so it needs nothing else.

### EventBridge

Rules are **not** created by hand — `src/eventbridge_sync.py` reconciles them
from `scheduled_reports/*.yaml` on every CI run. Rules are named
`oz-<environment>-<report-name>`, tagged `ManagedBy=ozonetel-report-ci`, and
carry an input of `{"report_name": "...", "environment": "..."}`. A rule whose
spec is deleted or disabled is removed, along with its Lambda invoke permission.

Run it by hand if needed:

```bash
python -m src.eventbridge_sync \
  --environment prod \
  --lambda-arn arn:aws:lambda:ap-south-1:018100542607:function:ozonetel-cdr-proxy-poc \
  --lambda-function-name ozonetel-cdr-proxy-poc \
  --dry-run          # drop --dry-run to apply
```

### GitHub Actions OIDC role

`arn:aws:iam::018100542607:role/ozonetel-cdr-proxy-poc-github-actions-role`.
Inline policy `ozonetel-cdr-proxy-poc-deploy` grants only Lambda code/config
update on this one function, and EventBridge rule management on `oz-*`
(plus `events:ListRules` on `*`, which cannot be resource-scoped).

**⚠️ This org customises the OIDC subject claim.** The trust policy matches:

```
repo:Leadzo-tech@263658350/ozontel-report@1381439532:ref:refs/heads/main
```

not the documented default `repo:<org>/<repo>:ref:refs/heads/<branch>`. The org
has GitHub's "include org and repo IDs" customisation enabled, which embeds
immutable numeric IDs so a renamed or recreated repo can't inherit the trust.

This costs hours if you don't know it: a trust policy written to the documented
default fails with `Not authorized to perform sts:AssumeRoleWithWebIdentity`,
which looks identical to a typo, a propagation delay, or a broken provider.

**If you create another role for another repo in this org**, get the real claim
rather than assuming — add a step to the workflow that prints its own token's
claims (never the token):

```yaml
- name: Show OIDC claims
  run: |
    token=$(curl -sLS "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=sts.amazonaws.com" \
      -H "Authorization: Bearer ${ACTIONS_ID_TOKEN_REQUEST_TOKEN}" | jq -r '.value')
    payload=$(echo "$token" | cut -d. -f2)
    pad=$(( (4 - ${#payload} % 4) % 4 ))
    [ "$pad" -gt 0 ] && payload="${payload}$(printf '=%.0s' $(seq 1 $pad))"
    echo "$payload" | tr '_-' '/+' | base64 -d | jq '{sub, aud, repository}'
```

Note the numeric IDs are per-repo, so this role's trust policy is not
copy-pasteable to another repo.

### A deploy must never blank the credentials

`update-function-configuration --environment` **replaces every variable** — any
variable not re-sent is erased. An unset repo secret expands to `""`, so a naive
`jq` merge silently wipes the live API key while the deploy reports success. The
function then fails every run with `Missing OZONETEL_API_KEY`.

This happened once, on the first deploy that got past OIDC. The workflow now
only overwrites a credential when it actually has a value, and fails the deploy
outright if the result would leave one empty. Keep that guard.

## One-time GCP setup (already done — for reference)

Project `leadzo-497107`, service account
`query-scheduler@leadzo-497107.iam.gserviceaccount.com` — deliberately the
*same* SA the query-scheduler uses, so there's one Google identity to share
sheets with, not two.

1. **Service account JSON key** lives in SSM at
   `/leadzo/ozonetel-cdr-proxy-poc/poc/google/sa-json` as a `SecureString`.
2. **APIs enabled** in the project: Google Sheets API, Google Drive API.
3. **Each target sheet** shared with the SA email as **Editor**.

## Deploy

### Via CI (the normal path)

Push to `main`. `.github/workflows/deploy-prod.yml` runs two jobs: `validate`
(spec lint + pytest) and `deploy` (build zip → `update-function-code` → merge
`GIT_SHA` and Ozonetel creds into env vars → `python -m src.eventbridge_sync`).

Requires repo secrets `OZONETEL_API_KEY` and `OZONETEL_USERNAME` (already set):

```bash
gh secret set OZONETEL_API_KEY --repo Leadzo-tech/ozontel-report --body '<key>'
gh secret set OZONETEL_USERNAME --repo Leadzo-tech/ozontel-report --body 'leadzo'
```

### Manual (fallback)

```bash
rm -rf build lambda.zip && mkdir build
pip install -r requirements.txt -t build/ \
  --platform manylinux2014_x86_64 --only-binary=:all: \
  --python-version 3.12 --implementation cp --abi cp312
cp -r src scheduled_reports build/
(cd build && zip -qr ../lambda.zip . -x "*/__pycache__/*" "__pycache__/*")
aws lambda update-function-code --profile leadzo --region ap-south-1 \
  --function-name ozonetel-cdr-proxy-poc --zip-file fileb://lambda.zip
aws lambda wait function-updated --profile leadzo --region ap-south-1 \
  --function-name ozonetel-cdr-proxy-poc
```

`template.yaml` documents the function's shape for reference. It is **not**
deployed — the live resources were created by CLI and `sam deploy` would try to
create a colliding stack. See the comment at the top of that file.

## Local development

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
python -m src.validate_specs    # lints every YAML in scheduled_reports/
```

Tests are pure — no AWS calls, no network, no credentials needed.

## Operations

- **Logs:** CloudWatch group `/aws/lambda/ozonetel-cdr-proxy-poc`. Every run
  emits JSON events (`start`, `ozonetel_fetch` per day, `success` / `failure`)
  stamped with `report_name`, `environment`, `git_sha`, and `spec_hash`.
- **Failure alerts** go to the Slack webhook at
  `notifications.slack_webhook_param`. That parameter does not exist yet, so
  alerts currently no-op silently — create it to turn them on.
- **Schedules** are EventBridge rules `oz-prod-<report-name>`, reconciled from
  `scheduled_reports/*.yaml` on every CI run.
- **Concurrency** is capped at 1. If a run ever exceeds the 5-minute interval,
  the next invoke is throttled rather than racing it. Throttles show up as the
  `Throttles` metric on the function.
- **This is a log, not a snapshot.** Every run merges the last
  `days_back_routine` days plus today into the existing rows by `CallID`. Don't
  add manual columns to that tab — they will be dropped. Build derived views in a separate tab referencing this one.

## Repo layout

```
src/
  handler.py              # Lambda entry — dispatch on report_name, alert on failure
  ozonetel_cdr.py         # the API pull + sheet write, driven by the spec
  report_registry.py      # YAML loader + schema validator + spec_hash
  eventbridge_sync.py     # CLI reconciler invoked from CI
  sheets_client.py        # gspread overwrite + document flattener
  secrets.py              # SSM parameter fetch helpers
  slack.py                # Slack webhook on failure
  logging_utils.py        # JSON log formatter
  validate_specs.py       # CI hook for YAML lint
scheduled_reports/        # ← add your specs here
template.yaml             # SAM/CFN template (reference only, not deployed)
samconfig.toml            # SAM deploy params
.github/workflows/deploy-prod.yml   # CI: validate → build → update-function-code → eventbridge_sync
```
