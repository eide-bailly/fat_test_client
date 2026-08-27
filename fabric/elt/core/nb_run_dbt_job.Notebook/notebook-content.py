# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   }
# META }

# PARAMETERS CELL ********************

data_build_tool_job_id = ""
workspace_id = ""
operation = "run"
select = ""
exclude = ""
full_refresh = False
fail_fast = False
threads = 1
selector_name = ""
empty_catalog = ""
run_source_freshness = ""
generate_docs = ""
engine = "Core"
poll_interval_seconds = 20
timeout_seconds = 3600

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

from datetime import datetime, timezone  # noqa: E402
from email.utils import parsedate_to_datetime  # noqa: E402
import functools  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from typing import Any  # noqa: E402
from urllib.parse import unquote, urlparse  # noqa: E402

import msal  # noqa: E402
import notebookutils  # noqa: E402
import requests  # noqa: E402

KEY_VAULT_URL = "https://{key_vault_name}.vault.azure.net/"
FABRIC_API_SCOPE = "https://api.fabric.microsoft.com/.default"
FABRIC_API_BASE_URL = "https://api.fabric.microsoft.com/v1"
HTTP_TIMEOUT_SECONDS = 60
CANCELLATION_WAIT_SECONDS = 300
SUPPORTED_OPERATIONS = {"build", "compile", "run", "seed", "snapshot", "test"}
SUCCESS_STATUSES = {"COMPLETED", "SUCCEEDED", "SUCCESS"}
FAILURE_STATUSES = {"CANCELED", "CANCELLED", "FAILED"}
ACTIVE_STATUSES = {
    "CANCELING",
    "CANCELLING",
    "INPROGRESS",
    "NOTSTARTED",
    "QUEUED",
    "RUNNING",
}


class JobLifecycleError(RuntimeError):
    """Fail the notebook with structured, non-secret job diagnostics."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        super().__init__(json.dumps(result, sort_keys=True, default=str))


def utc_timestamp() -> str:
    """Return a UTC timestamp suitable for the notebook result."""
    return datetime.now(timezone.utc).isoformat()


def require_non_empty_string(value: Any, name: str) -> str:
    """Validate an identifier or required execution setting."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")
    return value.strip()


def normalize_optional_string(value: Any, name: str) -> str | None:
    """Convert blank optional notebook parameters to JSON null."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null.")
    normalized = value.strip()
    return normalized or None


def require_positive_integer(value: Any, name: str) -> int:
    """Validate a positive integer notebook parameter."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer.")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer.") from exc
    if normalized <= 0 or (isinstance(value, float) and not value.is_integer()):
        raise ValueError(f"{name} must be a positive integer.")
    return normalized


def require_boolean(value: Any, name: str) -> bool:
    """Validate a boolean command parameter."""
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean.")
    return value


def validate_execution_inputs() -> tuple[str, str, dict[str, Any], int, int]:
    """Validate notebook parameters before any Fabric API request is issued."""
    normalized_workspace_id = require_non_empty_string(workspace_id, "workspace_id")
    normalized_job_id = require_non_empty_string(data_build_tool_job_id, "data_build_tool_job_id")
    normalized_operation = require_non_empty_string(operation, "operation").lower()
    if normalized_operation not in SUPPORTED_OPERATIONS:
        supported = ", ".join(sorted(SUPPORTED_OPERATIONS))
        raise ValueError(f"operation must be one of: {supported}.")

    normalized_engine = require_non_empty_string(engine, "engine")
    normalized_threads = require_positive_integer(threads, "threads")
    normalized_poll_interval = require_positive_integer(
        poll_interval_seconds, "poll_interval_seconds"
    )
    normalized_timeout = require_positive_integer(timeout_seconds, "timeout_seconds")
    arguments = {
        "select": normalize_optional_string(select, "select"),
        "exclude": normalize_optional_string(exclude, "exclude"),
        "fullRefresh": require_boolean(full_refresh, "full_refresh"),
        "failFast": require_boolean(fail_fast, "fail_fast"),
        "threads": normalized_threads,
        "selectorName": normalize_optional_string(selector_name, "selector_name"),
        "emptyCatalog": normalize_optional_string(empty_catalog, "empty_catalog"),
        "runSourceFreshness": normalize_optional_string(
            run_source_freshness, "run_source_freshness"
        ),
        "generateDocs": normalize_optional_string(generate_docs, "generate_docs"),
    }
    return (
        normalized_workspace_id,
        normalized_job_id,
        {
            "operation": normalized_operation,
            "arguments": arguments,
            "engine": normalized_engine,
        },
        normalized_poll_interval,
        normalized_timeout,
    )


def response_request_id(response: requests.Response) -> str | None:
    """Extract Fabric's correlation ID without exposing authorization data."""
    for header_name in ("x-ms-request-id", "request-id", "x-ms-correlation-request-id"):
        value = response.headers.get(header_name)
        if value:
            return value
    return None


def response_json(response: requests.Response) -> dict[str, Any]:
    """Return a JSON object response body, or an empty object."""
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def fabric_failure_metadata(
    payload: dict[str, Any], http_status_code: int | None = None
) -> dict[str, Any] | None:
    """Keep Fabric failure fields that are useful to callers and contain no credentials."""
    failure: dict[str, Any] = {}
    if http_status_code is not None:
        failure["http_status_code"] = http_status_code
    for key in (
        "code",
        "errorCode",
        "errorMessage",
        "failureReason",
        "message",
        "requestId",
        "relatedResource",
    ):
        if key in payload and payload[key] is not None:
            failure[key] = payload[key]
    return failure or None


def parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header expressed as seconds or an HTTP date."""
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


def job_instance_id_from_location(location: str | None) -> str:
    """Extract the job instance ID from the required Location response header."""
    if not location:
        raise ValueError("Fabric accepted the job request without a Location header.")
    path_segments = [segment for segment in urlparse(location).path.split("/") if segment]
    if not path_segments or "instances" not in path_segments:
        raise ValueError("Fabric returned an invalid job instance Location header.")
    instance_id = unquote(path_segments[-1])
    if not instance_id:
        raise ValueError("Fabric returned an invalid job instance ID in the Location header.")
    return instance_id


def job_status(payload: dict[str, Any]) -> str | None:
    """Get Fabric's job status from the instance representation."""
    status = payload.get("status")
    return status if isinstance(status, str) and status else None


def classify_status(status: str | None) -> str:
    """Classify known Fabric job states without treating unknown states as success."""
    normalized = status.upper() if status else ""
    if normalized in SUCCESS_STATUSES:
        return "success"
    if normalized in FAILURE_STATUSES:
        return "failure"
    if normalized in ACTIVE_STATUSES:
        return "active"
    return "unknown"


def result_timestamps(payload: dict[str, Any], request_started_at: str) -> dict[str, str]:
    """Return request and Fabric job timestamps when Fabric supplied them."""
    timestamps = {"request_started_at": request_started_at}
    for result_key, payload_key in (
        ("created_at", "createdTimeUtc"),
        ("started_at", "startTimeUtc"),
        ("ended_at", "endTimeUtc"),
    ):
        value = payload.get(payload_key)
        if isinstance(value, str) and value:
            timestamps[result_key] = value
    return timestamps


def build_result(
    *,
    request_started_at: str,
    started_monotonic: float,
    requested_execution_data: dict[str, Any],
    status: str,
    workspace: str,
    item_id: str,
    job_instance_id: str | None = None,
    request_id: str | None = None,
    job_payload: dict[str, Any] | None = None,
    failure: dict[str, Any] | None = None,
    cancellation_attempted: bool = False,
) -> dict[str, Any]:
    """Build a serializable notebook result containing only non-secret request data."""
    payload = job_payload or {}
    result: dict[str, Any] = {
        "status": status,
        "workspace_id": workspace,
        "data_build_tool_job_id": item_id,
        "job_instance_id": job_instance_id,
        "request_id": request_id,
        "elapsed_seconds": round(time.monotonic() - started_monotonic, 3),
        "timestamps": result_timestamps(payload, request_started_at),
        "requested_command": requested_execution_data,
        "cancellation_attempted": cancellation_attempted,
    }
    fabric_status = job_status(payload)
    if fabric_status:
        result["fabric_job_status"] = fabric_status
    if failure:
        result["fabric_failure"] = failure
    return result


def acquire_access_token() -> str:
    """Acquire a Fabric API token without logging credential material."""
    tenant_id = notebookutils.credentials.getSecret(KEY_VAULT_URL, "SERVICE-PRINCIPAL-TENANT-ID")
    client_id = notebookutils.credentials.getSecret(KEY_VAULT_URL, "SERVICE-PRINCIPAL-CLIENT-ID")
    client_secret = notebookutils.credentials.getSecret(KEY_VAULT_URL, "SERVICE-PRINCIPAL-SECRET")
    msal_app = msal.ConfidentialClientApplication(
        client_id=client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        client_credential=client_secret,
    )
    token_result = msal_app.acquire_token_for_client(scopes=[FABRIC_API_SCOPE])
    access_token = token_result.get("access_token")
    if not access_token:
        raise RuntimeError("Unable to acquire a Fabric API access token.")
    return access_token


def poll_job_instance(
    *,
    headers: dict[str, str],
    instance_url: str,
    request_started_at: str,
    started_monotonic: float,
    requested_execution_data: dict[str, Any],
    workspace: str,
    item_id: str,
    job_instance_id: str,
    submission_request_id: str | None,
    poll_interval: int,
    timeout: int,
    initial_delay: float | None,
) -> dict[str, Any]:
    """Poll one submitted Fabric job and cancel it if its execution window expires."""
    deadline = started_monotonic + timeout
    cancellation_deadline: float | None = None
    cancellation_attempted = False
    cancellation_failure: dict[str, Any] | None = None

    build_partial_result = functools.partial(
        build_result,
        request_started_at=request_started_at,
        started_monotonic=started_monotonic,
        requested_execution_data=requested_execution_data,
        workspace=workspace,
        item_id=item_id,
        job_instance_id=job_instance_id,
    )

    def raise_lifecycle_error(
        *,
        status: str,
        request_id: str | None = None,
        job_payload: dict[str, Any] | None = None,
        failure: dict[str, Any] | None = None,
        cancellation_attempted: bool = False,
        from_exc: BaseException | None = None,
    ) -> None:
        """Build and raise a JobLifecycleError from this poll's lifecycle inputs."""
        result = build_partial_result(
            status=status,
            request_id=request_id,
            job_payload=job_payload,
            failure=failure,
            cancellation_attempted=cancellation_attempted,
        )
        if from_exc is not None:
            raise JobLifecycleError(result) from from_exc
        raise JobLifecycleError(result)

    def build_success_result(*, payload: dict[str, Any], request_id: str | None) -> dict[str, Any]:
        """Build the terminal success result for a completed poll."""
        return build_partial_result(
            status="Succeeded",
            request_id=request_id,
            job_payload=payload,
        )

    def poll_once() -> tuple[dict[str, Any], str | None]:
        try:
            response = requests.get(instance_url, headers=headers, timeout=HTTP_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise_lifecycle_error(
                status="PollingFailed",
                request_id=submission_request_id,
                cancellation_attempted=cancellation_attempted,
                failure={"message": f"Fabric job polling transport failure: {exc}"},
                from_exc=exc,
            )

        payload = response_json(response)
        if not response.ok:
            raise_lifecycle_error(
                status="PollingFailed",
                request_id=response_request_id(response) or submission_request_id,
                job_payload=payload,
                cancellation_attempted=cancellation_attempted,
                failure=fabric_failure_metadata(payload, response.status_code),
            )
        return payload, response_request_id(response)

    while True:
        now = time.monotonic()
        if not cancellation_attempted and now >= deadline:
            payload, poll_request_id = poll_once()
            state = classify_status(job_status(payload))
            if state == "success":
                return build_success_result(
                    payload=payload, request_id=poll_request_id or submission_request_id
                )
            if state == "failure":
                raise_lifecycle_error(
                    status="Failed",
                    request_id=poll_request_id or submission_request_id,
                    job_payload=payload,
                    failure=fabric_failure_metadata(payload),
                )
            if state == "unknown":
                raise_lifecycle_error(
                    status="UnknownTerminalStatus",
                    request_id=poll_request_id or submission_request_id,
                    job_payload=payload,
                    failure=fabric_failure_metadata(payload),
                )

            cancellation_attempted = True
            cancellation_deadline = time.monotonic() + CANCELLATION_WAIT_SECONDS
            try:
                cancel_response = requests.post(
                    f"{instance_url}/cancel", headers=headers, timeout=HTTP_TIMEOUT_SECONDS
                )
                cancel_payload = response_json(cancel_response)
                if not cancel_response.ok:
                    cancellation_failure = fabric_failure_metadata(
                        cancel_payload, cancel_response.status_code
                    ) or {"message": "Fabric rejected the cancellation request."}
            except requests.RequestException as exc:
                cancellation_failure = {"message": f"Fabric cancellation transport failure: {exc}"}
            initial_delay = 0
            continue

        if (
            cancellation_attempted
            and cancellation_deadline is not None
            and now >= cancellation_deadline
        ):
            raise_lifecycle_error(
                status="CancellationTimedOut",
                request_id=submission_request_id,
                cancellation_attempted=True,
                failure=cancellation_failure
                or {"message": "Job did not reach a terminal state after cancellation."},
            )

        delay = poll_interval if initial_delay is None else initial_delay
        initial_delay = None
        next_deadline = cancellation_deadline if cancellation_attempted else deadline
        time.sleep(min(delay, max(0.0, next_deadline - time.monotonic())))
        payload, poll_request_id = poll_once()
        state = classify_status(job_status(payload))
        if state == "success" and not cancellation_attempted:
            return build_success_result(
                payload=payload, request_id=poll_request_id or submission_request_id
            )
        if state == "success":
            raise_lifecycle_error(
                status="TimedOut",
                request_id=poll_request_id or submission_request_id,
                job_payload=payload,
                cancellation_attempted=True,
                failure=cancellation_failure
                or {"message": "Job completed after the notebook execution timeout."},
            )
        if state == "failure":
            raise_lifecycle_error(
                status="Failed",
                request_id=poll_request_id or submission_request_id,
                job_payload=payload,
                cancellation_attempted=cancellation_attempted,
                failure=fabric_failure_metadata(payload) or cancellation_failure,
            )
        if state == "unknown":
            raise_lifecycle_error(
                status="UnknownTerminalStatus",
                request_id=poll_request_id or submission_request_id,
                job_payload=payload,
                cancellation_attempted=cancellation_attempted,
                failure=fabric_failure_metadata(payload) or cancellation_failure,
            )


def run_job_lifecycle() -> dict[str, Any]:
    """Submit exactly one Fabric job instance and wait for its terminal state."""
    workspace, item_id, execution_data, poll_interval, timeout = validate_execution_inputs()
    request_started_at = utc_timestamp()
    started_monotonic = time.monotonic()
    request_url = (
        f"{FABRIC_API_BASE_URL}/workspaces/{workspace}/items/{item_id}/jobs/Execute/instances"
    )
    headers = {
        "Authorization": f"Bearer {acquire_access_token()}",
        "Content-Type": "application/json",
    }

    build_partial_result = functools.partial(
        build_result,
        request_started_at=request_started_at,
        started_monotonic=started_monotonic,
        requested_execution_data=execution_data,
        workspace=workspace,
        item_id=item_id,
    )

    def raise_submission_error(
        *,
        status: str,
        request_id: str | None = None,
        job_payload: dict[str, Any] | None = None,
        failure: dict[str, Any] | None = None,
        from_exc: BaseException | None = None,
    ) -> None:
        """Build and raise a JobLifecycleError from submission-phase lifecycle inputs."""
        result = build_partial_result(
            status=status,
            request_id=request_id,
            job_payload=job_payload,
            failure=failure,
        )
        if from_exc is not None:
            raise JobLifecycleError(result) from from_exc
        raise JobLifecycleError(result)

    try:
        submission_response = requests.post(
            request_url,
            headers=headers,
            json={"executionData": execution_data},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise_submission_error(
            status="SubmissionTransportFailed",
            failure={
                "message": (
                    "Fabric job submission transport failure; the request was not retried because "
                    f"the job creation outcome is ambiguous: {exc}"
                )
            },
            from_exc=exc,
        )

    submission_payload = response_json(submission_response)
    submission_request_id = response_request_id(submission_response)
    if not submission_response.ok:
        raise_submission_error(
            status="SubmissionFailed",
            request_id=submission_request_id,
            job_payload=submission_payload,
            failure=fabric_failure_metadata(submission_payload, submission_response.status_code),
        )

    try:
        job_instance_id = job_instance_id_from_location(submission_response.headers.get("Location"))
    except ValueError as exc:
        raise_submission_error(
            status="SubmissionFailed",
            request_id=submission_request_id,
            job_payload=submission_payload,
            failure={"message": str(exc)},
            from_exc=exc,
        )

    initial_delay = parse_retry_after(submission_response.headers.get("Retry-After"))
    instance_url = (
        f"{FABRIC_API_BASE_URL}/workspaces/{workspace}/items/{item_id}/jobs/instances/{job_instance_id}"
    )
    return poll_job_instance(
        headers=headers,
        instance_url=instance_url,
        request_started_at=request_started_at,
        started_monotonic=started_monotonic,
        requested_execution_data=execution_data,
        workspace=workspace,
        item_id=item_id,
        job_instance_id=job_instance_id,
        submission_request_id=submission_request_id,
        poll_interval=poll_interval,
        timeout=timeout,
        initial_delay=initial_delay,
    )


result = run_job_lifecycle()
notebookutils.notebook.exit(json.dumps(result, sort_keys=True))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
