"""GET /customers/search -- privacy correction: this route used to return
raw phone/email straight from customer_predictions.search_customers(),
the same PII exposure Ask Celeste's masking discipline was built to close
(see test_customer_lookup.py). Calls the endpoint function directly, same
pattern test_case_api.py already uses for main.py routes.
"""

from backend.app import main
from backend.app.services import customer_predictions


async def test_customer_search_masks_phone_and_never_returns_raw_email(monkeypatch):
    monkeypatch.setattr(
        customer_predictions,
        "search_customers",
        lambda name, phone, email: [
            {"partner_id": 1, "name": "Ann Silva", "phone": "+94 77 300 0003", "email": "ann@example.com", "order_count": 5},
        ],
    )

    response = await main.customer_search(name="Ann")

    assert response["status"] == "ok"
    result = response["results"][0]
    assert set(result) == {"partner_id", "name", "phone_masked", "order_count"}
    assert result["phone_masked"] == "•••• 0003"
    blob = str(response)
    assert "+94 77 300 0003" not in blob
    assert "ann@example.com" not in blob


async def test_customer_search_handles_no_matches(monkeypatch):
    monkeypatch.setattr(customer_predictions, "search_customers", lambda name, phone, email: [])
    response = await main.customer_search(name="Nobody")
    assert response == {"status": "ok", "results": []}
