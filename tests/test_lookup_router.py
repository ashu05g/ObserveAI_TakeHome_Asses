import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.lookup import router as lookup_router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(lookup_router)
    return TestClient(app)


def _caller_response():
    return {
        "records": [
            {
                "id": "recJaneDoe",
                "fields": {
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "phone": "+14155550001",
                    "claim_id": "CLM-2024-0001",
                    "claim_status": "approved",
                    "claim_type": "auto",
                    "claim_date": "2024-08-12",
                },
            }
        ]
    }


class TestLookupEndpoint:
    @respx.mock
    def test_hit_returns_caller_payload(self, client):
        respx.get(
            "https://api.airtable.com/v0/appTest1234567890/callers"
        ).respond(200, json=_caller_response())

        response = client.get("/lookup", params={"phone": "(415) 555-0001"})

        assert response.status_code == 200
        body = response.json()
        assert body == {
            "found": True,
            "first_name": "Jane",
            "last_name": "Doe",
            "claim_id": "CLM-2024-0001",
            "claim_status": "approved",
            "claim_type": "auto",
            "claim_date": "2024-08-12",
            "airtable_record_id": "recJaneDoe",
        }

    @respx.mock
    def test_miss_returns_found_false(self, client):
        respx.get(
            "https://api.airtable.com/v0/appTest1234567890/callers"
        ).respond(200, json={"records": []})

        response = client.get("/lookup", params={"phone": "+19999999999"})

        assert response.status_code == 200
        assert response.json() == {"found": False}

    def test_invalid_phone_returns_400(self, client):
        response = client.get("/lookup", params={"phone": "not-a-phone"})
        assert response.status_code == 400
        assert "expected a 10-digit" in response.json()["detail"]

    def test_missing_phone_returns_422(self, client):
        response = client.get("/lookup")
        assert response.status_code == 422

    def test_empty_phone_returns_422(self, client):
        response = client.get("/lookup", params={"phone": ""})
        assert response.status_code == 422

    @respx.mock
    def test_normalizes_before_querying_airtable(self, client):
        route = respx.get(
            "https://api.airtable.com/v0/appTest1234567890/callers"
        ).respond(200, json=_caller_response())

        client.get("/lookup", params={"phone": "415.555.0001"})

        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(str(route.calls.last.request.url)).query)
        assert query["filterByFormula"] == ["{phone}='+14155550001'"]
