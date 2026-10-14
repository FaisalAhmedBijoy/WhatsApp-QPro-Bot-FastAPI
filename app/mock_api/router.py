"""Mock of the Queue Pro Token Management API, for the demo.

POST /mock/api/v1/tokens  ->  {"token_no": "D002", ...}

Numbers are sequential per branch + service + day, with a per-service letter prefix.
Behaviour can be tuned with MOCK_LATENCY_MS, MOCK_FAILURE_RATE and MOCK_CLOSED_BRANCHES.
"""

import asyncio
import random
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/mock/api/v1", tags=["mock-token-api"])

# Letter prefix per service code. Unknown services fall back to "G" (as in the kiosk sample "G002").
SERVICE_PREFIXES = {
    "CASH_DEPOSIT": "D",
    "CASH_WITHDRAWAL": "W",
    "ACCOUNT_OPENING": "A",
    "ACCOUNT_STATEMENT": "S",
    "LOAN_APPLICATION": "L",
    "CARD_SERVICES": "C",
    "REMITTANCE": "R",
    "CUSTOMER_SERVICE": "G",
}
DAILY_LIMIT = 999


class MockTokenRequest(BaseModel):
    branch_code: str
    service_code: str
    mobile_no: str
    channel: str = "WHATSAPP"
    request_id: str


class MockTokenResponse(BaseModel):
    token_no: str
    branch_code: str
    service_code: str
    issued_at: datetime
    waiting_count: int
    status: str = "ISSUED"


class MockTokenState:
    def __init__(self) -> None:
        self.counters: dict[tuple[str, str, str], int] = {}
        self.by_request_id: dict[str, MockTokenResponse] = {}
        self.lock = asyncio.Lock()


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error_code": code, "message": message})


@router.post("/tokens", response_model=MockTokenResponse)
async def issue_token(body: MockTokenRequest, request: Request) -> MockTokenResponse:
    settings = request.app.state.settings
    state: MockTokenState = request.app.state.mock_token_state

    if settings.mock_latency_ms:
        await asyncio.sleep(settings.mock_latency_ms / 1000)
    if settings.mock_failure_rate and random.random() < settings.mock_failure_rate:
        raise HTTPException(status_code=503, detail="Simulated outage")
    if body.branch_code in settings.mock_closed_branches:
        raise _error(409, "BRANCH_CLOSED", "Branch is not accepting tokens right now")

    async with state.lock:
        if body.request_id in state.by_request_id:  # idempotent retry
            return state.by_request_id[body.request_id]

        now = datetime.now(ZoneInfo(settings.timezone))
        key = (now.date().isoformat(), body.branch_code, body.service_code)
        number = state.counters.get(key, 0) + 1
        if number > DAILY_LIMIT:
            raise _error(409, "LIMIT_REACHED", "Daily token limit reached for this service")
        state.counters[key] = number

        result = MockTokenResponse(
            token_no=f"{SERVICE_PREFIXES.get(body.service_code, 'G')}{number:03d}",
            branch_code=body.branch_code,
            service_code=body.service_code,
            issued_at=now,
            waiting_count=(number - 1) % 12,  # fake "people ahead" figure
        )
        state.by_request_id[body.request_id] = result
        return result
