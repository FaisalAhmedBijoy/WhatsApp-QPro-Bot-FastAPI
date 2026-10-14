import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.conversation.engine import ConversationEngine
from app.conversation.store import MemoryStore
from app.masterdata.repository import MasterDataRepository
from app.mock_api.router import MockTokenState
from app.mock_api.router import router as mock_router
from app.tokens.provider import MockTokenProvider
from app.whatsapp.inbound import IncomingMessage


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, media_dir=tmp_path / "media", whatsapp_verify_token="verify-me")


@pytest.fixture
def repo(settings) -> MasterDataRepository:
    return MasterDataRepository(settings.master_data_dir)


@pytest.fixture
def mock_api(settings) -> FastAPI:
    api = FastAPI()
    api.state.settings = settings
    api.state.mock_token_state = MockTokenState()
    api.include_router(mock_router)
    return api


@pytest.fixture
async def mock_http(mock_api):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mock_api), base_url="http://mock") as client:
        yield client


@pytest.fixture
def engine(repo, settings, mock_http) -> ConversationEngine:
    provider = MockTokenProvider(mock_http, "/mock/api/v1/tokens")
    return ConversationEngine(repo, MemoryStore(), provider, settings, render=lambda ticket: b"PNG:" + ticket.token_no.encode())


class Chat:
    """Drives the engine like a WhatsApp user."""

    def __init__(self, engine: ConversationEngine, wa_id: str = "8801700000001"):
        self.engine, self.wa_id, self._n = engine, wa_id, 0

    async def say(self, text: str):
        return await self._send(text=text)

    async def tap(self, reply_id: str):
        return await self._send(reply_id=reply_id)

    async def _send(self, **kw):
        self._n += 1
        return await self.engine.handle(IncomingMessage(message_id=f"m{self._n}", wa_id=self.wa_id, **kw))


@pytest.fixture
def chat(engine) -> Chat:
    return Chat(engine)


def row_ids(message) -> list[str]:
    return [r["id"] for r in message.payload()["interactive"]["action"]["sections"][0]["rows"]]


def button_ids(message) -> list[str]:
    return [b["reply"]["id"] for b in message.payload()["interactive"]["action"]["buttons"]]
