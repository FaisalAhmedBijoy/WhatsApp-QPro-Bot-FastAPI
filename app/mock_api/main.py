"""Standalone mock Token API, to run it as a separate service (closer to production topology):

    uvicorn app.mock_api.main:app --port 9000
    MOCK_TOKEN_API_URL=http://localhost:9000/mock/api/v1/tokens   # in the bot's .env
"""

from fastapi import FastAPI

from app.config import get_settings
from app.mock_api.router import MockTokenState, router

app = FastAPI(title="Queue Pro Token API (mock)")
app.state.settings = get_settings()
app.state.mock_token_state = MockTokenState()
app.include_router(router)
