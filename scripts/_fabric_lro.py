"""
Shared Fabric REST long-running-operation (LRO) client.

Self-contained helper module (stdlib + requests + azure-identity only) used
by deploy_fabric.py and any other script that needs to call the Fabric REST
API safely: SSRF-hardened Location/Operation-Location header validation,
Retry-After parsing, bounded polling deadlines, and redaction of secrets
before logging response bodies.

Not a script itself — import it from sibling scripts in this directory,
e.g. `import _fabric_lro` or `from _fabric_lro import _get_json`.
"""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import logging
import time
from typing import Any
from urllib.parse import quote, unquote, urlparse

from azure.identity import ClientSecretCredential
import requests

_FABRIC_API = "https://api.fabric.microsoft.com/v1"
_FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
_HTTP_TIMEOUT_SECONDS = 30
_LRO_TIMEOUT_SECONDS = 600
_LRO_POLL_INTERVAL_SECONDS = 5
_LRO_SUCCESS_STATES = {"SUCCEEDED"}
_LRO_FAILURE_STATES = {"CANCELED", "CANCELLED", "FAILED"}
_LRO_ACTIVE_STATES = {"INPROGRESS", "NOTSTARTED", "QUEUED", "RUNNING", "UNDEFINED"}
_SENSITIVE_LOG_KEYS = {"authorization", "credential", "password", "secret", "token"}

logger = logging.getLogger(__name__)


def _fabric_headers(credential: ClientSecretCredential) -> dict[str, str]:
    """Return authenticated headers without ever logging the bearer token."""
    access_token = credential.get_token(_FABRIC_SCOPE).token
    return {"Authorization": "Bearer " + access_token, "Content-Type": "application/json"}


def _redact_for_logging(value: Any) -> Any:
    """Remove sensitive values from Fabric response data before logging it."""
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if any(sensitive in key.lower() for sensitive in _SENSITIVE_LOG_KEYS)
                else _redact_for_logging(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_for_logging(item) for item in value]
    return value


def _response_payload(response: requests.Response, context: str) -> dict[str, Any]:
    """Return a required JSON-object response body or fail with useful diagnostics."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{context} returned a non-JSON response.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{context} returned a JSON response that is not an object.")
    return payload


def _require_success(response: requests.Response, context: str) -> dict[str, Any] | None:
    """Reject every non-success response while logging safe Fabric error details."""
    if 200 <= response.status_code < 300:
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    try:
        details = _response_payload(response, context)
    except RuntimeError:
        details = None
    if details:
        logger.error(
            "%s failed with HTTP %s: %s",
            context,
            response.status_code,
            json.dumps(_redact_for_logging(details), sort_keys=True),
        )
    else:
        logger.error(
            "%s failed with HTTP %s and no JSON error details.", context, response.status_code
        )
    raise RuntimeError(f"{context} failed with HTTP {response.status_code}.")


def _get_json(url: str, headers: dict[str, str], context: str) -> dict[str, Any]:
    response = requests.get(url, headers=headers, timeout=_HTTP_TIMEOUT_SECONDS)
    _require_success(response, context)
    return _response_payload(response, context)


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After delay expressed in seconds or as an HTTP date."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())


def _operation_location(response: requests.Response) -> tuple[str, str]:
    """Validate and return a trusted Fabric LRO URL and its operation ID."""
    operation_url = response.headers.get("Location") or response.headers.get("Operation-Location")
    if not isinstance(operation_url, str) or not operation_url.strip():
        raise RuntimeError("Fabric LRO response did not include a Location header.")
    if any(character.isspace() for character in operation_url):
        raise RuntimeError("Fabric LRO response included a malformed operation Location header.")

    try:
        parsed_url = urlparse(operation_url)
        location_port = parsed_url.port
        location_host = parsed_url.hostname
    except ValueError as exc:
        raise RuntimeError(
            "Fabric LRO response included an invalid or off-host operation Location header."
        ) from exc

    fabric_api = urlparse(_FABRIC_API)
    operation_path_prefix = f"{fabric_api.path.rstrip('/')}/operations/"
    is_relative_location = not parsed_url.scheme and not parsed_url.netloc
    if is_relative_location:
        if not operation_url.startswith("/"):
            raise RuntimeError(
                "Fabric LRO response included a malformed operation Location header."
            )
    elif (
        parsed_url.scheme != fabric_api.scheme
        or not parsed_url.netloc
        or location_host != fabric_api.hostname
        or location_port not in {None, 443}
        or parsed_url.username is not None
        or parsed_url.password is not None
    ):
        raise RuntimeError(
            "Fabric LRO response included an invalid or off-host operation Location header."
        )

    if (
        parsed_url.params
        or parsed_url.query
        or parsed_url.fragment
        or not parsed_url.path.startswith(operation_path_prefix)
    ):
        raise RuntimeError("Fabric LRO response included a malformed operation Location header.")

    operation_id = parsed_url.path.removeprefix(operation_path_prefix)
    if not operation_id or "/" in operation_id or "/" in unquote(operation_id):
        raise RuntimeError("Fabric LRO response Location header did not include an operation ID.")
    return f"{_FABRIC_API.rstrip('/')}/operations/{operation_id}", operation_id


def _operation_id_from_header(response: requests.Response) -> str | None:
    """Return a valid opaque Fabric operation ID from the response header, if present."""
    operation_id = response.headers.get("x-ms-operation-id")
    if (
        not isinstance(operation_id, str)
        or not operation_id
        or operation_id != operation_id.strip()
    ):
        return None

    decoded_operation_id = unquote(operation_id)
    if (
        not decoded_operation_id
        or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in decoded_operation_id
        )
        or "/" in decoded_operation_id
        or "\\" in decoded_operation_id
    ):
        return None
    return operation_id


def _operation_url_from_header(response: requests.Response) -> tuple[str, str] | None:
    """Build a trusted Fabric operation URL from a valid response operation ID."""
    operation_id = _operation_id_from_header(response)
    if operation_id is None:
        return None
    return f"{_FABRIC_API.rstrip('/')}/operations/{quote(operation_id, safe='')}", operation_id


def _sleep_with_timeout(delay: float, deadline: float, reason: str) -> None:
    """Sleep only while the bounded LRO timeout permits it."""
    if delay <= 0:
        return
    if time.monotonic() + delay > deadline:
        raise TimeoutError(f"Fabric Git update timed out before {reason}.")
    logger.info("Waiting %.1f seconds before %s.", delay, reason)
    time.sleep(delay)


def _retry_rate_limited_response(
    response: requests.Response, deadline: float, context: str
) -> bool:
    """Sleep for a bounded Retry-After interval when Fabric rate limits a request."""
    if response.status_code != 429:
        return False
    delay = _parse_retry_after(response.headers.get("Retry-After"))
    _sleep_with_timeout(
        _LRO_POLL_INTERVAL_SECONDS if delay is None else delay,
        deadline,
        f"retrying {context} after HTTP 429",
    )
    return True


def _required_string(payload: dict[str, Any], key: str, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{context} did not include a non-empty {key}.")
    return value
