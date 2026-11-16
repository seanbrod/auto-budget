"""Application entry points for local development and AWS Lambda."""

import os

import boto3
from fastapi import FastAPI, HTTPException, Request, Response
from mangum import Mangum
from pydantic import BaseModel

app = FastAPI(title="Auto Budget API", version="0.1.0")


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


handler = Mangum(app, lifespan="off")
