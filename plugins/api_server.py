#!/usr/bin/env python3
"""
plugins/api_server.py - Local HTTP API for the Invest dashboard (v5.9)

Runs a tiny stdlib HTTP server inside tracker.py on 127.0.0.1:8765, serving
JSON endpoints that the dashboard's invest.html calls instead of localStorage.

Why this exists:
  * Persists invest data to invest.db so a browser wipe doesn't lose purchases
  * Centralizes pokemontcg.io API calls behind a 12-hour cache
  * Provides KPI rollups computed server-side from authoritative SQL data

Architecture:
  * Plugin pattern: instantiated by ApiServer_Plugin in plugins.py
  * ThreadingHTTPServer in a daemon thread (dies cleanly when tracker exits)
  * stdlib BaseHTTPRequestHandler (no Flask/FastAPI dep)
  * Routes dispatched per (method, path) - returns 404 on unknown
  * Per-request exception isolation: a bad request never kills the server

Security:
  * Bound to 127.0.0.1 only (no LAN exposure)
  * CORS allowlist: http://localhost:8080 + http://127.0.0.1:8080
  * Requests with a different Origin header are 403'd and logged (tripwire)
  * No-Origin requests (curl, scripts) are allowed - they cannot exploit CORS
    and any local process that can hit loopback can already read the DBs
  * All DB writes go through invest_store / market_data_refresh which use
    parameterized queries

Endpoints:
  GET  /api/health                       sanity check
  GET  /api/invest/list                  all purchases
  GET  /api/invest/get?id=N              single purchase
  POST /api/invest/add                   create (body: purchase JSON)
  PUT  /api/invest/update?id=N           partial update (body: fields to change)
  DELETE /api/invest/delete?id=N         delete
  POST /api/invest/bulk_import           bulk insert (body: {purchases:[...]})
  GET  /api/invest/is_empty              {empty: bool}  (for client migration)
  GET  /api/invest/kpi                   KPI summary
  GET  /api/invest/snapshots?id=N        market snapshot history for one purchase
  GET  /api/market/value?pokemontcg_id=X cached value (or fetch if stale)
  POST /api/market/refresh               trigger manual refresh (returns immediately)
  GET  /api/market/cooldown              {available, remaining_seconds}
  GET  /api/market/log?limit=20          recent refresh runs (audit trail)
"""

import os
import sys
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# Make shared + sibling plugins importable
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_PLUGIN_DIR)
sys.path.insert(0, _ROOT_DIR)
sys.path.insert(0, _PLUGIN_DIR)

import invest_store
import market_data_refresh

log = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────
API_HOST = "127.0.0.1"
API_PORT = 8765
API_VERSION = "5.9"

ALLOWED_ORIGINS = frozenset({
    "http://localhost:8080",
    "http://127.0.0.1:8080",
})

# Reject any single request body larger than 5MB. Protects against memory abuse
# from a misbehaving client. invest data is tiny - a sane payload is <50KB.
MAX_BODY_BYTES = 5 * 1024 * 1024


# ── Request handler ──────────────────────────────────────────────────────────
class _ApiHandler(BaseHTTPRequestHandler):
    """Per-request handler. Dispatches by (method, path)."""

    # ── Logging override ─────────────────────────────────────────────────
    def log_message(self, format, *args):
        # Stock BaseHTTPRequestHandler logs to stderr; route to our logger instead
        log.debug("[api] " + format % args)

    # ── Origin / CORS ────────────────────────────────────────────────────
    def _origin_allowed(self) -> tuple[bool, str]:
        """
        Decide whether to honor this request based on the Origin header.

        Returns (allowed, origin_to_echo).

        Rules:
          - No Origin header -> allow (script/curl, can't be a CORS attack)
          - Origin in allowlist -> allow + echo Origin in CORS header
          - Origin not in allowlist -> deny (403, logged)
        """
        origin = self.headers.get("Origin", "")
        if not origin:
            return True, ""
        if origin in ALLOWED_ORIGINS:
            return True, origin
        # Tripwire: if this fires, something's hitting our loopback API from
        # an unexpected origin. Worth knowing about.
        log.warning(f"[api] BLOCKED request from disallowed origin: {origin!r} path={self.path}")
        return False, origin

    def _cors_headers(self, origin: str) -> None:
        """Emit CORS headers. Only called when origin is allowed."""
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "3600")

    # ── Response helpers ─────────────────────────────────────────────────
    def _send_json(self, status: int, payload, origin: str = "") -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors_headers(origin)
        self.end_headers()
        self.wfile.write(body)

    def _send_403(self) -> None:
        body = b'{"error":"origin not allowed"}'
        self.send_response(403)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        """Read and parse JSON body. Returns parsed dict or None on error."""
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except (TypeError, ValueError):
            return None
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            log.warning(f"[api] rejected oversized body: {length} bytes")
            return None
        try:
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as e:
            log.warning(f"[api] body parse error: {e}")
            return None

    # ── HTTP method dispatch ─────────────────────────────────────────────
    def do_OPTIONS(self):
        """CORS preflight."""
        allowed, origin = self._origin_allowed()
        if not allowed:
            return self._send_403()
        self.send_response(204)
        self._cors_headers(origin)
        self.end_headers()

    def do_GET(self):
        allowed, origin = self._origin_allowed()
        if not allowed:
            return self._send_403()
        try:
            self._dispatch_get(origin)
        except Exception as e:
            log.exception(f"[api] GET {self.path} unhandled: {e}")
            self._send_json(500, {"error": "internal", "detail": str(e)}, origin)

    def do_POST(self):
        allowed, origin = self._origin_allowed()
        if not allowed:
            return self._send_403()
        try:
            self._dispatch_post(origin)
        except Exception as e:
            log.exception(f"[api] POST {self.path} unhandled: {e}")
            self._send_json(500, {"error": "internal", "detail": str(e)}, origin)

    def do_PUT(self):
        allowed, origin = self._origin_allowed()
        if not allowed:
            return self._send_403()
        try:
            self._dispatch_put(origin)
        except Exception as e:
            log.exception(f"[api] PUT {self.path} unhandled: {e}")
            self._send_json(500, {"error": "internal", "detail": str(e)}, origin)

    def do_DELETE(self):
        allowed, origin = self._origin_allowed()
        if not allowed:
            return self._send_403()
        try:
            self._dispatch_delete(origin)
        except Exception as e:
            log.exception(f"[api] DELETE {self.path} unhandled: {e}")
            self._send_json(500, {"error": "internal", "detail": str(e)}, origin)

    # ── Route dispatch ───────────────────────────────────────────────────
    def _dispatch_get(self, origin: str) -> None:
        url = urlparse(self.path)
        path = url.path
        params = parse_qs(url.query)

        if path == "/api/health":
            return self._send_json(200, {
                "ok": True,
                "version": API_VERSION,
                "endpoints": [
                    "/api/invest/list", "/api/invest/get", "/api/invest/kpi",
                    "/api/invest/is_empty", "/api/invest/snapshots",
                    "/api/market/value", "/api/market/cooldown", "/api/market/log",
                ],
            }, origin)

        if path == "/api/invest/list":
            return self._send_json(200, invest_store.list_purchases(), origin)

        if path == "/api/invest/get":
            pid = _int_param(params, "id")
            if pid is None:
                return self._send_json(400, {"error": "id required"}, origin)
            row = invest_store.get_purchase(pid)
            if row is None:
                return self._send_json(404, {"error": "not found"}, origin)
            return self._send_json(200, row, origin)

        if path == "/api/invest/is_empty":
            return self._send_json(200, {"empty": invest_store.is_empty()}, origin)

        if path == "/api/invest/kpi":
            return self._send_json(200, invest_store.kpi_summary(), origin)

        if path == "/api/invest/snapshots":
            pid = _int_param(params, "id")
            if pid is None:
                return self._send_json(400, {"error": "id required"}, origin)
            limit = _int_param(params, "limit", default=100)
            return self._send_json(200, invest_store.get_snapshots(pid, limit), origin)

        if path == "/api/market/value":
            tcg_id = (params.get("pokemontcg_id") or [""])[0]
            if not tcg_id:
                return self._send_json(400, {"error": "pokemontcg_id required"}, origin)
            force = (params.get("force") or ["0"])[0] in ("1", "true", "yes")
            result = market_data_refresh.get_market_value(tcg_id, force_refresh=force)
            return self._send_json(200, result, origin)

        if path == "/api/market/cooldown":
            return self._send_json(200, market_data_refresh.manual_cooldown_status(), origin)

        if path == "/api/market/log":
            limit = _int_param(params, "limit", default=20)
            return self._send_json(200, market_data_refresh.get_recent_refresh_log(limit), origin)

        return self._send_json(404, {"error": "unknown route", "path": path}, origin)

    def _dispatch_post(self, origin: str) -> None:
        url = urlparse(self.path)
        path = url.path

        if path == "/api/invest/add":
            body = self._read_json_body()
            if body is None:
                return self._send_json(400, {"error": "invalid JSON body"}, origin)
            try:
                new_id = invest_store.add_purchase(body)
                return self._send_json(201, {"id": new_id}, origin)
            except ValueError as e:
                return self._send_json(400, {"error": "validation", "detail": str(e)}, origin)

        if path == "/api/invest/bulk_import":
            body = self._read_json_body()
            if body is None or "purchases" not in body:
                return self._send_json(400, {"error": "body must be {purchases: [...]}"}, origin)
            replace_all = bool(body.get("replace_all", False))
            try:
                result = invest_store.bulk_import(body["purchases"], replace_all=replace_all)
                return self._send_json(200, result, origin)
            except Exception as e:
                return self._send_json(500, {"error": "bulk_import failed", "detail": str(e)}, origin)

        if path == "/api/market/refresh":
            result = market_data_refresh.trigger_manual_refresh()
            status = 200 if result.get("status") == "started" else 429
            return self._send_json(status, result, origin)

        return self._send_json(404, {"error": "unknown route", "path": path}, origin)

    def _dispatch_put(self, origin: str) -> None:
        url = urlparse(self.path)
        path = url.path
        params = parse_qs(url.query)

        if path == "/api/invest/update":
            pid = _int_param(params, "id")
            if pid is None:
                return self._send_json(400, {"error": "id required"}, origin)
            body = self._read_json_body()
            if body is None:
                return self._send_json(400, {"error": "invalid JSON body"}, origin)
            try:
                updated = invest_store.update_purchase(pid, body)
                if not updated:
                    return self._send_json(404, {"error": "not found or no changes"}, origin)
                return self._send_json(200, {"updated": True, "id": pid}, origin)
            except ValueError as e:
                return self._send_json(400, {"error": "validation", "detail": str(e)}, origin)

        return self._send_json(404, {"error": "unknown route", "path": path}, origin)

    def _dispatch_delete(self, origin: str) -> None:
        url = urlparse(self.path)
        path = url.path
        params = parse_qs(url.query)

        if path == "/api/invest/delete":
            pid = _int_param(params, "id")
            if pid is None:
                return self._send_json(400, {"error": "id required"}, origin)
            deleted = invest_store.delete_purchase(pid)
            if not deleted:
                return self._send_json(404, {"error": "not found"}, origin)
            return self._send_json(200, {"deleted": True, "id": pid}, origin)

        return self._send_json(404, {"error": "unknown route", "path": path}, origin)


def _int_param(params: dict, key: str, default=None):
    """Pull and parse an integer from parsed query string."""
    raw = (params.get(key) or [None])[0]
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


# ── Server lifecycle ─────────────────────────────────────────────────────────
class _ApiServerThread(threading.Thread):
    """
    Runs ThreadingHTTPServer in a daemon thread.
    Daemon = dies automatically when tracker.py exits, no shutdown handshake needed.
    """
    def __init__(self):
        super().__init__(daemon=True, name="api_server")
        self._httpd = None
        self._ready = threading.Event()

    def run(self):
        try:
            self._httpd = ThreadingHTTPServer((API_HOST, API_PORT), _ApiHandler)
            self._ready.set()
            log.info(f"[api_server] listening on http://{API_HOST}:{API_PORT}")
            self._httpd.serve_forever(poll_interval=1.0)
        except OSError as e:
            log.error(
                f"[api_server] could not bind {API_HOST}:{API_PORT} - {e}. "
                f"Is another tracker.py instance already running?"
            )
            self._ready.set()  # unblock waiter
        except Exception as e:
            log.exception(f"[api_server] fatal: {e}")
            self._ready.set()

    def stop(self):
        if self._httpd:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
                log.info("[api_server] stopped")
            except Exception as e:
                log.warning(f"[api_server] shutdown error: {e}")


# ── Implementation class ─────────────────────────────────────────────────────
class ApiServer:
    """
    Implementation class - instantiated by ApiServer_Plugin in plugins.py:
        from api_server import ApiServer
        self._api = ApiServer(config, products)
        self._api.start(schedule)
    """
    def __init__(self, config: dict, products: list):
        self.config = config
        self._thread = None
        log.info(f"[api_server] initialized (will bind {API_HOST}:{API_PORT})")

    def start(self, schedule) -> None:
        # No scheduled jobs - this plugin runs an async server thread instead.
        # We expect invest_store and market_data_refresh to already be initialized
        # by the time this starts (their schemas exist on disk).
        self._thread = _ApiServerThread()
        self._thread.start()
        # Wait briefly so log lines appear in the right order on startup
        self._thread._ready.wait(timeout=2.0)

    def stop(self) -> None:
        if self._thread:
            self._thread.stop()


# ── CLI for diagnostics ──────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print(f"Standalone API server on http://{API_HOST}:{API_PORT}")
    print("Endpoints listed at /api/health")
    print("Ctrl+C to stop.")
    httpd = ThreadingHTTPServer((API_HOST, API_PORT), _ApiHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()
        httpd.server_close()
        print("\nStopped.")
