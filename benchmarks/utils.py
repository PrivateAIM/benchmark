import json
import tarfile
from typing import Any


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
