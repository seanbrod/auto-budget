# Automated Budget Dashboard

A personal budgeting dashboard organized by calendar year and month. Phase 1 provides a local application foundation; it does not yet connect accounts or store financial data.

## Run locally

Requirements: Conda and Node.js 22 or later. From the project root, create the Conda environment once:

```sh
conda env create -f environment.yml
```

If the `auto-budget` environment already exists, skip this step.

Start the API in one terminal:

```sh
conda activate auto-budget
cd backend
python -m pip install -e '.[dev]'
python -m uvicorn app.main:app --reload --port 8000
```

Start the frontend in another terminal:

```sh
cd frontend
npm ci
npm run dev
```

Open the address printed by Vite, usually `http://localhost:5173`, to see the dashboard. Keep both terminals running. Port 8000 serves the API, not the dashboard, so opening `http://localhost:8000/` returns 404. To check the API directly, open `http://localhost:8000/api/health`. Vite proxies `/api` requests to the API on port 8000.

## Checks

Run `ruff check .` and `pytest` in `backend`, and `npm run build` in `frontend`. GitHub Actions runs these checks on pull requests and pushes to main. The existing Terraform deployment remains separate; see [terraform/README.md](terraform/README.md).

To build the Lambda archive, run `python package_lambda.py` from `backend` on Linux with Python 3.12 and the target Lambda architecture. It creates `backend/dist/api.zip`; CI also checks packaging. This does not deploy the API.

Authentication, Plaid, storage, and application deployment will be added in later phases. Do not enter financial credentials into this scaffold.
