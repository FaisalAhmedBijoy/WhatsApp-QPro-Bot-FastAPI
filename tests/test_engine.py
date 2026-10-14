from app.conversation.engine import WELCOME
from app.whatsapp.messages import ButtonsMessage, ImageMessage, ListMessage, TextMessage
from tests.conftest import Chat, button_ids, row_ids


async def _to_confirm(chat: Chat, service: str = "svc:cash-deposit"):
    await chat.say("Hi")
    await chat.tap("div:dhaka")
    branches = (await chat.tap("dist:gazipur"))[0]
    await chat.tap(row_ids(branches)[0])  # Gazipur Branch
    return await chat.tap(service)


async def test_hi_sends_welcome_and_division_list(chat):
    reply = await chat.say("Hi")
    assert isinstance(reply[0], TextMessage) and reply[0].body == WELCOME
    assert isinstance(reply[1], ListMessage)
    assert "div:dhaka" in row_ids(reply[1])


async def test_full_flow_issues_token_with_text_and_image(chat):
    reply = await _to_confirm(chat)
    assert isinstance(reply[0], ButtonsMessage)
    assert button_ids(reply[0])[0] == "cf:yes"

    text, image = await chat.tap("cf:yes")
    assert "Token No: *D001*" in text.body
    assert "Cash Deposit" in text.body and "Gazipur Branch, Gazipur" in text.body
    assert isinstance(image, ImageMessage) and image.content == b"PNG:D001"


async def test_second_token_increments(chat):
    await _to_confirm(chat)
    await chat.tap("cf:yes")
    await chat.say("Hi")
    reply = await chat.tap("last:use")  # remembered branch
    assert "svc:cash-deposit" in row_ids(reply[0])
    await chat.tap("svc:cash-deposit")
    text, _ = await chat.tap("cf:yes")
    assert "*D002*" in text.body


async def test_returning_user_is_offered_last_branch(chat):
    await _to_confirm(chat)
    await chat.tap("cf:yes")
    reply = await chat.say("hello")
    assert isinstance(reply[0], ButtonsMessage)
    assert "Gazipur Branch" in reply[0].body
    assert button_ids(reply[0]) == ["last:use", "last:other"]


async def test_district_list_paginates_beyond_ten(chat):
    await chat.say("Hi")
    page1 = (await chat.tap("div:dhaka"))[0]  # Dhaka division has 13 districts with branches
    ids = row_ids(page1)
    assert len(ids) <= 10 and ids[-1] == "pg:district:1"
    page2 = (await chat.tap("pg:district:1"))[0]
    ids2 = row_ids(page2)
    assert ids2[0] != ids[0] and "pg:district:0" in ids2
    assert len(set(ids[:-1]) | set(i for i in ids2 if not i.startswith("pg:"))) == 13


async def test_branch_list_paginates_in_dhaka_district(chat):
    await chat.say("Hi")
    await chat.tap("div:dhaka")
    page1 = (await chat.tap("dist:dhaka"))[0]  # 14 branches
    assert row_ids(page1)[-1] == "pg:branch:1"


async def test_typed_branch_name_selects_branch(chat):
    await chat.say("Hi")
    reply = await chat.say("gulshan")
    assert isinstance(reply[0], TextMessage) and "Gulshan Branch" in reply[0].body
    assert "svc:cash-deposit" in row_ids(reply[1])


async def test_ambiguous_search_shows_matches(chat):
    await chat.say("Hi")
    reply = await chat.say("bra")  # every branch name contains "Branch"
    assert isinstance(reply[0], ListMessage)
    assert len(row_ids(reply[0])) == 10


async def test_unknown_branch_search_reprompts(chat):
    await chat.say("Hi")
    reply = await chat.say("zzzzz")
    assert "No branch found" in reply[0].body and isinstance(reply[1], ListMessage)


async def test_service_sub_page_and_back(chat):
    await chat.say("Hi")
    await chat.say("gulshan")
    sub = (await chat.tap("svc:more-services"))[0]
    assert "svc:remittance" in row_ids(sub) and row_ids(sub)[-1] == "nav:back"
    root = (await chat.tap("nav:back"))[0]
    assert "svc:more-services" in row_ids(root)
    confirm = (await chat.tap("svc:remittance"))[0]
    assert "Remittance" in confirm.body


async def test_typed_service_name_is_accepted(chat):
    await chat.say("Hi")
    await chat.say("gulshan")
    reply = await chat.say("withdrawal")
    assert isinstance(reply[0], ButtonsMessage) and "Withdrawal" in reply[0].body


async def test_back_walks_up_the_hierarchy(chat):
    await chat.say("Hi")
    await chat.tap("div:dhaka")
    await chat.tap("dist:gazipur")
    assert "dist:gazipur" in row_ids((await chat.say("back"))[0])
    assert "div:dhaka" in row_ids((await chat.say("back"))[0])


async def test_cancel_clears_session(chat):
    await _to_confirm(chat)
    reply = await chat.say("cancel")
    assert "cancelled" in reply[0].body
    reply = await chat.tap("cf:yes")  # stale Confirm tap after cancel must not issue a token
    assert not any(isinstance(m, ImageMessage) for m in reply)


async def test_double_confirm_issues_only_one_token(chat):
    await _to_confirm(chat)
    first = await chat.tap("cf:yes")
    second = await chat.tap("cf:yes")
    assert any(isinstance(m, ImageMessage) for m in first)
    assert not any(isinstance(m, ImageMessage) for m in second)


async def test_closed_branch_message(chat, settings, repo):
    settings.mock_closed_branches = [repo.search_branches("gulshan")[0].code]
    await chat.say("Hi")
    await chat.say("gulshan")
    await chat.tap("svc:cash-deposit")
    reply = await chat.tap("cf:yes")
    assert "not accepting tokens" in reply[0].body


async def test_unauthorised_number_is_rejected(engine, settings):
    settings_auth = settings.model_copy(update={"authorized_numbers": ["8801999999999"]})
    from app.conversation.engine import ConversationEngine
    locked = ConversationEngine(engine._repo, engine._store, engine._tokens, settings_auth)
    reply = await Chat(locked, wa_id="8801700000001").say("Hi")
    assert "not authorised" in reply[0].body


async def test_any_first_message_starts_conversation(chat):
    reply = await chat.say("I need a token")
    assert reply[0].body == WELCOME
