from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agent import SOURCE_GROUPS, TrustedMedicalAgent


load_dotenv()

app = FastAPI(title="Trusted Medical Search API")


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


default_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
]
allowed_origins = _split_csv(os.getenv("CORS_ALLOW_ORIGINS")) or default_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = TrustedMedicalAgent()


class AskBody(BaseModel):
    query: str


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "Trusted Medical Search API",
        "frontend": os.getenv("FRONTEND_URL", "http://localhost:3000"),
        "docs": "/docs",
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True}


@app.get("/api/sources")
def sources() -> dict[str, Any]:
    return {"groups": SOURCE_GROUPS}


@app.post("/api/ask")
def ask(body: AskBody) -> JSONResponse:
    result = agent.ask(body.query)
    return JSONResponse(result, status_code=200 if result.get("ok", False) else result.get("status", 400))


@app.get("/api/test-llm")
def test_llm() -> JSONResponse:
    result = agent.test_nvidia()
    return JSONResponse(result, status_code=200 if result.get("ok", False) else result.get("status", 400))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
