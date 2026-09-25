"""
FastAPI frontend service for your NAFDAC Apify actor.

This does NOT run the crawl/answer pipeline itself. It's a thin wrapper
around the Apify REST API:

    1. POST /api/runs        -> starts a run of your deployed actor with
                                 {"query": "..."} as input, returns immediately
    2. GET  /api/runs/{id}   -> poll this; once the run finishes it returns
                                 the single output record the actor pushes via
                                 `Actor.push_data(output)` in main.py

A static single-page frontend (static/index.html) calls these two endpoints
and polls every few seconds, since crawls can take longer than a typical
HTTP request timeout.

Setup
-----
    pip install -r requirements.txt
    export APIFY_TOKEN=apify_api_xxxxxxxx
    export APIFY_ACTOR_ID=your-username~your-actor-name   # or its actor ID
    uvicorn app:app --reload

Then open http://127.0.0.1:8000/
"""

import os
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

APIFY_API_BASE = "https://api.apify.com/v2"
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
APIFY_ACTOR_ID = os.environ.get("APIFY_ACTOR_ID", "")

app = FastAPI(title="NAFDAC Search Frontend")


def _require_config() -> None:
    if not APIFY_TOKEN or not APIFY_ACTOR_ID:
        raise HTTPException(
            500,
            "Server is missing APIFY_TOKEN / APIFY_ACTOR_ID environment variables. "
            "See README.md.",
        )


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class AskRequest(BaseModel):
    query: str = Field(..., min_length=1)
    maxResults: Optional[int] = None
    maxPages: Optional[int] = None
    crawlDepth: Optional[int] = None
    startUrls: Optional[List[str]] = None


class RunHandle(BaseModel):
    runId: str
    datasetId: str
    status: str


class RunStatus(BaseModel):
    runId: str
    status: str
    finished: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


_TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/api/runs", response_model=RunHandle)
async def start_run(req: AskRequest) -> RunHandle:
    """Kick off one actor run for this query. Returns immediately (does not wait)."""
    _require_config()

    actor_input: Dict[str, Any] = {"query": req.query}
    if req.maxResults:
        actor_input["maxResults"] = req.maxResults
    if req.maxPages:
        actor_input["maxPages"] = req.maxPages
    if req.crawlDepth:
        actor_input["crawlDepth"] = req.crawlDepth
    if req.startUrls:
        actor_input["startUrls"] = req.startUrls

    url = f"{APIFY_API_BASE}/acts/{APIFY_ACTOR_ID}/runs"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, params={"token": APIFY_TOKEN}, json=actor_input)

    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, f"Apify error starting run: {resp.text[:500]}")

    data = resp.json()["data"]
    return RunHandle(runId=data["id"], datasetId=data["defaultDatasetId"], status=data["status"])


@app.get("/api/runs/{run_id}", response_model=RunStatus)
async def get_run(run_id: str) -> RunStatus:
    """Poll this until `finished` is true. The frontend calls it every few seconds."""
    _require_config()

    async with httpx.AsyncClient(timeout=30) as client:
        run_resp = await client.get(
            f"{APIFY_API_BASE}/actor-runs/{run_id}", params={"token": APIFY_TOKEN}
        )
        if run_resp.status_code >= 400:
            raise HTTPException(run_resp.status_code, f"Apify error reading run: {run_resp.text[:500]}")
        run_data = run_resp.json()["data"]
        status = run_data["status"]

        if status not in _TERMINAL_STATUSES:
            return RunStatus(runId=run_id, status=status, finished=False)

        if status != "SUCCEEDED":
            return RunStatus(
                runId=run_id,
                status=status,
                finished=True,
                error=f"Actor run ended with status {status}.",
            )

        dataset_id = run_data["defaultDatasetId"]
        items_resp = await client.get(
            f"{APIFY_API_BASE}/datasets/{dataset_id}/items",
            params={"token": APIFY_TOKEN, "format": "json"},
        )
        if items_resp.status_code >= 400:
            raise HTTPException(
                items_resp.status_code, f"Apify error reading dataset: {items_resp.text[:500]}"
            )
        items = items_resp.json()

    if not items:
        return RunStatus(runId=run_id, status=status, finished=True, error="Actor produced no output record.")

    # main.py pushes exactly one record (the answer object) to the default dataset.
    return RunStatus(runId=run_id, status=status, finished=True, result=items[0])


# Serve the static frontend at "/" (must be mounted last, after the /api routes above).
app.mount("/", StaticFiles(directory="static", html=True), name="static")