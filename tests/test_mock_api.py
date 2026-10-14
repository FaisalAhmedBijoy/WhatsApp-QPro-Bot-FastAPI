def _body(**kw):
    return {"branch_code": "BR001", "service_code": "CASH_DEPOSIT", "mobile_no": "8801700000000",
            "request_id": kw.pop("request_id", "r1"), **kw}


async def test_tokens_are_sequential_per_branch_and_service(mock_http):
    first = (await mock_http.post("/mock/api/v1/tokens", json=_body(request_id="a"))).json()
    second = (await mock_http.post("/mock/api/v1/tokens", json=_body(request_id="b"))).json()
    other = (await mock_http.post("/mock/api/v1/tokens", json=_body(request_id="c", service_code="CASH_WITHDRAWAL"))).json()
    assert (first["token_no"], second["token_no"], other["token_no"]) == ("D001", "D002", "W001")


async def test_same_request_id_returns_same_token(mock_http):
    a = (await mock_http.post("/mock/api/v1/tokens", json=_body(request_id="same"))).json()
    b = (await mock_http.post("/mock/api/v1/tokens", json=_body(request_id="same"))).json()
    assert a["token_no"] == b["token_no"] == "D001"


async def test_unknown_service_uses_default_prefix(mock_http):
    data = (await mock_http.post("/mock/api/v1/tokens", json=_body(service_code="SOMETHING_NEW"))).json()
    assert data["token_no"] == "G001"


async def test_closed_branch_returns_error_code(mock_http, settings):
    settings.mock_closed_branches = ["BR001"]
    resp = await mock_http.post("/mock/api/v1/tokens", json=_body())
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_code"] == "BRANCH_CLOSED"
