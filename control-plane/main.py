import hashlib
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import models  # noqa: F401 - registers tables on Base.metadata
from db import Base, engine
from executor import spend as executor_spend  # noqa: F401 - registers spend_reservations table
from executor import step_up as executor_step_up  # noqa: F401 - registers step_up_requests table
from ledger import models as ledger_models  # noqa: F401 - registers ledger_rows + triggers
from ledger.events import LedgerEvent
from ledger.writer import append_event_best_effort
from mandates import nonce as mandate_nonce  # noqa: F401 - registers mandate_nonces table
from routers import demo


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Pramaan Control Plane", lifespan=lifespan)
app.include_router(demo.router)


@app.exception_handler(RequestValidationError)
async def malformed_request_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Logged even though nothing here has been verified yet -- a rogue
    agent trying to smuggle amount_paise or an idempotency_key back into the
    request body (rejected by GatedCheckoutRequest's extra="forbid") is
    arguably the most demo-relevant attack this endpoint sees, and it would
    otherwise leave zero trace. Best-effort (no reservation/money exists yet
    to justify failing closed) and deliberately never logs the raw body --
    only its digest and the pydantic error locations -- since this is an
    UNAUTHENTICATED write path into a table that can never be pruned.
    """
    raw_body = await request.body()
    append_event_best_effort(
        event_type=LedgerEvent.REQUEST_REJECTED_MALFORMED,
        now=datetime.now(timezone.utc),
        transaction_id=str(uuid.uuid4()),
        error_detail=str([err.get("loc") for err in exc.errors()])[:500],
        explanation=f"malformed request body (sha256={hashlib.sha256(raw_body).hexdigest()})",
    )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catches only genuinely UNEXPECTED failures -- MandateError and
    RazorpayToolError are already handled inside routers/demo.py and never
    reach here. This exists so the best-covered code path isn't only the
    happy one: an unaudited 500 in a governance demo reads as "the gate
    crashed", not "the gate blocked", which is the wrong impression.
    """
    append_event_best_effort(
        event_type=LedgerEvent.REQUEST_ERRORED,
        now=datetime.now(timezone.utc),
        transaction_id=str(uuid.uuid4()),
        error_detail=type(exc).__name__,
        explanation="an unexpected error occurred while handling this request",
    )
    return JSONResponse(status_code=500, content={"detail": "internal error"})


@app.get("/health")
def health():
    return {"status": "ok"}
