# Infrastructure as Code (IaC) and Github Actions CI/CD Template

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DEVELOPER WORKFLOW                                │
│                                                                             │
│   Developer  ──────>  Git Push  ─────>  GitHub Repository                  │
│                                               │                             │
└───────────────────────────────────────────────┼─────────────────────────────┘
                                                │ Triggers on push to main
                                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      GITHUB ACTIONS CI/CD PIPELINE                         │
│                                                                             │
│   deploy.yml Workflow                                                       │
│     Step 1: Checkout Code                                                   │
│     Step 2: Setup Terraform                                                 │
│     Step 3: Configure AWS via OIDC (no stored keys) ────────┐               │
│     Step 4: Terraform Init (connect to remote state) ───────┤               │
│     Step 5: Format Check                                    │               │
│     Step 6: Validate                                        │               │
│     Step 7: Plan                                            │               │
│     Step 8: Apply only on manual dispatch                   │               │
│                                                             │               │
│   GitHub Secrets (non-sensitive identifiers only):          │               │
│     • AWS_ROLE_ARN      — OIDC role to assume               │               │
│     • AWS_REGION        — target region                     │               │
│     • TF_STATE_BUCKET   — S3 bucket for remote state        │               │
└─────────────────────────────────────────────────────────────┼───────────────┘
                                                              │ OIDC token exchange
                                                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                               AWS ACCOUNT                                   │
│                                                                             │
│   IAM OIDC Identity Provider (token.actions.githubusercontent.com)          │
│     └─> IAM Role: github-actions-terraform-deployer                         │
│           • Scoped trust policy (your repo + main branch only)              │
│           • S3 access for state management and native locking               │
│           • Resource permissions for your infrastructure                    │
│                                                                             │
│   STATE MANAGEMENT                  TERRAFORM CONFIG                        │
│     S3 Bucket (remote state)   <──  terraform/envs/dev/                     │
│       • Versioning enabled             main.tf      — provider, backend     │
│       • AES256 encryption              variables.tf — input variables       │
│       • Public access blocked          outputs.tf   — resource outputs      │
│     S3 lock file                                                            │
│       • Prevents concurrent runs without a separate database                │
│                                                                             │
│   DEPLOYED RESOURCES                                                        │
│     Default tags auto-applied: Environment, Project, ManagedBy              │
│     Add your actual resources to main.tf                                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Project Structure

```
.
├── .github/
│   └── workflows/
│       └── deploy.yml          # CI/CD: OIDC auth, plan, manual apply
├── scripts/
│   └── bootstrap-state.sh      # One-time setup: S3 state bucket
├── .env.example                # Template for local .env (never commit .env)
├── terraform/
│   ├── envs/
│   │   └── dev/
│   │       ├── main.tf         # Provider, backend, resources
│   │       ├── variables.tf    # Input variable declarations
│   │       ├── outputs.tf      # Output values
│   └── modules/                # Reusable Terraform modules
└── README.md
```

## Prerequisites

- AWS account with permissions to create IAM roles and S3
- AWS CLI installed and configured locally (for the one-time bootstrap)
- Terraform >= 1.12.0 (optional — only needed for local runs)

---

## Setup Guide

### Step 1: Bootstrap Local AWS Credentials

You need local credentials once to create the state infrastructure. This is a personal admin account or your SSO session — it is **not** what GitHub Actions uses.

```bash
aws configure
# Enter your Access Key ID, Secret Access Key, region, and output format
aws sts get-caller-identity  # verify it works
```

### Step 2: Create Remote State Infrastructure

S3 bucket names are globally unique — choose a name that won't collide across projects.

Create your local `.env` from the example file and fill in your values:

```bash
cp .env.example .env
# edit .env — set PROJECT_NAME and AWS_REGION
```

Then run the bootstrap script:

```bash
chmod +x scripts/bootstrap-state.sh
./scripts/bootstrap-state.sh           # loads .env by default
./scripts/bootstrap-state.sh .env.prod # or pass a custom env file
```

The script creates the S3 bucket with versioning, encryption, and public access blocked. Terraform uses an S3 lock file in this bucket for native state locking. The script then prints the exact values you'll need for Step 4.

> `.env` is git-ignored. Never commit it.

### Step 3: Create the OIDC IAM Role for GitHub Actions

This replaces the legacy IAM user + static key approach. GitHub Actions exchanges a short-lived OIDC token for temporary AWS credentials — no secrets to rotate or leak.

Add these variables to your `.env` file (the same one used in Step 2). GitHub repositories using immutable OIDC subjects require the numeric owner and repository IDs:

```
GITHUB_USERNAME=your-github-username
GITHUB_OWNER_ID=your-github-owner-id
GITHUB_REPO=your-repo-name
GITHUB_REPO_ID=your-github-repository-id
IAM_ROLE_NAME=github-actions-terraform-deployer
```

Then run the setup script:

```bash
chmod +x scripts/setup-oidc-role.sh
./scripts/setup-oidc-role.sh           # loads .env by default
./scripts/setup-oidc-role.sh .env.prod # or pass a custom env file
```

The script handles all sub-steps automatically:
- **3a** — Creates the OIDC Identity Provider (skips gracefully if it already exists)
- **3b** — Generates a scoped trust policy locked to your immutable repo identity + main branch
- **3c** — Creates the IAM role, or updates its trust policy if it already exists
- **3d** — Attaches an S3 policy for state management and native locking
- **3e** — Prints the Role ARN you'll need for Step 4

The state policy allows listing the state bucket, reading and writing `dev/terraform.tfstate`, and reading, writing, and deleting its `.tflock` object. It targets the default workspace; other workspaces or environments require additional scoped permissions. Set `STATE_BUCKET` or `STATE_KEY` in the ignored `.env` to override the defaults. The script does not remove any existing broad policies; review and detach those separately after validating the narrower access.

Add application-specific IAM permissions as described under Phase 2 below.

### Step 4: Configure GitHub Repository Secrets

In your repository: **Settings → Secrets and variables → Actions → New repository secret**

| Secret name      | Value                                              |
|------------------|----------------------------------------------------|
| `AWS_ROLE_ARN`   | The role ARN from Step 3e                          |
| `AWS_REGION`     | e.g. `us-east-1`                                  |
| `TF_STATE_BUCKET`| The S3 bucket name from Step 2 (`$STATE_BUCKET`)  |

No AWS access keys are stored anywhere.

### Step 5: Configure Local Variables

```bash
cd terraform/envs/dev
# Create an ignored terraform.tfvars with project_name, aws_region, and environment
```

> `terraform.tfvars` is git-ignored. Never commit it.

To run Terraform locally:

```bash
terraform init \
  -backend-config="bucket=<your-state-bucket>" \
  -backend-config="region=<your-region>"

terraform plan
```

GitHub Actions supplies the same resource variables through `TF_VAR_project_name`, `TF_VAR_environment`, and `TF_VAR_aws_region` in `.github/workflows/deploy.yml`. Backend values remain separate and are passed to `terraform init` from GitHub Secrets.

### Step 6: Add Your Resources and Deploy

1. Add your application resources to `terraform/envs/dev/main.tf` or reusable modules
2. Commit and push to `main`
3. GitHub Actions builds the API package and runs a Terraform plan; review the result.
4. Run the `Deploy` workflow manually on `main` with `apply` selected. It creates a fresh plan and applies it.

Monitor the run under the **Actions** tab in your repository. See the root README for application-specific permissions and user setup. A local Terraform plan also requires the Linux-built `backend/dist/api.zip` archive.

### Phase 2 deployment permissions

The bootstrap grants the GitHub role scoped access to Terraform state, but no permissions to create the application's resources. Older runs attached `AmazonS3FullAccess`; rerunning the bootstrap adds the scoped policy but does not remove that older policy. To prepare the additional Phase 2 policy, run from the repository root using an AWS administrator's credentials. Confirm the account ID and replace the region if necessary:

```bash
aws sts get-caller-identity
python scripts/generate-deployer-policy.py --account-id 987127901122 --region us-east-1 > /tmp/auto-budget-deployer-policy.json
```

The generator only prints JSON and makes no AWS changes. Review `/tmp/auto-budget-deployer-policy.json`, then attach it as an inline policy to the existing role:

```bash
aws iam put-role-policy \
  --role-name github-actions-terraform-deployer \
  --policy-name auto-budget-dev-deployment \
  --policy-document file:///tmp/auto-budget-deployer-policy.json
```

This command creates or replaces the inline policy with that name. It leaves the role's trust policy and existing S3 access unchanged. The account and region must match the Terraform deployment. The generator targets the `auto-budget-dev` resource names configured in GitHub Actions and the standard `aws` partition.

API Gateway stage creation can require the additional `apigateway:TagResource` permission on the stage collection even when the HTTP-verb permissions are already allowed. The generated policy includes it for stage collections in the chosen region. AWS IAM Access Analyzer may flag that action as invalid because its public authorization reference currently lists HTTP-verb actions; if the policy cannot be attached or the error persists, investigate the returned AWS error before broadening permissions.

The table, function, execution role, log group, alarm, and budget permissions target this project's names, and `iam:PassRole` is limited to the API role and Lambda. Cognito pool creation and management covers **all user pools in the specified account and region**, since their IDs are generated by AWS. API Gateway management covers **all APIs in that region**, including unrelated APIs. Some list and describe operations require `Resource: "*"`. The ability to modify the Lambda execution role's inline policy is powerful and can be used to grant that role additional permissions; this policy is not a strict security boundary against a compromised GitHub workflow. Restrict changes to the main branch and review workflow changes carefully.

After attaching the policy, run the `Deploy` workflow with `apply` unchecked and review the new plan. Then run it with `apply` checked. Terraform may require additional permissions as resources or AWS provider behavior change. If a prior apply partially created resources outside state, investigate any existing-resource conflicts before deleting or importing them.

---

## .gitignore Requirements

The following must always be git-ignored (already configured in this repo):

```
.terraform/          # local provider cache
*.tfstate            # state files contain resource IDs and secrets
*.tfstate.*          # backup and numbered state files
terraform.tfvars     # local variable values (may contain account-specific info)
backend.hcl          # local backend config
*.tfplan             # plan output files
```

Never commit any of these files. If a `.tfstate` file is accidentally committed, treat it as a credentials leak and rotate any exposed values immediately.

---

## Adding More Environments

1. Copy `terraform/envs/dev/` to `terraform/envs/prod/`
2. Update the `environment` variable default to `"prod"`
3. Use a different backend `key` (e.g., `prod/terraform.tfstate`) — you can reuse the same S3 bucket
4. Add a new GitHub Actions workflow or extend `deploy.yml` with a second job

---

## Troubleshooting

**OIDC token exchange fails (`AssumeRoleWithWebIdentity`):**
- Verify the trust policy `sub` condition exactly matches your `org/repo:ref:refs/heads/main`
- Confirm the workflow has `id-token: write` permission (already set in `deploy.yml`)
- Check the OIDC provider thumbprint is correct for your region

**`BucketAlreadyExists` when creating state bucket:**
- S3 names are global — add a unique suffix (e.g., your AWS account ID or a random string)

**`Error acquiring the state lock`:**
- Another run is in progress, or a previous run crashed and left a lock
- First verify no Terraform operation is running, then use `terraform force-unlock <LOCK_ID>`

**`Access Denied` during Terraform init:**
- Verify the IAM role has S3 permissions for the state object and its `.tflock` lock file
- Check that the role ARN in `AWS_ROLE_ARN` matches what was created in Step 3

**Resources not deleted when removed from config:**
- Confirm remote state is initialised (backend block is present in `main.tf`)
- Run `terraform state list` to verify the resource is tracked
