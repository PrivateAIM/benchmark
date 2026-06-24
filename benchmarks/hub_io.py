import os
import time
from typing import Any

import flame_hub
import httpx

from benchmarks.config import (
    CODE_BUCKET_TIMEOUT_SECONDS,
    HTTP_RETRY_BACKOFF_SECONDS,
    HTTP_RETRY_MAX_ATTEMPTS,
    PROJECT_NAME,
)
from benchmarks.utils import call_with_retries


def ensure_project(core_client: flame_hub.CoreClient) -> Any:
    print(f"[*] Ensuring project '{PROJECT_NAME}' exists...")
    projects = call_with_retries(core_client.get_projects)
    matching_projects = [project for project in projects if project.name.lower() == PROJECT_NAME.lower()]

    if len(matching_projects) == 0:
        project = call_with_retries(core_client.create_project, name=PROJECT_NAME)
        print(f"\n[!] New project '{PROJECT_NAME}' created.")
        print("[!] Please connect the MinIO Bucket by creating a Data Store for this project.")
        assert False, (
            f"Project {project.id} created but data store is not configured. "
            "Configure storage and rerun."
        )

    return matching_projects[0]


def ensure_analysis_bucket(core_client: flame_hub.CoreClient, analysis_id: str, bucket_type: str) -> Any:
    deadline = time.time() + CODE_BUCKET_TIMEOUT_SECONDS
    while True:
        buckets = call_with_retries(core_client.find_analysis_buckets, filter={"analysis_id": analysis_id})
        matching_buckets = [bucket for bucket in buckets if bucket.type == bucket_type]
        if len(matching_buckets) > 0:
            analysis_bucket = matching_buckets[0]
            assert (
                analysis_bucket.bucket_id is not None
            ), f"{bucket_type} bucket {analysis_bucket.id} has no storage bucket_id assigned"
            return analysis_bucket
        assert (
            time.time() < deadline
        ), f"{bucket_type} bucket not auto-created for analysis {analysis_id} within timeout"
        time.sleep(1.0)


def resolve_storage_bucket_id(analysis_bucket: Any) -> str:
    bucket_data = analysis_bucket.model_dump()
    if "external_id" in bucket_data and bucket_data["external_id"] is not None:
        return str(bucket_data["external_id"])
    return str(bucket_data["bucket_id"])


def upload_benchmark_code(
    core_client: flame_hub.CoreClient,
    storage_client: flame_hub.StorageClient,
    analysis_id: str,
    benchmark_path: str,
    script_name: str,
) -> None:
    code_bucket = ensure_analysis_bucket(core_client, analysis_id, "CODE")
    code_bucket_core_id = str(code_bucket.model_dump()["id"])
    storage_bucket_id = resolve_storage_bucket_id(code_bucket)

    with open(benchmark_path, "rb") as benchmark_file:
        script_bytes = benchmark_file.read()

    upload_response = call_with_retries(
        storage_client.upload_to_bucket,
        storage_bucket_id, {"file_name": script_name, "content": script_bytes},
    )
    assert len(upload_response) > 0, "Upload did not return file metadata."

    time.sleep(2)
    response = call_with_retries(
        core_client._client.get,
        f"analysis-bucket-files?filter[analysis_bucket_id]={code_bucket_core_id}",
    )
    bucket_file_payload = response.json()["data"]
    file_ids = [entry["id"] for entry in bucket_file_payload if entry["path"] == script_name]
    assert len(file_ids) == 1, f"Expected exactly one uploaded entrypoint file for {script_name}."

    call_with_retries(
        core_client.update_analysis_bucket_file,
        analysis_bucket_file_id=file_ids[0],
        is_entrypoint=True,
    )


def download_result_tar(
    core_client: flame_hub.CoreClient,
    storage_client: flame_hub.StorageClient,
    analysis_id: str,
    raw_output_dir: str,
) -> str:
    buckets = call_with_retries(core_client.find_analysis_buckets, filter={"analysis_id": analysis_id})
    result_buckets = [bucket for bucket in buckets if bucket.type == "RESULT"]
    assert len(result_buckets) > 0, f"No RESULT bucket found for analysis {analysis_id}"
    result_bucket = result_buckets[0]

    print(f"    [+] Downloading results from bucket {result_bucket.id}...")
    result_path = os.path.join(raw_output_dir, f"results_{analysis_id}.tar")

    # stream_bucket_tarball yields chunks over an open connection, so a mid-stream
    # drop can't be retried by re-issuing a single call like call_with_retries does.
    # Restart the whole download (truncating the partial file) on a transient error.
    for attempt in range(1, HTTP_RETRY_MAX_ATTEMPTS + 1):
        try:
            with open(result_path, "wb") as result_tar:
                for chunk in storage_client.stream_bucket_tarball(result_bucket.bucket_id):
                    result_tar.write(chunk)
            break
        except httpx.TransportError as exception:
            assert attempt < HTTP_RETRY_MAX_ATTEMPTS, (
                f"Exhausted {HTTP_RETRY_MAX_ATTEMPTS} retries downloading result tar: {exception}"
            )
            print(
                f"    [~] Transient network error downloading result tar "
                f"({type(exception).__name__}). Retrying download "
                f"({attempt}/{HTTP_RETRY_MAX_ATTEMPTS})..."
            )
            time.sleep(HTTP_RETRY_BACKOFF_SECONDS)

    print(f"    [+] Results saved to {result_path}")
    return result_path
