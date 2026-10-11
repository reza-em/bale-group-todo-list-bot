"""Minimal Bale Bot API client (Telegram-compatible) on raw requests. The token never reaches logs."""
import os, logging, requests

log = logging.getLogger("api")
BASE = os.environ.get("BALE_API_BASE", "https://tapi.bale.ai")


class ApiError(Exception):
    """The server answered ok=false (code is the HTTP-like error_code)."""
    def __init__(self, method, code, desc):
        super().__init__("%s: %s %s" % (method, code, desc))
        self.method, self.code, self.desc = method, code, desc or ""


class NetError(Exception):
    """Network/timeout problem: the request may not have arrived. Never contains the token."""


class BaleApi:
    def __init__(self, token):
        self._token = token
        self._s = requests.Session()

    def _safe(self, s):
        return str(s).replace(self._token, "<TOKEN>") if self._token else str(s)

    def call(self, method, _http_timeout=20, **params):
        url = "%s/bot%s/%s" % (BASE, self._token, method)
        params = {k: v for k, v in params.items() if v is not None}
        try:
            r = self._s.post(url, json=params, timeout=_http_timeout)
        except requests.RequestException as e:
            raise NetError("%s: %s" % (method, self._safe(type(e).__name__ + " " + str(e))[:200])) from None
        try:
            data = r.json()
        except ValueError:
            raise ApiError(method, r.status_code, "non-JSON response") from None
        if not data.get("ok"):
            raise ApiError(method, data.get("error_code", r.status_code), self._safe(data.get("description", "")))
        return data.get("result")

    def get_updates(self, offset=None, poll=25):
        return self.call("getUpdates", _http_timeout=poll + 15, offset=offset, timeout=poll,
                         allowed_updates=["message", "callback_query"])
