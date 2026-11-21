# Auto Budget progress and handoff

## Current session update (2026-11-21)

- The user reports the transaction review and manual classification slice was merged and deployed. Its totals still need live Sandbox reconciliation.
- The user reports the rule changes were merged and deployed. They add priority-based rules, historical previews and explicit application, regular expressions using RE2, and rule editing. Historical application proceeds in batches of 25. Automatic forecast matching and forecasts remain future work.
- Deployed verification on 2026-11-21: API Gateway rejected an unauthenticated rule request, but the deployed worker failed its disposable initial transaction sync with `AccessDeniedException`. The execution role policy lacks `dynamodb:BatchGetItem`, required by the new batched snapshot reads. A table-scoped permission fix is local in `terraform/envs/dev/app.tf` and passed Terraform formatting and validation; it is not yet deployed. Transaction-bearing syncs may fail until deployed. The deployed disconnect removed the test Sandbox Item; its test household, membership, Item records and token parameter were verified removed. The remaining deployed rule tests must be rerun after the IAM fix is merged and applied. A queued message for the removed test Item may retry and should be ignored by the worker when the Item no longer exists.
- On 2026-11-21, a local worker using real Plaid Sandbox and isolated temporary AWS records imported and rule-classified 200 synthetic transactions in 41.8 seconds. After batching the strongly consistent reads, a second disposable Item completed the same check in 13.1 seconds. Both Items were removed, their temporary table rows were verified absent, and their temporary token parameters were deleted. This is not a deployed Lambda, API Gateway, browser, or large-history test; the deployed rule routes still need Sandbox verification. DynamoDB still charges for the underlying reads.
- The older handoff below contains stale statements about next steps; retain it until its uncommitted changes are reconciled with the newer repository history.

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
- The dead-letter CloudWatch alarm has no email action. User wants low AWS costs; on-demand DynamoDB and point-in-time recovery are not guaranteed free. An earlier Cost Explorer API check incurred a $0.01 request charge; don't repeat paid checks without a reason.

## What to do next

1. Prefer completing the live signed webhook and weekly recovery verification before starting the next phase. Use disposable Sandbox data, don't expose secrets, distinguish manual invocation from a real scheduled event, and remove only explicitly created test Items. Avoid disturbing any existing Items.
2. Review production readiness, cost and plan terms with the user before enabling real account connections. The implementation is currently fixed to Plaid Sandbox.
3. Start Phase 4 with household-scoped budget/category storage, vendor rules, monthly/yearly reporting, and forecasts as described in `PLAN.md`. The original plan specified per-user keys; the deployed design uses household membership to support eventual sharing. Preserve household isolation and clarify calculation rules where necessary.

## Relevant paths

- `backend/app/main.py`, `plaid_api.py`, `plaid_client.py`, `plaid_sync.py`, `plaid_webhook.py`, `plaid_worker.py`
- `backend/tests/` for unit and simulated-service integration tests
- `frontend/src/App.tsx`, `frontend/src/PlaidAccounts.tsx`, `frontend/src/auth.ts`
- `terraform/envs/dev/app.tf`, `plaid.tf`, and `terraform/README.md`
- `scripts/generate-deployer-policy.py`, `scripts/configure-plaid-sandbox.py`, `scripts/create-household-user.py`
