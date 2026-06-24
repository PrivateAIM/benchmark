import json
import tarfile
import time
from typing import Any, Callable

import flame_hub
import httpx

from benchmarks.config import HTTP_RETRY_BACKOFF_SECONDS, HTTP_RETRY_MAX_ATTEMPTS
from benchmarks.credentials import AUTH_URL, CORE_URL, PASSWORD, STORAGE_URL, USERNAME


def call_with_retries(
    func: Callable[..., Any],
    *args: Any,
    max_attempts: int = HTTP_RETRY_MAX_ATTEMPTS,
    backoff_seconds: float = HTTP_RETRY_BACKOFF_SECONDS,
    **kwargs: Any,
) -> Any:
    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)
        except httpx.TransportError as exception:
            assert attempt < max_attempts, (
                f"Exhausted {max_attempts} retries on transient network error calling "
                f"{func.__qualname__}: {exception}"
            )
            print(
                f"    [~] Transient network error in {func.__qualname__} "
                f"({type(exception).__name__}). Retrying in {backoff_seconds:.0f}s "
                f"({attempt}/{max_attempts})..."
            )
            time.sleep(backoff_seconds)


def authenticate_clients() -> tuple[flame_hub.CoreClient, flame_hub.StorageClient]:
    print(f"[*] Authenticating with {AUTH_URL}...")
    auth = flame_hub.auth.PasswordAuth(username=USERNAME, password=PASSWORD, base_url=AUTH_URL)
    core_client = flame_hub.CoreClient(base_url=CORE_URL, auth=auth)
    storage_client = flame_hub.StorageClient(base_url=STORAGE_URL, auth=auth)
    return core_client, storage_client


def extract_result_payload(result_tar_path: str) -> dict[str, Any]:
    assert result_tar_path.endswith(".tar"), f"Expected .tar result file, got: {result_tar_path}"

    with tarfile.open(result_tar_path, "r:*") as archive:
        file_members = [member for member in archive.getmembers() if member.isfile()]
        assert len(file_members) > 0, f"No result files found in archive: {result_tar_path}"

        json_candidate = file_members[0]
        payload_file = archive.extractfile(json_candidate)
        assert payload_file is not None, f"Failed to extract payload file from: {result_tar_path}"

        raw_content = payload_file.read().decode("utf-8")
        payload = json.loads(raw_content)

    assert isinstance(payload, dict), "Result payload must be a JSON object."
    return payload


def average(values: list[float]) -> float:
    assert len(values) > 0, "Cannot average an empty list."
    return sum(values) / len(values)


def derive_internal_compute_seconds(payload: dict[str, Any]) -> float:
    if "total_critical_compute_time_seconds" in payload:
        return float(payload["total_critical_compute_time_seconds"])

    return 0.0


def benchmark_slug(script_name: str) -> str:
    base_name = script_name.replace(".py", "")
    parts = base_name.split("_", 1)
    assert len(parts) == 2, f"Benchmark file must start with NN_: {script_name}"
    return parts[1]


def is_successful_payload(payload: dict[str, Any]) -> bool:
    assert "overall_success" in payload, "Result payload missing 'overall_success'."
    return bool(payload["overall_success"])
