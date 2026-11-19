# Auto Budget progress and handoff

Last updated: 2026-11-18. This is a snapshot; inspect the repository, current AWS state, and user instructions before acting.

## Project and workflow

- Repository: `auto-budget`; user intends to keep it public. Frontend is local at `http://localhost:5173`; backend is deployed to AWS in `us-east-1`.
- The original plan specifies user-defined categories and vendor rules as the source of budget categorization. Plaid categories may be retained as raw metadata only; do not automatically map them into budget categories.
- User uses Conda environment `auto-budget` and Node.js. Run backend checks with `conda run -n auto-budget python -m pytest` and `conda run -n auto-budget ruff check .` from `backend`; run `npm run build` from `frontend`.
- Deployment normally uses the GitHub Actions Deploy workflow: a main-branch push creates a plan; manually run the workflow with apply checked after reviewing it. Preserve the `BUDGET_ALERT_EMAIL` GitHub secret to avoid removing the existing budget. Terraform uses S3-native locking.
- The user reported Phase 3 changes merged and Terraform deployment successful. At the time of this handoff, `git status --short` showed local modifications to `scripts/generate-deployer-policy.py` and `terraform/README.md`; inspect before editing or committing. Do not overwrite user work or assume it is safe to discard.

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
- A Sandbox webhook was requested and another sync was observed, but causation wasn't isolated. The weekly rule was inspected on 2026-11-18 and was enabled with the correct worker target and input; its actual scheduled delivery has not been verified. Mocked tests cover both paths, not live infrastructure delivery.
- On 2026-11-18, a second disposable Sandbox Item was initially synced, then a `SYNC_UPDATES_AVAILABLE` webhook was requested. The API Lambda was invoked and a later sync completed, but success logs do not identify the webhook or its signature, so causation still isn't independently established. For an accelerated recovery test, a simulated transaction was added while that Item was paused; a temporary one-minute EventBridge rule targeting the same worker and recovery input was enabled, after which a sync imported the transaction. The worker's logs show two invocations at the expected time, but do not identify their sources; the actual seven-day rule was not tested. The temporary rule, disposable Plaid Item, item records, and token parameter were removed; a follow-up check found no temporary rules or Plaid Item metadata or item token parameters.
- After deploying correlation logs, a fresh disposable test on 2026-11-18 confirmed a verified Sandbox webhook was queued at 22:36:17 UTC and a worker sync with source `webhook` completed at 22:36:41 for the same hashed Item reference. A temporary one-minute rule queued recovery at 22:37:50; a sync with source `recovery` completed at 22:37:58 and imported a simulated transaction created while the Item was paused. This verifies the accelerated delivery and recovery path, not an actual firing of the seven-day rule. The test Item, its records and token, the temporary rule, and its Lambda permission were removed and independently checked.
- The dead-letter CloudWatch alarm has no email action. User wants low AWS costs; on-demand DynamoDB and point-in-time recovery are not guaranteed free. An earlier Cost Explorer API check incurred a $0.01 request charge; don't repeat paid checks without a reason.

## What to do next

1. Live signed webhook delivery and accelerated scheduled recovery have been verified. The actual seven-day rule has not been observed firing; decide whether to observe it later rather than blocking Phase 4 on a seven-day wait.
2. Review production readiness, cost and plan terms with the user before enabling real account connections. The implementation is currently fixed to Plaid Sandbox.
3. Start Phase 4 with household-scoped budget/category storage, vendor rules, monthly/yearly reporting, and forecasts as described in `PLAN.md`. The original plan specified per-user keys; the deployed design uses household membership to support eventual sharing. Preserve household isolation and clarify calculation rules where necessary.

Changes deployed after Sandbox verification group worker writes into lease-checked batches and improve handling and display of uncertain connections. Recovery from interruption before journal publication is covered by simulated tests; batching reduces request round trips but does not guarantee completion for every history. See the README recovery guidance for exchange and disconnect failures. Real accounts remain disabled.

On 2026-11-19, after those changes were deployed, a disposable custom Sandbox Item imported 200 of 200 transactions on its first application sync in 18.3 seconds including queue wait, and the deployed listing and accounts routes succeeded. Disconnect timed out at the API Lambda's 29-second limit while deleting rows one at a time. Plaid removal had succeeded; the remaining disposable records, token, household link and test membership were removed, and follow-up checks found no Item metadata or item token parameters. A fix to batch deletion and return a retryable cleanup status was subsequently deployed and tested as described below. A 200-transaction Sandbox fixture does not establish an upper bound for production histories.

The batched disconnect fix was deployed and retested on 2026-11-19: a fresh 200-transaction initial application sync completed in 18.3 seconds including queue wait, all 200 rows were present, and disconnect completed in one request with local records and token removed. Two subsequent disposable Items were created successfully through the deployed exchange route, but each initial queued worker invocation logged a generic failure. A later direct diagnostic sync of the second Item succeeded, and its deployed disconnect succeeded; this does not prove the queued sync path recovered. Plaid calls for the first Item also succeeded during diagnosis, but the initial error cause is unknown. Both Items and test memberships were cleaned up and follow-up checks found no remaining Item metadata or item tokens. Investigate the initial queued sync failures before declaring the live verification complete; do not log credentials or financial data.

## Relevant paths

- `backend/app/main.py`, `plaid_api.py`, `plaid_client.py`, `plaid_sync.py`, `plaid_webhook.py`, `plaid_worker.py`
- `backend/tests/` for unit and simulated-service integration tests
- `frontend/src/App.tsx`, `frontend/src/PlaidAccounts.tsx`, `frontend/src/auth.ts`
- `terraform/envs/dev/app.tf`, `plaid.tf`, and `terraform/README.md`
- `scripts/generate-deployer-policy.py`, `scripts/configure-plaid-sandbox.py`, `scripts/create-household-user.py`
