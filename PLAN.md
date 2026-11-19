# Auto Budget project plan

This document records the agreed direction for the next chat. Check `PROGRESS.md` and the current repository and AWS state before making changes; plans and pricing can change.

## Product goals

- Build a personal budget dashboard, initially with a local frontend and AWS backend, with future private access from the existing portfolio website and domain.
- Present a calendar year divided into months, with an explicit year selection. Monthly unspent budget does not roll over; the user expects to move excess funds into savings or investments.
- Connect checking, savings, and credit card accounts through Plaid. Present balances and spending clearly, including pending transaction labels. Transfers and investment movements should not be counted as spending.
- Start with one household and one user. Preserve household-based authorization so a second person can be added later; define sharing and permissions explicitly before supporting multiple users.
- Use user-defined budget categories, merchant/vendor rules, and manual overrides. Plaid category fields may be retained as raw metadata for debugging but must not drive budget categorization. Manual choices take priority.
- Keep costs very low and review charges before introducing services or enabling real financial data. The $5 AWS budget is an account-wide alert, not a hard limit.

## Phase 1: local application foundation — implemented

React/TypeScript/Vite frontend, Python/FastAPI backend, tests, CI, and Conda environment. See `README.md` for startup instructions.

## Phase 2: AWS and authentication — implemented and deployed

Terraform provisions the Cognito user pool, API Gateway, Lambda, DynamoDB, logging, and budget alert. Cognito sign-in uses required authenticator MFA; membership maps a signed-in subject to a household. New protected endpoints must enforce household membership and ownership, not accept household IDs from clients as authority.

## Phase 3: Plaid Sandbox — implemented and deployed; verification remains

- Sandbox-only Link, exchange, list, disconnect, manual sync, accounts, and signed webhook endpoints. The actual implementation uses an API Lambda for the webhook and one worker Lambda for queued sync and weekly recovery; the original plan proposed separate webhook, worker, and reconciliation Lambdas.
- SQS worker with transaction sync cursors, pagination, staged replay, added/modified/removed records, and ordinary account balances via `/accounts/get`. EventBridge schedules weekly recovery.
- A simulated AWS/Plaid test suite and a real disposable Sandbox Item test passed. The user also completed an interactive Link check without errors.
- A live signed `SYNC_UPDATES_AVAILABLE` webhook was verified through queueing and worker sync on 2026-11-18 using correlated logs.
- A temporary one-minute EventBridge rule exercised the deployed recovery handler and imported a simulated missed transaction on 2026-11-18. The actual seven-day schedule has not been observed firing; it was enabled with the expected target when inspected that day. See `PROGRESS.md` for evidence and cleanup.
- The worker stages and replays data in small lease-checked DynamoDB transactions. A 200-transaction Sandbox initial sync and batched disconnect passed live; larger histories and the five-minute timeout still need evaluation before production. Interrupted staging and ambiguous AWS write responses are covered by simulated tests; an exchange response lost before the Item is saved can still require manual investigation.
- Three initial queued syncs failed with unknown causes before failure classifications were deployed. Three subsequent queued syncs succeeded; the prior failures remain unexplained. The dead-letter alarm has no notification action. Review these and other recovery, security, and cost gaps before Phase 5.
- Keep the application hard-coded to Sandbox until the user reviews their actual Plaid account's Production access, plan, Item limits, and quoted Transactions price. Never use `/transactions/refresh` or paid Balance, Recurring Transactions, or Enrich add-ons unless the user explicitly changes that decision.

## Phase 4: dashboard MVP — next phase

Phase 4 remains Sandbox-only. Before implementation, inspect the current schema and agree on a small end-to-end slice. The original plan calls for the following authenticated API interfaces:

- `GET /v1/dashboard?month=YYYY-MM` and `GET /v1/transactions` with pagination and filters.
- `PATCH /v1/transactions/{transaction_id}`, category CRUD, vendor-rule CRUD, `POST /v1/category-rules/preview`, and `POST /v1/category-rules/{rule_id}/apply`.
- `GET/PUT /v1/budgets/{YYYY-MM}` and forecast CRUD endpoints.

Provide overview, spending, transactions, categories/rules, budget, forecast, and accounts views. Overview includes cash, card debt, net available money, spending, remaining allowance, and account balances. Accounts show institution status, last sync, errors, reconnect, and disconnect. Year selection and a monthly breakdown are important to the user's existing annual budgeting workflow.

Implementation should cover:

1. Design household-scoped categories, vendor matching rules, manual transaction categorization, and calendar-year/month budget records. Keep imported Plaid data separate from user edits so syncs do not overwrite overrides.
2. Implement authorized APIs and a local interface for maintaining categories and monthly budgets; add useful tests for isolation and financial calculations.
3. Build year and month views showing planned, spent, and remaining amounts, with no rollover. Define handling for refunds, pending-to-posted transitions, transfers, credit card payments, and uncategorized transactions before calculating totals.
4. Add account/balance and spending analytics incrementally, validating calculations against examples and preserving accurate historical data.
5. Add one-time and recurring expected income and expenses, using weekly, biweekly, monthly, and annual recurrence and a 12-month forecast horizon.

Rules specify a category, merchant-name or raw-name match field, exact/contains/regex operator, normalized value, priority, and enabled status. The first matching active rule wins unless a manual override exists. Provide a preview and an explicit option to apply a rule to existing transactions without overrides.

Financial definitions agreed in the original plan:

- Cash is available checking and savings balances, falling back to current balances. Card debt is current enabled credit-card balances. Net available money is cash minus card debt.
- Monthly spending is eligible outgoing transactions minus refunds. Include and label pending transactions; exclude transfers, credit-card payments, income, and explicitly excluded transactions.
- Remaining allowance is the configured amount minus eligible spending. Forecast balance is current net available money plus expected income minus expected expenses.
- Use USD, calendar months, and `America/New_York`; no unused allowance rolls over. Validate pending-to-posted transitions and avoid double counting.

The exact phase boundaries and UI design remain open; don't assume reporting is complete simply because transactions are stored.

## Phase 5: real accounts

Complete Sandbox testing first, then check the user's actual Plaid Trial access and exact quoted Production subscription price before enabling live Items. Store Production credentials separately in SSM. Connect checking/savings and credit cards through Link, reconcile balances and at least two months of transactions, then add vendor rules. Multiple accounts under one bank login may share an Item; separate institutions can require separate Items. If pricing is unacceptable, stop before paid Items and consider CSV import as a separate milestone.

## Later: web hosting and authentication improvements

The planned address is `budget.seanbroderick.dev` on the existing Cloudflare-managed domain. Once the local MVP is stable, the auto-budget project should own a separate private S3 frontend bucket, CloudFront distribution with Origin Access Control, ACM certificate and Cloudflare DNS-only CNAME/validation record; do not modify the portfolio distribution. Route static content to S3 and `/api/*` to API Gateway with caching disabled and authorization headers forwarded. The existing webhook may continue to use API Gateway directly. Reconcile the planned `/api/*` route with the current `/v1/*` application routes before implementing.

Configure SPA routing, long-lived hashed-asset caching, no-cache HTML, security headers, restrictive CSP, `robots.txt`, and `noindex`. Keep financial API responses `Cache-Control: no-store`, public signup disabled, and CORS restricted to approved origins. Do not link the dashboard from the portfolio unless requested. A hidden URL is not access control: enforce authentication and household authorization server-side. Review OAuth redirect URIs and browser token storage before going public. Cognito passkeys with user verification may be supported later; verify feature tier, price, recovery, and authentication domain first.

The planned deployment flow is test frontend and backend, package Lambdas, apply Terraform, build the frontend using Terraform outputs, sync the dashboard assets, and invalidate the CloudFront HTML entry point.

## Testing and acceptance

Test available-money and spending calculations, refunds, pending and posted transactions, exclusions, allowances, and forecasts. Cover rule operators, priority, disabled/conflicting/invalid rules, previews, and preservation of manual overrides. Verify pagination, cursor persistence, removals, duplicate webhooks, invalid signatures, retries, and weekly recovery of an intentionally missed webhook. Test missing, expired, wrong-user, and nonmember authorization, and verify financial responses are not cacheable. Run Terraform format/validation and application checks in CI. MVP acceptance requires accurate balances, reconciled spending, working categorization, editable allowances, forecasts, and secure access.

## Cost and security practices

- Never put Plaid credentials, access tokens, passwords, or financial data in the public repository, Terraform variables/state, frontend environment variables, logs, shell arguments, or chat. Provision Sandbox credentials using the documented hidden-input SSM script.
- Protect Terraform state and its native S3 lock with scoped IAM. Keep the GitHub deployment role and workflow changes under review; its infrastructure permissions are not a hard security boundary against a compromised workflow.
- Cognito access tokens in browser session storage remain valid until expiry even after sign-out; XSS and browser extensions matter. Only trusted AWS principals should invoke the API Lambda directly, since it trusts API Gateway authorizer claims in the event.
- AWS DynamoDB on-demand operations and point-in-time recovery may incur charges at low usage. Cost Explorer API requests are billed; use the AWS billing console when practical. AWS costs and Plaid Production pricing are separate.
