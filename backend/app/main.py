"""Application entry points for local development and AWS Lambda."""

from fastapi import FastAPI
from mangum import Mangum
from pydantic import BaseModel

app = FastAPI(title="Auto Budget API", version="0.1.0")


class HealthResponse(BaseModel):
    status: str


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


handler = Mangum(app, lifespan="off")
