"""Minimal read/write HTTP API for the consolidation service (SPEC 12).

Only the Python standard library is used so the service has no extra
runtime dependencies beyond duckdb/pyarrow.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time as dtime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import LAKE_PATH
from .ingest import BatchRejected, DuplicateBatch, Manifest
from .pipeline import connect, ingest_batch

# Tables/views that /query is allowed to read.
WHITELIST = {
    "raw.manifests", "bronze.batches", "bronze.records",
    "silver.records_norm", "identity.persons", "identity.person_keys",
    "identity.person_events", "identity.person_conflicts",
    "identity.pending_keys", "ref.translit_map", "ref.program_map",
    "ref.airport_map", "ref.null_policy", "gold.flights",
    "gold.flight_schedule", "gold.anomalies",
    "identity.person_canonical", "gold.flight_dedup",
    "gold.flights_enriched", "gold.anomalies_open",
}


def json_default(value):
    if isinstance(value, (date, datetime, dtime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def row_to_dict(columns, row):
    return {c: v for c, v in zip(columns, row)}


class Handler(BaseHTTPRequestHandler):
    server_version = "FlightLake/1.0"

    # -- plumbing -----------------------------------------------------
    def log_message(self, fmt, *args):  # quieter logs
        pass

    def _send(self, status: int, payload) -> None:
        body = json.dumps(payload, default=json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    # -- routes -------------------------------------------------------
    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/batches":
            try:
                body = self._body()
                manifest = body.get("manifest", body)
                rows = body.get("rows")
                if rows is None:
                    self._send(422, {"error": "rows are required"})
                    return
                if not isinstance(rows, list):
                    self._send(422, {"error": "rows must be a list"})
                    return
                if not manifest.get("content_checksum"):
                    from .ingest import Manifest as M

                    m = M.from_dict({**manifest, "content_checksum": "sha256:" + "0" * 64})
                    from .pipeline import _iter_checksum

                    checksum = _iter_checksum(rows, m.columns)
                    manifest = {**manifest, "content_checksum": checksum}
                result = ingest_batch(self.server.lake, manifest,
                                      lambda: iter(rows),
                                      run_key=self.server.run_key)
                self._send(200, result)
            except (BatchRejected, DuplicateBatch) as exc:
                self._send(getattr(exc, "status", 409), {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                self._send(500, {"error": str(exc)})
            return
        self._send(404, {"error": "not found"})

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        try:
            if parsed.path.startswith("/batches/"):
                bid = parsed.path.split("/", 2)[2]
                row = self.server.lake.execute(
                    "SELECT batch_id, source_name, schema_version, batch_kind, "
                    "effective_date, trust_identity, trust_fact, is_schedule, "
                    "content_checksum, loaded_at FROM bronze.batches WHERE batch_id=?",
                    [bid]).fetchone()
                if not row:
                    self._send(404, {"error": "unknown batch"})
                    return
                cols = [d[0] for d in self.server.lake.description]
                self._send(200, row_to_dict(cols, row))
                return
            if parsed.path == "/query":
                sql = (qs.get("sql") or [""])[0]
                rows, cols = self._query(sql)
                self._send(200, {"columns": cols,
                                 "rows": [row_to_dict(cols, r) for r in rows]})
                return
            if parsed.path == "/views/persons":
                limit = int((qs.get("limit") or ["100"])[0])
                offset = int((qs.get("offset") or ["0"])[0])
                rows, cols = self._sql(
                    "SELECT * FROM identity.persons ORDER BY person_id LIMIT ? OFFSET ?",
                    [limit, offset])
                self._send(200, {"columns": cols,
                                 "rows": [row_to_dict(cols, r) for r in rows]})
                return
            if parsed.path == "/views/flights":
                where, params = [], []
                for key, col in (("person_id", "person_id"), ("from", "from_code"),
                                 ("to", "to_code")):
                    if key in qs:
                        where.append(f"{col}=?")
                        params.append(qs[key][0])
                limit = int((qs.get("limit") or ["100"])[0])
                offset = int((qs.get("offset") or ["0"])[0])
                sql = "SELECT * FROM gold.flights"
                if where:
                    sql += " WHERE " + " AND ".join(where)
                sql += " ORDER BY flight_id LIMIT ? OFFSET ?"
                rows, cols = self._sql(sql, params + [limit, offset])
                self._send(200, {"columns": cols,
                                 "rows": [row_to_dict(cols, r) for r in rows]})
                return
            if parsed.path == "/views/anomalies":
                where, params = [], []
                for key, col in (("type", "type"), ("status", "status")):
                    if key in qs:
                        where.append(f"{col}=?")
                        params.append(qs[key][0])
                if "person_id" in qs:
                    where.append("person_ids LIKE ?")
                    params.append(f'%"{qs["person_id"][0]}"%')
                limit = int((qs.get("limit") or ["100"])[0])
                offset = int((qs.get("offset") or ["0"])[0])
                sql = "SELECT * FROM gold.anomalies"
                if where:
                    sql += " WHERE " + " AND ".join(where)
                sql += " ORDER BY anomaly_id LIMIT ? OFFSET ?"
                rows, cols = self._sql(sql, params + [limit, offset])
                self._send(200, {"columns": cols,
                                 "rows": [row_to_dict(cols, r) for r in rows]})
                return
            self._send(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            self._send(400, {"error": str(exc)})

    # -- helpers ------------------------------------------------------
    def _sql(self, sql, params):
        cur = self.server.lake.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return cur.fetchall(), cols

    def _query(self, sql: str):
        s = (sql or "").strip().rstrip(";").strip()
        if not s:
            raise ValueError("sql is required")
        if not re.match(r"(?is)^(select|with)\b", s):
            raise ValueError("only SELECT queries are allowed")
        if ";" in s:
            raise ValueError("only a single statement is allowed")
        lowered = s.lower()
        for token in ("insert ", "update ", "delete ", "drop ", "create ",
                      "alter ", "attach ", "copy ", "pragma ", "call ",
                      "export ", "install ", "load "):
            if token in lowered:
                raise ValueError("only read-only queries are allowed")
        for m in re.finditer(r"(?i)\b(?:from|join)\s+([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)",
                             s):
            if m.group(1).lower() not in WHITELIST:
                raise ValueError(f"table not whitelisted: {m.group(1)}")
        return self._sql(s, [])


class LakeServer(ThreadingHTTPServer):
    def __init__(self, addr, db_path: Path):
        super().__init__(addr, Handler)
        self.lake = connect(db_path)
        self.run_key = str(Path(db_path).resolve())


def create_server(host: str = "127.0.0.1", port: int = 8000,
                  db_path: Path | str = LAKE_PATH) -> LakeServer:
    return LakeServer((host, int(port)), Path(db_path))


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", default=str(LAKE_PATH))
    args = ap.parse_args()
    srv = create_server(args.host, args.port, args.db)
    print(f"listening on http://{args.host}:{args.port} db={args.db}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
