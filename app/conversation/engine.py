"""Conversation state machine.

    IDLE ─"Hi"─► [LAST_BRANCH] ─► DIVISION ─► DISTRICT ─► BRANCH ─► SERVICE (tree) ─► CONFIRM ─► token
                                   └──── typed text at any branch step = branch search ────┘

Taps carry self-describing ids ("div:dhaka", "br:BR001", "svc:cash-deposit", "pg:district:1"),
so a tap on an older list still does the right thing. Commands anytime: hi/menu, back, cancel.
"""

import asyncio
import logging
import math
import uuid
import weakref
from collections.abc import Callable
from enum import StrEnum
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.config import Settings
from app.conversation.store import KeyValueStore
from app.masterdata.repository import Branch, MasterDataRepository, ServiceNode
from app.rendering.token_image import TokenTicket, render_token_image
from app.tokens.provider import TokenProvider, TokenProviderError, TokenRequest
from app.whatsapp.inbound import IncomingMessage
from app.whatsapp.messages import (
    LIST_MAX_ROWS, Button, ButtonsMessage, ImageMessage, ListMessage, OutgoingMessage, Row, TextMessage,
)

log = logging.getLogger(__name__)

GREETINGS = {"hi", "hello", "hey", "start", "menu", "assalamualaikum", "salam"}
CANCEL_WORDS = {"cancel", "stop", "exit", "quit"}
BACK_WORDS = {"back", "previous"}
YES_WORDS = {"yes", "y", "confirm", "ok", "okay"}
NO_WORDS = {"no", "n", "change"}

WELCOME = "Welcome to the BRAC Bank Queue Pro System."
HELP_FOOTER = "Type back or menu anytime"
NOT_AUTHORIZED = "Sorry, this number is not authorised to request tokens. Please contact your branch."
CANCELLED = "Your request has been cancelled. Send *Hi* whenever you want a token."
EXPIRED = "Your previous session has expired, so let's start again."
UNSUPPORTED = "Sorry, I can only understand text and menu selections."
TOKEN_ERRORS = {
    "BRANCH_CLOSED": "Sorry, this branch is not accepting tokens right now. Please try another branch or come back during banking hours.",
    "SERVICE_UNAVAILABLE": "Sorry, this service is not available at this branch right now.",
    "LIMIT_REACHED": "Sorry, today's token limit for this service has been reached.",
}
TOKEN_RETRY = "Sorry, we couldn't generate your token right now. Please tap *Confirm* to try again."


class State(StrEnum):
    IDLE = "idle"
    LAST_BRANCH = "last_branch"
    DIVISION = "division"
    DISTRICT = "district"
    BRANCH = "branch"
    SERVICE = "service"
    CONFIRM = "confirm"


class Session(BaseModel):
    state: State = State.IDLE
    division_id: str | None = None
    district_id: str | None = None
    branch_code: str | None = None
    last_branch_code: str | None = None
    service_page: str | None = None  # current service sub-menu; None = root menu
    service_id: str | None = None
    page: int = 0  # pagination index of the list currently shown

    def reset(self) -> None:
        for name, field in Session.model_fields.items():
            setattr(self, name, field.default)


Reply = list[OutgoingMessage]


class ConversationEngine:
    def __init__(
        self,
        repo: MasterDataRepository,
        store: KeyValueStore,
        tokens: TokenProvider,
        settings: Settings,
        render: Callable[[TokenTicket], bytes] = render_token_image,
    ) -> None:
        self._repo = repo
        self._store = store
        self._tokens = tokens
        self._settings = settings
        self._render = render
        self._tz = ZoneInfo(settings.timezone)
        self._authorized = set(settings.authorized_numbers)
        # Serialise messages from the same user (e.g. a double tap on Confirm) within this process.
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    async def handle(self, msg: IncomingMessage) -> Reply:
        if self._authorized and msg.wa_id not in self._authorized:
            return [TextMessage(NOT_AUTHORIZED)]
        lock = self._locks.setdefault(msg.wa_id, asyncio.Lock())
        async with lock:
            session = await self._load(msg.wa_id)
            reply = await self._dispatch(session, msg)
            await self._save(msg.wa_id, session)
            return reply

    # --- persistence ---

    async def _load(self, wa_id: str) -> Session:
        raw = await self._store.get(f"session:{wa_id}")
        return Session.model_validate(raw) if raw else Session()

    async def _save(self, wa_id: str, session: Session) -> None:
        if session.state == State.IDLE:
            await self._store.delete(f"session:{wa_id}")
        else:
            await self._store.set(f"session:{wa_id}", session.model_dump(mode="json"), self._settings.session_ttl_seconds)

    # --- routing ---

    async def _dispatch(self, s: Session, msg: IncomingMessage) -> Reply:
        if msg.unsupported_type:
            if s.state == State.IDLE:
                return await self._start(s, msg.wa_id)
            return [TextMessage(UNSUPPORTED), *self._prompt(s)]
        if msg.reply_id:
            return await self._on_tap(s, msg.reply_id, msg.wa_id)

        text = msg.text.strip()
        word = text.lower().strip(" !.?")
        if word in GREETINGS:
            return await self._start(s, msg.wa_id)
        if word in CANCEL_WORDS:
            s.reset()
            return [TextMessage(CANCELLED)]
        if word in BACK_WORDS:
            return self._back(s)
        if s.state == State.IDLE:
            return await self._start(s, msg.wa_id)
        return await self._on_text(s, text, word, msg.wa_id)

    async def _start(self, s: Session, wa_id: str) -> Reply:
        s.reset()
        last = await self._store.get(f"last_branch:{wa_id}")
        branch = self._repo.branch(last["code"]) if last else None
        if branch:
            s.state, s.last_branch_code = State.LAST_BRANCH, branch.code
            return [ButtonsMessage(
                body=f"{WELCOME}\n\nLast time you used *{branch.name}*. Would you like a token for this branch?",
                buttons=[Button("last:use", "Use this branch"), Button("last:other", "Choose another")],
            )]
        s.state = State.DIVISION
        return [TextMessage(WELCOME), *self._prompt(s)]

    async def _on_tap(self, s: Session, reply_id: str, wa_id: str) -> Reply:
        kind, _, value = reply_id.partition(":")
        repo = self._repo

        if kind == "last":
            branch = repo.branch(s.last_branch_code) if value == "use" and s.last_branch_code else None
            if branch:
                return self._select_branch(s, branch)
            return self._goto(s, State.DIVISION)
        if kind == "div" and (division := repo.division(value)):
            s.division_id, s.district_id = division.id, None
            return self._goto(s, State.DISTRICT)
        if kind == "dist" and (district := repo.district(value)):
            s.division_id, s.district_id = district.division_id, district.id
            return self._goto(s, State.BRANCH)
        if kind == "br" and (branch := repo.branch(value)):
            return self._select_branch(s, branch)
        if kind == "pg":
            list_kind, _, page = value.partition(":")
            if list_kind == s.state.value and page.isdigit():
                s.page = int(page)
            return self._prompt(s)
        if kind == "nav" and value == "back":
            return self._back(s)
        if kind == "svc":
            if not s.branch_code:
                return [TextMessage(EXPIRED), *await self._start(s, wa_id)]
            if node := repo.service_node(value):
                return self._select_service(s, node)
        if kind == "cf":
            if value == "restart":
                return await self._start(s, wa_id)
            if not (s.branch_code and s.service_id):
                return [TextMessage(EXPIRED), *await self._start(s, wa_id)]
            if value == "yes" and s.state == State.CONFIRM:
                return await self._issue_token(s, wa_id)
            if value == "change":
                s.service_id = None
                return self._goto(s, State.SERVICE)
        # Unknown or stale tap: show the current step again.
        return self._prompt(s) if s.state != State.IDLE else await self._start(s, wa_id)

    async def _on_text(self, s: Session, text: str, word: str, wa_id: str) -> Reply:
        if s.state in (State.DIVISION, State.DISTRICT, State.BRANCH):
            return self._search_branch(s, text)
        if s.state == State.LAST_BRANCH:
            if word in YES_WORDS:
                return await self._on_tap(s, "last:use", wa_id)
            if word in NO_WORDS:
                return await self._on_tap(s, "last:other", wa_id)
            return self._search_branch(s, text)
        if s.state == State.SERVICE:
            if node := self._repo.find_service_on_page(text, s.service_page):
                return self._select_service(s, node)
            return [TextMessage("Please choose a service from the list."), *self._prompt(s)]
        if s.state == State.CONFIRM:
            if word in YES_WORDS:
                return await self._issue_token(s, wa_id)
            if word in NO_WORDS:
                return await self._on_tap(s, "cf:change", wa_id)
            return self._prompt(s)
        return self._prompt(s)

    # --- transitions ---

    def _goto(self, s: Session, state: State) -> Reply:
        s.state, s.page = state, 0
        if state == State.SERVICE:
            s.service_page = None
        return self._prompt(s)

    def _select_branch(self, s: Session, branch: Branch) -> Reply:
        s.branch_code, s.district_id, s.division_id = branch.code, branch.district_id, branch.division_id
        s.service_id = None
        return self._goto(s, State.SERVICE)

    def _select_service(self, s: Session, node: ServiceNode) -> Reply:
        if node.type == "page":
            s.service_page, s.page = node.id, 0
            return self._prompt(s)
        s.service_id, s.state = node.id, State.CONFIRM
        return self._prompt(s)

    def _back(self, s: Session) -> Reply:
        if s.state == State.DISTRICT:
            return self._goto(s, State.DIVISION)
        if s.state == State.BRANCH:
            return self._goto(s, State.DISTRICT)
        if s.state == State.SERVICE:
            if s.service_page:
                page = self._repo.service_node(s.service_page)
                s.service_page, s.page = page.parent_id if page else None, 0
                return self._prompt(s)
            return self._goto(s, State.BRANCH)
        if s.state == State.CONFIRM:
            s.state, s.service_id, s.page = State.SERVICE, None, 0
            return self._prompt(s)
        if s.state in (State.IDLE, State.LAST_BRANCH):
            return self._goto(s, State.DIVISION)
        return self._prompt(s)

    def _search_branch(self, s: Session, query: str) -> Reply:
        scoped = {State.DISTRICT: {"division_id": s.division_id},
                  State.BRANCH: {"district_id": s.district_id}}.get(s.state, {})
        hits = self._repo.search_branches(query, **scoped) or self._repo.search_branches(query)
        if not hits:
            return [TextMessage(f"No branch found for \"{query}\". Please choose from the list or type at least 3 letters of the branch name."),
                    *self._prompt(s)]
        if len(hits) == 1:
            return [TextMessage(f"Branch selected: *{hits[0].name}*"), *self._select_branch(s, hits[0])]
        shown = hits[:LIST_MAX_ROWS]
        more = f"\nShowing the first {len(shown)} of {len(hits)}. Type more of the name to narrow it down." if len(hits) > len(shown) else ""
        return [ListMessage(
            header="Search results",
            body=f"Branches matching \"{query}\":{more}",
            button="Choose branch",
            rows=[Row(f"br:{b.code}", b.name, b.address) for b in shown],
            footer=HELP_FOOTER,
        )]

    # --- prompts ---

    def _prompt(self, s: Session) -> Reply:
        repo = self._repo
        if s.state == State.DIVISION:
            rows = [Row(f"div:{d.id}", d.name, f"{len(repo.districts(d.id))} district(s)") for d in repo.divisions()]
            return [self._list(s, rows, header="Step 1 of 3: Division", button="Choose division",
                               body="Please select your *division*.\nYou can also type a branch name to search.")]

        if s.state == State.DISTRICT and (division := repo.division(s.division_id or "")):
            rows = [Row(f"dist:{d.id}", d.name, f"{len(repo.branches(d.id))} branch(es)") for d in repo.districts(division.id)]
            return [self._list(s, rows, header="Step 2 of 3: District", button="Choose district",
                               body=f"Division: *{division.name}*\nPlease select your *district*.")]

        if s.state == State.BRANCH and (district := repo.district(s.district_id or "")):
            rows = [Row(f"br:{b.code}", b.name, b.address) for b in repo.branches(district.id)]
            return [self._list(s, rows, header="Step 3 of 3: Branch", button="Choose branch",
                               body=f"District: *{district.name}*\nPlease select your *branch*, or type its name.")]

        if s.state == State.SERVICE and (branch := repo.branch(s.branch_code or "")):
            rows = [
                Row(f"svc:{n.id}", f"{n.label} ›" if n.type == "page" else n.label,
                    "More options" if n.type == "page" else "")
                for n in repo.service_menu(s.service_page)
            ]
            back = Row("nav:back", "⬅ Back") if s.service_page else None
            page_node = repo.service_node(s.service_page) if s.service_page else None
            title = f"*{page_node.label}*\n" if page_node else ""
            return [self._list(s, rows, header="Select a Service", button="Choose service", back=back,
                               body=f"Branch: *{branch.name}*\n\n{title}What service do you need today?")]

        if s.state == State.CONFIRM and (branch := repo.branch(s.branch_code or "")) and (
            service := repo.service_node(s.service_id or "")
        ):
            district = repo.district(branch.district_id)
            return [ButtonsMessage(
                header="Confirm your token",
                body=f"Branch: *{branch.name}*{', ' + district.name if district else ''}\nService: *{service.label}*\n\nShall I generate your token?",
                buttons=[Button("cf:yes", "✅ Confirm"), Button("cf:change", "Change service"),
                         Button("cf:restart", "Start over")],
            )]

        # Session points at data that no longer exists: start fresh.
        s.reset()
        s.state = State.DIVISION
        return self._prompt(s)

    def _list(self, s: Session, rows: list[Row], *, header: str, body: str, button: str,
              back: Row | None = None) -> ListMessage:
        """Build a list message, paginating when rows exceed WhatsApp's 10-row limit."""
        extra = [back] if back else []
        footer = HELP_FOOTER
        if len(rows) + len(extra) > LIST_MAX_ROWS:
            size = LIST_MAX_ROWS - 2 - len(extra)  # leave room for Previous / More
            pages = math.ceil(len(rows) / size)
            s.page = min(max(s.page, 0), pages - 1)
            nav = []
            if s.page > 0:
                nav.append(Row(f"pg:{s.state.value}:{s.page - 1}", "◀ Previous", f"Page {s.page} of {pages}"))
            if s.page < pages - 1:
                nav.append(Row(f"pg:{s.state.value}:{s.page + 1}", "More ▶", f"Page {s.page + 2} of {pages}"))
            rows = rows[s.page * size:(s.page + 1) * size] + nav
            footer = f"Page {s.page + 1} of {pages} · {HELP_FOOTER}"
        return ListMessage(header=header, body=body, button=button, rows=rows + extra, footer=footer)

    # --- token ---

    async def _issue_token(self, s: Session, wa_id: str) -> Reply:
        branch = self._repo.branch(s.branch_code or "")
        service = self._repo.service_node(s.service_id or "")
        if not branch or not service or not service.code:
            return [TextMessage(EXPIRED), *await self._start(s, wa_id)]

        request = TokenRequest(branch_code=branch.code, service_code=service.code, mobile_no=wa_id,
                               request_id=uuid.uuid4().hex)
        try:
            result = await self._tokens.issue_token(request)
        except TokenProviderError as exc:
            log.warning("Token request failed for branch=%s service=%s: %s %s",
                        branch.code, service.code, exc.code, exc.message)
            if exc.code in TOKEN_ERRORS:
                s.reset()
                return [TextMessage(TOKEN_ERRORS[exc.code] + "\n\nSend *Hi* to start again.")]
            return [TextMessage(TOKEN_RETRY), *self._prompt(s)]

        log.info("Token issued: %s branch=%s service=%s", result.token_no, branch.code, service.code)
        district = self._repo.district(branch.district_id)
        branch_label = f"{branch.name}, {district.name}" if district else branch.name
        issued_at = result.issued_at.astimezone(self._tz)
        png = await asyncio.to_thread(self._render, TokenTicket(
            token_no=result.token_no, branch_name=branch_label, service_name=service.label,
            issued_at=issued_at, waiting_count=result.waiting_count,
        ))

        await self._store.set(f"last_branch:{wa_id}", {"code": branch.code}, self._settings.last_branch_ttl_seconds)
        s.reset()

        lines = [
            "✅ *Your token has been generated*", "",
            f"Token No: *{result.token_no}*",
            f"Service: {service.label}",
            f"Branch: {branch_label}",
            f"Date/Time: {issued_at.strftime('%d %b %Y, %I:%M %p')}",
        ]
        if result.waiting_count is not None:
            lines.append(f"Customers ahead of you: {result.waiting_count}")
        lines += ["", "Please wait for your number to be called.", "Send *Hi* to get another token."]
        return [
            TextMessage("\n".join(lines)),
            ImageMessage(content=png, filename=f"token_{result.token_no}.png", caption=f"Your token: {result.token_no}"),
        ]
