# Automated Budget Dashboard

A personal budgeting dashboard organized by calendar year and month. The frontend runs locally; its protected API and household membership live in AWS. Plaid and budget data are not yet connected.

## Run locally

Requirements: Conda and Node.js 22 or later. From the project root, create the Conda environment once:

```sh
conda env create -f environment.yml
```

If the `auto-budget` environment already exists, skip this step.

After the AWS resources are deployed, configure the frontend as described below. Activate the environment and install the backend dependencies (also needed for the user setup script):

```sh
conda activate auto-budget
cd backend
python -m pip install -e '.[dev]'
```

Start the frontend:

```sh
cd frontend
npm ci
npm run dev
```

Open **http://localhost:5173/**. Cognito is configured for this exact address; another port or `127.0.0.1` will not work. The frontend calls the deployed API directly. The local API can still be started with `python -m uvicorn app.main:app --reload --port 8000` from `backend`, but its protected endpoint intentionally rejects requests outside API Gateway.

## Deploy and configure Phase 2

The GitHub role from the Terraform bootstrap needs permissions to manage Cognito, DynamoDB, Lambda, API Gateway, CloudWatch, IAM roles/policies, and optionally AWS Budgets. Its state-bucket policy alone is insufficient. Review and attach the Phase 2 deployment policy using the instructions in [terraform/README.md](terraform/README.md#phase-2-deployment-permissions) before deployment.

1. Optionally add a GitHub Actions secret named `BUDGET_ALERT_EMAIL` for a $5 monthly **account-wide** actual-cost alert. If omitted, no budget is created; AWS charges and free-tier eligibility depend on account usage and current AWS pricing. Keep this secret configured for subsequent deployments so Terraform does not remove the budget.
2. Merge the changes to `main`. The deploy workflow builds the Lambda package and creates a Terraform plan, but does **not** apply it. Review the plan in GitHub Actions. Then run the `Deploy` workflow on `main` manually with `apply` checked. It creates a fresh plan and applies that exact plan; review changes to main and the current AWS state before running it.
3. Obtain the `api_url`, `cognito_user_pool_id`, `cognito_client_id`, `cognito_domain`, and `household_table_name` Terraform outputs from the successful apply log, or initialize the remote backend locally as described in [terraform/README.md](terraform/README.md) and run `terraform output` in `terraform/envs/dev`.
4. From the project root, activate the Conda environment and run the following with your values. This sends a Cognito invitation email and creates a household membership; it never places a password in Terraform state. The AWS credentials used here need `cognito-idp:AdminCreateUser`, `cognito-idp:AdminGetUser`, and `dynamodb:PutItem` on the respective resources.

   ```sh
   python scripts/create-household-user.py --region us-east-1 --user-pool-id YOUR_POOL_ID --table-name YOUR_TABLE_NAME --email YOUR_EMAIL
   ```

   Save the household ID printed by the script. To add another user to that household later, run it with their email and `--household-id YOUR_HOUSEHOLD_ID`. Existing memberships are never overwritten. If a run creates the Cognito user but fails before writing the membership, rerun the same command. Cognito sends the initial temporary password by email; use the hosted sign-in page to set a new one and enroll an authenticator app for MFA when prompted.
5. Create `frontend/.env.local` with these non-secret outputs and restart Vite:

   ```text
   VITE_API_URL=https://YOUR_API_ID.execute-api.YOUR_REGION.amazonaws.com
   VITE_COGNITO_DOMAIN=https://YOUR_COGNITO_DOMAIN.auth.YOUR_REGION.amazoncognito.com
   VITE_COGNITO_CLIENT_ID=YOUR_CLIENT_ID
   ```

   The client ID and URLs are public identifiers, not credentials. Never put passwords or Plaid secrets in `VITE_` variables; frontend variables are visible to the browser. Sign in with the invited account. The access token is kept in the browser tab's session storage and expires after one hour; sign in again after it expires.

API Gateway validates Cognito tokens for `/api/me`, and the Lambda checks household membership. `/api/health` is public and contains no financial information. Only the API Gateway integration should be permitted to invoke the Lambda; do not grant untrusted principals direct invocation, because the function trusts the authorizer claims in the gateway event.

## Checks

Run `ruff check .` and `pytest` in `backend`, and `npm run build` in `frontend`. GitHub Actions runs these checks on pull requests and pushes to main. See [terraform/README.md](terraform/README.md) for the original infrastructure setup.

To build the Lambda archive, run `python package_lambda.py` from `backend` on Linux with Python 3.12 and the target Lambda architecture. It creates `backend/dist/api.zip`; CI also checks packaging. This does not deploy the API.

Phase 4 is in progress: the local dashboard can create categories, save monthly allowances, and manually classify Sandbox transactions after the new API routes are deployed. Spending totals are preliminary and need live Sandbox reconciliation; unclassified transactions are omitted. Use Sandbox data only; do not enter real financial credentials.

## Phase 3 development status

The Plaid Sandbox infrastructure has been deployed and tested with disposable Sandbox Items. Link-token creation, initial synchronization, account retrieval, and disconnect worked; the user also completed an interactive Link check without errors. A signed webhook was verified through to a worker sync. The code is fixed to Plaid's Sandbox host and the local dashboard labels its connections as test accounts; real accounts are not supported yet.

The proposed implementation adds a queue and dead-letter queue, a worker Lambda, a weekly EventBridge rule, and a CloudWatch alarm. DynamoDB on-demand requests, point-in-time recovery, S3, and other services may incur costs; the $5 account-wide budget is an alert rather than a spending cap. The dead-letter alarm appears in CloudWatch but does not send an email notification.

The GitHub deployer role's inline policy must include the permissions generated by the administrator procedure in [terraform/README.md](terraform/README.md#phase-2-deployment-permissions). The generated policy includes project queues, the schedule, and Lambda event source mappings. Review the GitHub Actions Terraform plan before applying it. Keep `BUDGET_ALERT_EMAIL` configured so the existing budget is retained.

After deployment, Sandbox credentials can be stored outside Terraform state using `python scripts/configure-plaid-sandbox.py`. It prompts without echoing the client ID or Sandbox secret and stores them in a standard SSM SecureString parameter. It requires AWS credentials with permission to create that parameter and use the SSM KMS key. Never put Plaid secrets in Git, `VITE_` variables, Terraform variables, shell command arguments, or chat. Plaid Sandbox uses fake data; never enter real bank credentials into a test institution.

Live Sandbox verification confirmed a signed webhook led to a sync and an accelerated one-minute schedule recovered a simulated transaction. The actual seven-day schedule has not been observed firing. Check the worker logs and dead-letter queue if an account does not appear. Sandbox tests using mocked AWS and Plaid run as part of the backend test suite; they do not verify IAM or live Plaid behavior.

If a connection reports an uncertain status, refresh the account list before trying Link again. A connection marked “disconnect incomplete” can be retried; if it continues to fail, investigate the Item in the Plaid Dashboard before removing any stored data. For an incomplete exchange that does not appear in the app, an AWS administrator can compare the Plaid Dashboard and the names of parameters under `/auto-budget-dev/plaid/sandbox/items/` with the Item metadata and household references in DynamoDB. Do not print parameter values, delete unrelated Items, or assume an invalid token proves billing has ended. If the exchange response was lost before the Item ID and access token were saved, consult Plaid support or the Dashboard to identify and resolve the orphaned Item. Investigate any partial setup before reconnecting.

Sync writes are grouped into small lease-checked transactions to reduce network round trips; DynamoDB still charges for the underlying transactional reads and writes. Large initial histories, the five-minute worker timeout, and recovery from a timeout before the staging journal is published still need measurement before real accounts are enabled.
