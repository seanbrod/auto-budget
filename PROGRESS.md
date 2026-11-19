# Auto Budget progress and handoff

Last updated: 2026-11-19. This is a snapshot; inspect the repository, current AWS state, and user instructions before acting.

## Project and workflow

- Repository: `auto-budget`; user intends to keep it public. Frontend is local at `http://localhost:5173`; backend is deployed to AWS in `us-east-1`.
- The original plan specifies user-defined categories and vendor rules as the source of budget categorization. Plaid categories may be retained as raw metadata only; do not automatically map them into budget categories.
- User uses Conda environment `auto-budget` and Node.js. Run backend checks with `conda run -n auto-budget python -m pytest` and `conda run -n auto-budget ruff check .` from `backend`; run `npm run build` from `frontend`.
- Deployment normally uses the GitHub Actions Deploy workflow: a main-branch push creates a plan; manually run the workflow with apply checked after reviewing it. Preserve the `BUDGET_ALERT_EMAIL` GitHub secret to avoid removing the existing budget. Terraform uses S3-native locking.
- The user reported the Sandbox, batching, disconnect, and diagnostic logging changes merged and deployed. Local branch and remote-tracking references may be stale; inspect current status before editing or committing and preserve uncommitted work.
- The automatic main-branch plan and a manual apply can overlap and contend for the state lock. Wait for the automatic plan to finish and review it before starting the manual apply; never disable locking to work around a collision.

## Deployed foundation

- Cognito email sign-in, self-signup disabled, required authenticator MFA, OAuth authorization code + PKCE, access tokens stored per browser tab. User successfully signed out and back in with MFA.
- API Gateway validates JWTs. The backend requires an access token and looks up the subject's DynamoDB household membership. An end-to-end negative test confirmed a real authenticated user without a membership was denied; the temporary user was removed afterward.
- DynamoDB on-demand table with point-in-time recovery, API and worker Lambdas, CloudWatch logs, and a $5 monthly account-wide actual-spend budget alert. AWS budget has an email subscriber; verify the address in AWS if necessary. Alerting can be delayed and does not stop spending.
- The GitHub role's S3 state permissions were scoped to the state bucket, state object, and `.tflock`; broad S3 full access was removed. The deployment policy also includes permissions for the project resources. Event source mapping tag permissions apply across mappings in the account and region because mapping IDs are generated.

## Plaid Sandbox

- Credentials are stored as a standard SSM SecureString, provisioned by the user. Do not retrieve or expose secret values unless needed internally for an authorized test; never print them.
- API routes: `POST /v1/plaid/link-token`, `POST /v1/plaid/exchange`, `GET /v1/plaid/items`, `DELETE /v1/plaid/items/{item_id}`, `POST /v1/plaid/items/{item_id}/sync`, `GET /v1/accounts`, and public verified `POST /v1/webhooks/plaid`.
- Sandbox-only API client, webhook signature/body-hash/timestamp verification, SQS queue + dead-letter queue, worker Lambda, and `rate(7 days)` EventBridge recovery rule. The webhook shares the API Lambda and the weekly recovery shares the worker, unlike the separate Lambdas envisioned in the original plan. No real accounts have been connected as part of the work described here.
- The implementation uses a small allowlisted HTTP client rather than the originally proposed Plaid Python SDK. The API and worker currently share an execution role; the original plan called for least-privilege roles, structured logs, request IDs, standardized errors, and deterministic packaging. Review those areas rather than assuming every original infrastructure requirement is complete.
- A disposable real Sandbox Item was connected, synced, returned 14 simulated accounts, and was disconnected; its item records and token parameter were checked as removed. A Link token was created. The user separately reports completing the interactive dashboard Link check without errors.
- After deploying correlation logs, a fresh disposable test on 2026-11-18 confirmed a verified Sandbox webhook was queued at 22:36:17 UTC and a worker sync with source `webhook` completed at 22:36:41 for the same hashed Item reference. A temporary one-minute rule queued recovery at 22:37:50; a sync with source `recovery` completed at 22:37:58 and imported a simulated transaction created while the Item was paused. This verifies the accelerated delivery and recovery path, not an actual firing of the seven-day rule. The test Item, its records and token, the temporary rule, and its Lambda permission were removed and independently checked.
- The dead-letter CloudWatch alarm has no email action. User wants low AWS costs; on-demand DynamoDB and point-in-time recovery are not guaranteed free. An earlier Cost Explorer API check incurred a $0.01 request charge; don't repeat paid checks without a reason.

## What to do next

1. Continue Phase 4 in Sandbox. The local changes add monthly transaction review, independent manual classifications, and preliminary planned/spent/remaining totals. Unclassified transactions are omitted and reported, pending transactions are included, linked pending records are suppressed when posted, and refunds reduce the month received. These routes require deployment and live Sandbox reconciliation before relying on totals. The current transaction listing reads all Item transactions and has no pagination; it is not suitable for large histories. Shared matching rules, forecast items and matching, annual reporting, and the remaining dashboard views are not yet implemented. The user agreed that rules should share matching conditions while keeping classification and forecast-match actions separate; manual edits take priority, automatic forecast matches must be unambiguous and reversible, and amounts/date windows should accommodate variable bills.
2. Investigate the previously observed queued-sync failures if they recur; their cause remains unknown. Live signed webhook delivery and accelerated scheduled recovery have been verified; the actual seven-day rule has not been observed firing.
3. Before Phase 5 enables real accounts, review remaining recovery, security, and production-readiness gaps, the user's actual Plaid access and pricing, and AWS free-tier eligibility and likely charges. The implementation remains fixed to Plaid Sandbox until that review.

## Session verification and limitations

Changes deployed after Sandbox verification group worker writes into lease-checked batches and improve handling and display of uncertain connections. Recovery from interruption before journal publication is covered by simulated tests; batching reduces request round trips but does not guarantee completion for every history. See the README recovery guidance for exchange and disconnect failures. Real accounts remain disabled.

On 2026-11-19, after those changes were deployed, a disposable custom Sandbox Item imported 200 of 200 transactions on its first application sync in 18.3 seconds including queue wait, and the deployed listing and accounts routes succeeded. Disconnect timed out at the API Lambda's 29-second limit while deleting rows one at a time. Plaid removal had succeeded; the remaining disposable records, token, household link and test membership were removed, and follow-up checks found no Item metadata or item token parameters. A fix to batch deletion and return a retryable cleanup status was subsequently deployed and tested as described below. A 200-transaction Sandbox fixture does not establish an upper bound for production histories.

The batched disconnect fix was deployed and retested on 2026-11-19: a fresh 200-transaction initial application sync completed in 18.3 seconds including queue wait, all 200 rows were present, and disconnect completed in one request with local records and token removed. Two subsequent disposable Items were created successfully through the deployed exchange route, but each initial queued worker invocation logged a generic failure. A later direct diagnostic sync of the second Item succeeded, and its deployed disconnect succeeded; this does not prove the queued sync path recovered. Plaid calls for the first Item also succeeded during diagnosis, but the initial error cause is unknown. Both Items and test memberships were cleaned up and follow-up checks found no remaining Item metadata or item tokens. Investigate the initial queued sync failures before declaring the live verification complete; do not log credentials or financial data.

After deploying allowlisted failure classifications, three more disposable exchanges on 2026-11-19 completed their queued initial syncs and disconnects. The first included immediate diagnostic Plaid calls; the latter two did not. Worker logs confirmed all three syncs, and checks found no remaining Item metadata or token parameters. The prior queued failures did not recur, so their cause is still unknown; the new logging may help investigate a future recurrence. This does not establish that the earlier issue is fixed.

Session-end local checks passed: 35 backend tests, Ruff, the frontend production build, `terraform fmt -check -recursive`, and `terraform validate`. The backend tests emitted FastAPI test-client and Mangum deprecation warnings. Live tests used disposable data and included direct Lambda invocations with a temporary test membership; they did not repeat the browser-based Cognito or interactive Link flow. `AGENTS.md` records the public-repository security, small household, and low-cost requirements; ensure it is included when committing the documentation updates.

## Relevant paths

- `backend/app/main.py`, `plaid_api.py`, `plaid_client.py`, `plaid_sync.py`, `plaid_webhook.py`, `plaid_worker.py`
- `backend/tests/` for unit and simulated-service integration tests
- `frontend/src/App.tsx`, `frontend/src/PlaidAccounts.tsx`, `frontend/src/auth.ts`
- `terraform/envs/dev/app.tf`, `plaid.tf`, and `terraform/README.md`
- `scripts/generate-deployer-policy.py`, `scripts/configure-plaid-sandbox.py`, `scripts/create-household-user.py`
