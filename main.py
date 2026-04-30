"""
Fortis Edge CS Agent — ASGI entry point for FastAPI.

Deploy locally with: uvicorn main:app --reload
Vercel serverless invokes this module via @vercel/python.
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from fortis_cs_agent.api import router as fortis_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

app = FastAPI(
    title="Fortis Edge CS Agent",
    description="Customer success agent backend (Grok + Twilio + Supabase)",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(fortis_router)


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Many frontends read `error` instead of FastAPI's default `detail` only."""
    detail = exc.detail
    if isinstance(detail, str):
        payload: dict = {"detail": detail, "error": detail}
    else:
        payload = {"detail": detail, "error": "Request failed. See detail."}
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": exc.errors(),
            "error": "Invalid request body or query parameters.",
        },
    )

