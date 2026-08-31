from contextlib import asynccontextmanager

from fastapi import FastAPI

import models  # noqa: F401 - registers tables on Base.metadata
from db import Base, engine
from executor import spend as executor_spend  # noqa: F401 - registers spend_reservations table
from executor import step_up as executor_step_up  # noqa: F401 - registers step_up_requests table
from mandates import nonce as mandate_nonce  # noqa: F401 - registers mandate_nonces table
from routers import demo


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Pramaan Control Plane", lifespan=lifespan)
app.include_router(demo.router)


@app.get("/health")
def health():
    return {"status": "ok"}
