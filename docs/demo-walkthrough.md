# Demo Walkthrough

> This is the optional advanced authenticated-workflow walkthrough. The primary
> product flow now starts from the Web home page, `POST /api/scans`, or
> `gardenctl scan --url URL` and requires no commands below. See
> `docs/legacy-cli-migration.md` for the boundary between the two flows.

## Goal

Demonstrate a complete safe local Garden workflow in 5 to 10 minutes:

`target -> login -> session -> inventory -> checks -> findings -> evidence -> status update -> retest -> report`

## Preconditions

1. Create and activate a virtual environment.
2. Install dependencies with `make install` or `.venv/bin/pip install -e '.[dev]'`.
3. Copy [.env.example](/Users/an/Documents/Garden/.env.example) to `.env`.
4. Keep `GARDEN_ALLOW_NON_LOCAL_TARGETS=false`.
5. Use only local demo targets for the walkthrough.

## Local Demo Startup

Start the app:

```bash
make demo
```

Or directly:

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Verify health:

```bash
curl -s http://127.0.0.1:8000/healthz
```

## Add a Demo Target

You can import from [examples/targets/local-demo-targets.yaml](/Users/an/Documents/Garden/examples/targets/local-demo-targets.yaml):

```bash
gardenctl target import --file examples/targets/local-demo-targets.yaml --on-duplicate skip
```

Or add a single target manually:

```bash
gardenctl target add \
  --name garden-demo-web \
  --base-url http://127.0.0.1:8000 \
  --type web \
  --owner appsec \
  --tag demo \
  --tag local
```

## Add a Credential Profile

Use the safe local login config:

- [examples/login/demo-http-admin.yaml](/Users/an/Documents/Garden/examples/login/demo-http-admin.yaml)

Create the credential profile:

```bash
gardenctl cred add \
  --target-id 1 \
  --name garden-demo-admin \
  --role admin \
  --auth-type password \
  --username admin@example.local \
  --secret-ref env://GARDEN_DEMO_ADMIN_PASSWORD \
  --login-config-path examples/login/demo-http-admin.yaml
```

## Test Login

```bash
gardenctl login test --target garden-demo-web --profile garden-demo-admin
gardenctl session list
gardenctl session show 1
```

Expected result:

- a successful login
- a reusable authenticated session
- redacted session metadata only

## Build Inventory

```bash
gardenctl inventory build --target garden-demo-web --profile garden-demo-admin
gardenctl inventory list
gardenctl inventory show 1
```

Expected result:

- structured pages, endpoints, parameters, and annotations
- bounded crawl behavior
- a completed inventory run tied to target, profile, session, and job

## Run Checks

```bash
gardenctl checks run --inventory 1
gardenctl findings list
gardenctl findings show 1
```

Expected result:

- several low-risk, explainable findings
- severity and confidence values
- linked inventory references

## Inspect Redacted Evidence

```bash
gardenctl evidence list
gardenctl evidence show 1
gardenctl evidence export --finding 1 --format md
```

Expected result:

- evidence is linked to findings
- previews are redacted
- exports remain redacted by default

## Update Status

Choose a finding and mark it fixed for the demo:

```bash
gardenctl findings update-status 1 --status fixed
gardenctl findings show 1
```

Expected result:

- lifecycle status changes from `new` to `fixed`
- status change is auditable

## Run Retest

```bash
gardenctl retest run --finding 1
gardenctl findings show 1
```

Expected result:

- Garden reuses prior context where possible
- a retest record is created
- the finding either reopens to `triaged` if reproduced or closes if not reproduced

## Export Markdown Report

After a checks or retest job, export a report:

```bash
gardenctl findings export --format md
gardenctl report generate --job 2 --format md
```

Optional additional exports:

```bash
gardenctl findings export --format json
gardenctl findings export --format csv
```

## Web Review Flow

Open these pages in a browser:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/targets`
- `http://127.0.0.1:8000/jobs`
- `http://127.0.0.1:8000/sessions`
- `http://127.0.0.1:8000/inventory`
- `http://127.0.0.1:8000/findings`
- `http://127.0.0.1:8000/evidence`

Key things to point out in a demo:

- the dashboard summarizes current workflow state
- findings show lifecycle state and latest retest context
- evidence previews are redacted
- the UI is intentionally light because CLI remains the operational surface

## Talking Points for a Short Demo

If you only have a few minutes, emphasize:

1. Garden starts from authenticated context rather than anonymous scanning.
2. Inventory is structured, not a traffic blob.
3. Checks stay low-risk and explainable.
4. Evidence is redacted by default.
5. Findings have lifecycle, retest, and export support.
