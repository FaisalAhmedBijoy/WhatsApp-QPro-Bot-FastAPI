# BRAC Bank Queue Pro – WhatsApp Token Bot

Customers send **"Hi"** to the BRAC Bank WhatsApp Business number, pick
**Division → District → Branch → Service**, confirm, and receive their queue token as a
**text message and a token image**. Tokens come from the Queue Pro Token Management API
(a mock API for now), so the existing token logic is reused rather than duplicated.

```
WhatsApp user ⇄ Meta Cloud API ⇄ POST /webhook (FastAPI)
                                   ├─ ConversationEngine (state machine, sessions in memory/Redis)
                                   ├─ MasterDataRepository (branches.json, services.json)
                                   ├─ TokenProvider ──► mock API  |  Queue Pro API
                                   └─ Token image renderer (Pillow) ──► media upload ──► WhatsApp
```

## Run locally (no WhatsApp account needed)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/uvicorn app.main:app --reload
```

Open **http://localhost:8000/dev/chat**, a WhatsApp-like simulator that runs the same engine
as the real webhook. Type `Hi`. API docs are at http://localhost:8000/docs.

Run the tests with `.venv/bin/pip install pytest pytest-asyncio && .venv/bin/python -m pytest`.

## Connect to real WhatsApp (Meta Cloud API)

1. In [Meta for Developers](https://developers.facebook.com/), create an app of type
   **Business** and add the **WhatsApp** product. Meta gives you a test number. Add your own
   phone as a test recipient.
2. Copy the **access token**, **phone number ID** and **app secret** (App settings → Basic)
   into `.env`, and set `WHATSAPP_MODE=cloud`.
3. Expose the app over HTTPS, e.g. `ngrok http 8000`.
4. In WhatsApp → Configuration, set the callback URL to `https://<host>/webhook` and the verify token
   to `WHATSAPP_VERIFY_TOKEN`. Then subscribe to the **messages** field.
5. Send "Hi" to the test number.

For production, use a permanent System User token and complete Meta Business Verification and
display-name approval for the BRAC Bank number. Run with `SESSION_BACKEND=redis` (see
`docker-compose.yml`) and set `ENABLE_SIMULATOR=false`.

## Conversation

| Step | What the user sees | Notes |
|---|---|---|
| Hi | Welcome text and a **division** list | A returning user is offered their last branch first |
| Division → District → Branch | WhatsApp list messages | Lists over 10 rows get **More ▶ / ◀ Previous** rows. Typing a branch name (e.g. `gulshan`) searches instead |
| Service | List built from `services.json` | Supports sub-menus, like the kiosk's "Root page 2" |
| Confirm | Buttons: Confirm / Change service / Start over | |
| Token | Text details + PNG token image | |

You can type `back`, `menu` / `hi` or `cancel` at any step. Taps on older messages still work, and a
second tap on Confirm never issues a second token.

## Adding services and branches

- **Services:** edit `app/masterdata/data/services.json`. Use `"type": "service"` with the `code` sent
  to the Token API, or `"type": "page"` with `children` for a sub-menu. No code changes needed.
- **Branches:** `app/masterdata/data/branches.json` contains **demo data with fictional codes**.
  Replace it with Queue Pro's branch list. Districts and divisions without branches are hidden.
  To load these from Queue Pro directly, re-implement `MasterDataRepository`, keeping the same methods.

## Mock Token API

`POST /mock/api/v1/tokens`

```json
{"branch_code": "BR005", "service_code": "CASH_DEPOSIT", "mobile_no": "8801700000000",
 "channel": "WHATSAPP", "request_id": "6f1c..."}
```
→
```json
{"token_no": "D002", "branch_code": "BR005", "service_code": "CASH_DEPOSIT",
 "issued_at": "2026-10-14T10:42:00+06:00", "waiting_count": 1, "status": "ISSUED"}
```

- Numbers are sequential per branch, service and day. The prefix depends on the service:
  D = deposit, W = withdrawal, A = account opening, S = statement, L = loan, and G for unknown codes.
- A repeated `request_id` returns the same token, so retries are safe.
- To test error handling, set `MOCK_CLOSED_BRANCHES`, `MOCK_FAILURE_RATE` or `MOCK_LATENCY_MS`.
  Errors come back as `{"detail": {"error_code": "BRANCH_CLOSED", "message": "..."}}`.
- Standalone: `uvicorn app.mock_api.main:app --port 9000`, then set `MOCK_TOKEN_API_URL`.

## Switching to the real Queue Pro API

1. Fill in the mappings in `QueueProTokenProvider` (`app/tokens/provider.py`):
   - `build_payload`: the request fields
   - `parse_response`: the token number, time and queue length
   - `parse_error`: error codes mapped to `BRANCH_CLOSED`, `LIMIT_REACHED`, …
2. Set `TOKEN_PROVIDER=queuepro`, `QUEUEPRO_TOKEN_API_URL` and `QUEUEPRO_API_KEY`.
3. Make the `code` values in `branches.json` and `services.json` match Queue Pro's codes.

The conversation, the image and the WhatsApp code do not change.

## Token image

`app/rendering/token_image.py` recreates the kiosk "Your Token" screen at 1080×1350 (portrait,
for phones): the blue gradient with light streaks, the logo, and the white rounded card with
*Your Token / # D002 / Thank You*. A details section adds branch, service, time and queue length.

- **Fonts:** Noto Sans (OFL licence, in `app/assets/fonts`) stands in for Segoe UI.
- **Logo:** `app/assets/images/queuepro_logo.png` is a **low-resolution crop from a screenshot**.
  Replace it with the original logo file for a sharp result.

## Layout

```
app/
  main.py                  app wiring (lifespan builds store, provider, sender, engine)
  config.py                settings from env / .env
  routers/webhook.py       GET verify, POST receive (signature check, de-dup, background work)
  routers/simulator.py     /dev/chat + /dev/simulate
  conversation/engine.py   state machine
  conversation/store.py    MemoryStore / RedisStore
  masterdata/              repository + JSON data
  tokens/provider.py       TokenProvider, Mock + QueuePro adapters
  mock_api/                mock Token Management API
  rendering/token_image.py token PNG
  whatsapp/                inbound parsing, outgoing message models, Cloud API sender
tests/
```

## Not done yet

- **Access control:** `AUTHORIZED_NUMBERS` is a simple allow-list. Real customer linking (OTP or
  account lookup) still needs to be decided.
- **Abuse limits:** limits per phone number (e.g. one active token per service) belong in Queue Pro
  or a Redis counter.
- **Multiple workers:** the duplicate-tap lock is per process. With several workers, use a Redis lock.
- **Language:** messages are English only. Bangla support would need translated texts and a
  `libraqm`-enabled Pillow build for the image.
