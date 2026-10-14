import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.config import Settings, get_settings
from app.conversation.engine import ConversationEngine
from app.conversation.store import KeyValueStore, MemoryStore, RedisStore
from app.masterdata.repository import MasterDataRepository
from app.mock_api.router import MockTokenState
from app.mock_api.router import router as mock_router
from app.routers import simulator, webhook
from app.tokens.provider import MockTokenProvider, QueueProTokenProvider, TokenProvider
from app.whatsapp.client import CloudApiSender, DryRunSender, Sender

log = logging.getLogger(__name__)


def _build_token_provider(settings: Settings, app: FastAPI, http: httpx.AsyncClient) -> tuple[TokenProvider, httpx.AsyncClient | None]:
    if settings.token_provider == "queuepro":
        if not settings.queuepro_token_api_url:
            raise ValueError("QUEUEPRO_TOKEN_API_URL is required when TOKEN_PROVIDER=queuepro")
        return QueueProTokenProvider(http, settings.queuepro_token_api_url, settings.queuepro_api_key), None
    if settings.mock_token_api_url:
        return MockTokenProvider(http, settings.mock_token_api_url), None
    # In-process call to the mock router mounted on this app: no port or second process needed.
    local = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mock",
                              timeout=settings.token_api_timeout_seconds)
    return MockTokenProvider(local, "/mock/api/v1/tokens"), local


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        http = httpx.AsyncClient(timeout=settings.token_api_timeout_seconds)
        store: KeyValueStore = RedisStore(settings.redis_url) if settings.session_backend == "redis" else MemoryStore()
        tokens, local_http = _build_token_provider(settings, app, http)
        sender: Sender = (
            CloudApiSender(http, access_token=settings.whatsapp_access_token,
                           phone_number_id=settings.whatsapp_phone_number_id,
                           api_version=settings.graph_api_version, base_url=settings.graph_api_base_url)
            if settings.whatsapp_mode == "cloud" else DryRunSender()
        )
        repo = MasterDataRepository(settings.master_data_dir)

        app.state.store = store
        app.state.sender = sender
        app.state.engine = ConversationEngine(repo, store, tokens, settings)
        log.info("Bot ready: whatsapp=%s tokens=%s sessions=%s", settings.whatsapp_mode,
                 settings.token_provider, settings.session_backend)
        try:
            yield
        finally:
            if local_http:
                await local_http.aclose()
            await http.aclose()
            await store.close()

    app = FastAPI(title="BRAC Bank Queue Pro - WhatsApp Bot", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.mock_token_state = MockTokenState()

    app.include_router(webhook.router)
    app.include_router(mock_router)
    if settings.enable_simulator:
        app.include_router(simulator.router)

    @app.get("/health", tags=["ops"])
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
