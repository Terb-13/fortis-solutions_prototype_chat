"""
Fortis Edge CS Agent — ASGI entry point for FastAPI.

Deploy locally with: uvicorn main:app --reload
Vercel serverless invokes this module via @vercel/python.
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
