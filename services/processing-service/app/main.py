from __future__

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.worker import process_unchunked_documents

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",)
logger = logging.getLogger("processing-service")

POLL_INTERVAL_SECONDS = 30
_background_task: asyncio.task | None = None

async def poll_loop() -> None:
    "Background loop that picks up unchunked documents periodically."""
    while True:
        try:
            result = await process_unchunked_documents()
            if result["documents_processed"] > 0:
                logger.info("Processed %d documents, %d total chunks",
                            result["documents_processed"],
                            result["chunks_created"],)
        except Exception:
            logger.exception("Error processing documents")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

@asynccontextmanager
async def lifespan(application: FastAPI):
    global _background_task
    from shared.utils.db import ensure_schema

    ensure_schema()
    _background_task = asyncio.create_task(poll_loop())
    logger.info("Processing worker started (polling every %ds)", POLL_INTERVAL_SECONDS)
    yield
    if _background_task:
        _background_task.cancel()
 
 
app = FastAPI(
    title="OMSCS Processing Service",
    version="0.1.0",
    lifespan=lifespan,
)
 
 
@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok", "service": "processing-service"}
 
 
@app.post("/process")
async def trigger_processing() -> dict:
    """Manual trigger for processing unchunked documents."""
    return await process_unchunked_documents()
 