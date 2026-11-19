"""Application entry points for local development and AWS Lambda."""

import os

import boto3
from fastapi import FastAPI, HTTPException, Request, Response
from mangum import Mangum
from pydantic import BaseModel

app = FastAPI(title="Auto Budget API", version="0.1.0")


@app.middleware("http")
async def prevent_financial_caching(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/v1/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class HealthResponse(BaseModel):
    status: str


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/api/me")
def me(request: Request, response: Response) -> dict[str, str]:
    # Only API Gateway's verified JWT claims are trusted; direct ASGI calls fail closed.
    event = request.scope.get("aws.event", {})
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    subject = claims.get("sub")
    if not subject or claims.get("token_use") != "access":
        raise HTTPException(status_code=401, detail="Authentication required")
    table_name = os.environ.get("HOUSEHOLD_TABLE")
    if not table_name:
        raise HTTPException(status_code=503, detail="Service not configured")
    result = boto3.client("dynamodb").get_item(
        TableName=table_name,
        Key={"pk": {"S": f"USER#{subject}"}, "sk": {"S": "MEMBERSHIP"}},
        ConsistentRead=True,
    )
    household_id = result.get("Item", {}).get("household_id", {}).get("S")
    if not household_id:
        raise HTTPException(status_code=403, detail="Household access required")
    response.headers["Cache-Control"] = "no-store"
    return {"household_id": household_id}


from app.budget_api import router as budget_router
from app.plaid_api import router as plaid_router
from app.transactions_api import router as transactions_router

app.include_router(plaid_router)
app.include_router(budget_router)
app.include_router(transactions_router)
handler = Mangum(app, lifespan="off")
