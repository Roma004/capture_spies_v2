"""HTTP API smoke tests."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from backend.api import LakeServer


@pytest.fixture
def server(tmp_path):
    srv = LakeServer(("127.0.0.1", 0), tmp_path / "api.duckdb")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


def call(server, method, path, body=None):
    url = f"http://127.0.0.1:{server.server_address[1]}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


ROW = {
    "first_name": "IVAN", "last_name": "PETROV", "sex": "M",
    "birth_date": "1980-01-01", "document": "1234 567890", "pnr": "ABC123",
    "ticket_number": "5555555555555555", "departure_date": "2017-01-01",
    "departure_time": "10:00", "flight_number": "SU1234",
    "from_code": "SVO", "to_code": "LED", "codeshare": "false",
}


def test_api_flow(server):
    status, body = call(server, "POST", "/batches", {
        "manifest": {
            "source_name": "sirena-export",
            "schema_version": "v1",
            "batch_kind": "delta",
            "effective_date": "2017-01-01",
        },
        "rows": [ROW],
    })
    assert status == 200, body
    assert body["inserted"] == 1

    status, body = call(server, "GET", "/query?sql=" +
                        urllib.parse.quote("SELECT count(*) AS n FROM gold.flights"))
    assert status == 200, body
    assert body["rows"][0]["n"] == 1

    status, body = call(server, "GET", "/views/persons?limit=10")
    assert status == 200 and len(body["rows"]) == 1

    status, body = call(server, "GET", "/views/flights?from=SVO&to=LED")
    assert status == 200 and len(body["rows"]) == 1

    status, body = call(server, "GET", "/views/anomalies?status=open")
    assert status == 200

    status, body = call(server, "GET", "/batches/00000000-0000-0000-0000-000000000000")
    assert status == 404


def test_query_rejects_writes(server):
    status, _ = call(server, "GET", "/query?sql=" +
                     urllib.parse.quote("DELETE FROM gold.flights"))
    assert status == 400

