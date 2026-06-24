import os
import time
from typing import Any

from benchmarks.config import (
    RAW_OUTPUT_DIR_NAME,
    STORAGE_BENCH_FILES_PER_SIZE,
    STORAGE_BENCH_SIZES_BYTES,
)
from benchmarks.hub_io import ensure_analysis_bucket, ensure_project, resolve_storage_bucket_id
from benchmarks.summaries import runner_side_result, write_summary
from benchmarks.utils import authenticate_clients

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))


def _run_storage_io(core_client: Any, storage_client: Any) -> dict[str, Any]:
    print(f"[>>>] RUNNING BENCHMARK: storage_benchmark.py")

    project = ensure_project(core_client)
    images = core_client.get_master_images()
    assert len(images) > 0, "No master images found."
    analysis = core_client.create_analysis(
        name="storage-io-scratch", project_id=str(project.id), master_image_id=images[0].id
    )
    print(f"    [+] Created scratch analysis {analysis.id}")

    temp_bucket = ensure_analysis_bucket(core_client, str(analysis.id), "TEMP")
    bucket_id = resolve_storage_bucket_id(temp_bucket)
    print(f"    [+] Using auto-provisioned TEMP bucket {bucket_id}")

    write_seconds_by_size: dict[int, list[float]] = {}
    read_seconds_by_size: dict[int, list[float]] = {}

    for size_bytes in STORAGE_BENCH_SIZES_BYTES:
        write_seconds_by_size[size_bytes] = []
        read_seconds_by_size[size_bytes] = []

        for file_index in range(STORAGE_BENCH_FILES_PER_SIZE):
            file_content = os.urandom(size_bytes)
            file_name = f"{size_bytes}_{file_index}.bin"

            write_start = time.perf_counter()
            uploaded_files = storage_client.upload_to_bucket(
                bucket_id, {"file_name": file_name, "content": file_content}
            )
            write_seconds_by_size[size_bytes].append(time.perf_counter() - write_start)
            assert len(uploaded_files) == 1, f"Expected one uploaded file for {file_name}."

            bucket_file_id = uploaded_files[0].id
            read_start = time.perf_counter()
            downloaded_bytes = 0
            for chunk in storage_client.stream_bucket_file(bucket_file_id):
                downloaded_bytes += len(chunk)
            read_seconds_by_size[size_bytes].append(time.perf_counter() - read_start)
            assert downloaded_bytes == size_bytes, (
                f"Downloaded {downloaded_bytes} bytes, expected {size_bytes} for {file_name}."
            )

        print(f"    [+] size={size_bytes} bytes: {STORAGE_BENCH_FILES_PER_SIZE} files written and read")

    core_client.delete_analysis(analysis.id)
    print(f"    [+] Deleted scratch analysis {analysis.id} (cascades TEMP bucket cleanup)")

    write_seconds_by_size_str = {str(size): values for size, values in write_seconds_by_size.items()}
    read_seconds_by_size_str = {str(size): values for size, values in read_seconds_by_size.items()}

    all_write_seconds = [value for values in write_seconds_by_size.values() for value in values]
    all_read_seconds = [value for values in read_seconds_by_size.values() for value in values]

    return {
        "overall_success": True,
        "sizes_bytes": STORAGE_BENCH_SIZES_BYTES,
        "files_per_size": STORAGE_BENCH_FILES_PER_SIZE,
        "write_seconds_by_size": write_seconds_by_size_str,
        "read_seconds_by_size": read_seconds_by_size_str,
        "average_write_seconds": sum(all_write_seconds) / len(all_write_seconds),
        "average_read_seconds": sum(all_read_seconds) / len(all_read_seconds),
        "timestamp": time.time(),
    }


def run_storage_benchmark() -> str:
    raw_output_dir = os.path.join(PROJECT_ROOT, RAW_OUTPUT_DIR_NAME)

    core_client, storage_client = authenticate_clients()
    started_at = time.time()
    payload = _run_storage_io(core_client, storage_client)
    total_duration_seconds = time.time() - started_at

    result = runner_side_result(
        benchmark="storage_io",
        script="storage_benchmark.py",
        metrics=payload,
        total_duration_seconds=total_duration_seconds,
    )
    return write_summary([result], raw_output_dir, prefix="storage_summary")
