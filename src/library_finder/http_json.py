"""Minimal JSON-over-HTTP helper for the local model services."""
import json
import urllib.error
import urllib.request


class ServiceUnreachable(OSError): pass


class ServiceError(RuntimeError):
    def __init__(self, status, detail):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status


def request_json(method, url, payload=None, *, token=None, timeout=120):
    """Send one JSON request; raise ServiceUnreachable or ServiceError, return the decoded body."""
    headers = {"Content-Type": "application/json"}
    if token: headers["X-Internal-Token"] = token
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try: detail = json.loads(error.read().decode("utf-8")).get("detail", "")
        except (ValueError, AttributeError): detail = ""
        if isinstance(detail, dict): detail = detail.get("message", "")
        raise ServiceError(error.code, str(detail)[:200]) from error
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        raise ServiceUnreachable(str(getattr(error, "reason", error))) from error
