from decimal import Decimal

from dealsteal.config import config_from_dict
from dealsteal.skelbiu import SkelbiuClient, parse_skelbiu_price


def test_skelbiu_price_parses_localized_amounts() -> None:
    assert parse_skelbiu_price("1 234,50 €") is not None
    assert parse_skelbiu_price("1 234,50 €").amount == Decimal("1234.50")
    assert parse_skelbiu_price("159 EUR") == parse_skelbiu_price("159 €")


def test_skelbiu_comparables_require_active_detail(monkeypatch) -> None:
    config = config_from_dict(
        {
            "skelbiu_api": {
                "enabled": True,
                "base_url": "http://127.0.0.1:8080",
                "detail_limit": 10,
            }
        }
    )
    client = SkelbiuClient(config)

    class Response:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self.body

    def get(url, params=None, timeout=None):
        del params, timeout
        if url.endswith("/v1/listings"):
            return Response(
                {
                    "items": [
                        {
                            "id": "1",
                            "title": "iPhone 12 128GB",
                            "url": "https://www.skelbiu.lt/skelbimai/one-1.html",
                            "price": "100 €",
                        },
                        {
                            "id": "2",
                            "title": "iPhone 12 128GB sold",
                            "url": "https://www.skelbiu.lt/skelbimai/two-2.html",
                            "price": "90 €",
                        },
                    ]
                }
            )
        if url.endswith("/1"):
            return Response(
                {
                    "id": "1",
                    "title": "iPhone 12 128GB",
                    "url": "https://www.skelbiu.lt/skelbimai/one-1.html",
                    "status": "active",
                    "price": "100 €",
                    "currency": "EUR",
                }
            )
        return Response(
            {
                "id": "2",
                "title": "iPhone 12 128GB sold",
                "status": "sold",
                "price": "90 €",
                "currency": "EUR",
            }
        )

    monkeypatch.setattr(client.session, "get", get)
    result = client.search_active("iPhone 12", detail_limit=10)

    assert [item.item_id for item in result.listings] == ["1"]
    assert result.rejected_inactive == 1
    assert result.listings[0].is_active
    client.close()
