from datetime import datetime, timedelta
import gc
from typing import Any
from fastapi import FastAPI, HTTPException
from app.api.deps import get_redis_client
from fastapi_pagination import add_pagination
from starlette.middleware.cors import CORSMiddleware
from app.api.v1.api import api_router as api_router_v1
from app.core.config import settings
from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend
from fastapi_async_sqlalchemy import SQLAlchemyMiddleware
from contextlib import asynccontextmanager
from app.utils.fastapi_globals import g, GlobalsMiddleware
from transformers import pipeline
from app.api.celery_task import increment
from app.core.celery import celery

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    redis_client = await get_redis_client()
    FastAPICache.init(RedisBackend(redis_client), prefix="fastapi-cache")
    # Load a pre-trained sentiment analysis model as a dictionary to an easy cleanup
    models: dict[str, Any] = {
        "sentiment_model": pipeline(
            "sentiment-analysis",
            model="distilbert-base-uncased-finetuned-sst-2-english",
        ),
        "text_generator_model": pipeline("text-generation", model="gpt2"),
    }
    g.set_default("sentiment_model", models["sentiment_model"])
    g.set_default("text_generator_model", models["text_generator_model"])
    print("startup fastapi")
    yield
    # shutdown
    await FastAPICache.clear()
    models.clear()
    g.cleanup()
    gc.collect()


# Core Application Instance
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.API_VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)



app.add_middleware(
    SQLAlchemyMiddleware,
    db_url=settings.ASYNC_DATABASE_URI,
    engine_args={
        "echo": False,
        "pool_pre_ping": True,
        "pool_size": settings.POOL_SIZE,
        "max_overflow": 64,
    },
)
app.add_middleware(GlobalsMiddleware)

# Set all CORS origins enabled
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin) for origin in settings.BACKEND_CORS_ORIGINS],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


class CustomException(Exception):
    http_code: int
    code: str
    message: str

    def __init__(self, http_code: int = None, code: str = None, message: str = None):
        self.http_code = http_code if http_code else 500
        self.code = code if code else str(self.http_code)
        self.message = message


@app.get("/")
async def root():
    """
    An example "Hello world" FastAPI route.
    """


    val = increment.delay(1)  #wait
    increment.delay(9)  #no wait
    tomorrow = datetime.utcnow() + timedelta(seconds=20)
    new_tomorrow = increment.apply_async(args=[7], eta=tomorrow)
    print("task_id", new_tomorrow.task_id)
    print("result", new_tomorrow.result)
    print("status", new_tomorrow.status)
    increment.apply_async(args=[20], expires=datetime.now() + timedelta(seconds=10))
    
    
    # if oso.is_allowed(user, "read", message):    
    return {"message": new_tomorrow.task_id}


@app.get("/2")
async def root(task_id: Any):
    """
    An example "Hello world" FastAPI route.
    """
    # Retrieve the result using the task ID
    async_result = celery.AsyncResult(task_id)
    print("state", async_result.state)
    print("ready", async_result.ready())
    print("successful", async_result.successful())
    if async_result.ready():
        print(f"Task {task_id} exists and has completed.")
        result = async_result.get(timeout=1.0)
        return {"message": result}
    else:        
        raise HTTPException(status_code=404, detail="Task {task_id} does not exist or is still running.")
    

# Add Routers
app.include_router(api_router_v1, prefix=settings.API_V1_STR)
add_pagination(app)
