# Project guidance

- This is a personal budgeting project for one household, initially one user and potentially one or two more people. Prefer simple, maintainable solutions suited to that scale; preserve household isolation and define sharing before adding users.
- The repository is public. Never commit or print credentials, access tokens, financial data, Terraform state, or other private information. Keep secrets in appropriate secret storage or GitHub Actions secrets, not frontend environment variables or Terraform state.
- Treat financial data as sensitive. Enforce authentication and household authorization on the server, and avoid exposing sensitive data in logs, tests, or client-facing errors. Use disposable Sandbox data for testing.
- Keep AWS usage and cost as low as practical, with the goal of staying within the applicable free-tier allowances. Verify eligibility, current pricing, usage, and any charges before introducing or enabling services or paid features; a budget alert is not a spending limit. Avoid paid billing API queries without a specific reason.
- Keep Plaid fixed to Sandbox until real-account readiness, the user's actual Plaid access and pricing, and the proposed Production changes have been reviewed with the user. Do not enable paid Plaid add-ons without explicit authorization.
- Read `README.md`, `PLAN.md`, and `PROGRESS.md` for the current architecture, deployment workflow, checks, and outstanding work. Preserve existing work and avoid unrelated changes.
