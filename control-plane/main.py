from contextlib import asynccontextmanager

from fastapi import FastAPI

import models  # noqa: F401 - registers tables on Base.metadata
from db import Base, engine
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
