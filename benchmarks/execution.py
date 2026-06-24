import os
import time
import uuid
from typing import Any

import flame_hub

from benchmarks.config import BENCHMARK_TIMEOUT_SECONDS, POLL_INTERVAL_SECONDS
from benchmarks.hub_io import download_result_tar, upload_benchmark_code
from benchmarks.utils import (
    benchmark_slug,
    call_with_retries,
    derive_internal_compute_seconds,
    extract_result_payload,
    is_successful_payload,
)

BENCHMARKS_DIR = os.path.dirname(__file__)


def _phase_duration(start_time: float | None, end_time: float | None, phase_name: str) -> float:
    if start_time is None or end_time is None:
        return 0.0
    assert end_time >= start_time, f"Negative duration for phase: {phase_name}"
    return end_time - start_time


def _update_phase_timestamps(
    analysis: Any, phase_timestamps: dict[str, float | None], now: float
) -> None:
    if analysis.build_status == "started" and phase_timestamps["build_started_at"] is None:
        phase_timestamps["build_started_at"] = now

    if analysis.build_status == "executed" and phase_timestamps["build_executed_at"] is None:
        phase_timestamps["build_executed_at"] = now

    if (
        analysis.distribution_status is not None
        and phase_timestamps["distribution_started_at"] is None
    ):
        phase_timestamps["distribution_started_at"] = now

    if (
        analysis.distribution_status == "executed"
        and phase_timestamps["distribution_executed_at"] is None
    ):
        phase_timestamps["distribution_executed_at"] = now

    if (
        analysis.execution_status in ["starting", "executing", "executed", "finished"]
        and phase_timestamps["execution_started_at"] is None
    ):
        phase_timestamps["execution_started_at"] = now

    if (
        analysis.execution_status in ["executed", "finished"]
        and phase_timestamps["execution_executed_at"] is None
    ):
        phase_timestamps["execution_executed_at"] = now


def _derive_runtime_metrics(
    payload: dict[str, Any],
    phase_timestamps: dict[str, float | None],
    total_duration_seconds: float,
) -> tuple[dict[str, float], float]:
    build_phase_seconds = _phase_duration(
        phase_timestamps["build_started_at"],
        phase_timestamps["build_executed_at"],
        "build",
    )
    distribution_phase_seconds = _phase_duration(
        phase_timestamps["distribution_started_at"],
        phase_timestamps["distribution_executed_at"],
        "distribution",
    )
    execution_phase_seconds = _phase_duration(
        phase_timestamps["execution_started_at"],
        phase_timestamps["execution_executed_at"],
        "execution",
    )

    platform_runtime_seconds = distribution_phase_seconds + execution_phase_seconds
    internal_compute_seconds = derive_internal_compute_seconds(payload)
    orchestration_overhead_seconds = max(platform_runtime_seconds - internal_compute_seconds, 0.0)
    end_to_end_overhead_seconds = max(total_duration_seconds - internal_compute_seconds, 0.0)

    return (
        {
            "total_duration_seconds": total_duration_seconds,
            "build_phase_seconds": build_phase_seconds,
            "distribution_phase_seconds": distribution_phase_seconds,
            "execution_phase_seconds": execution_phase_seconds,
            "platform_runtime_seconds": platform_runtime_seconds,
            "internal_compute_seconds": internal_compute_seconds,
            "orchestration_overhead_seconds": orchestration_overhead_seconds,
            "end_to_end_overhead_seconds": end_to_end_overhead_seconds,
        },
        orchestration_overhead_seconds,
    )


def execute_benchmark(
    core_client: flame_hub.CoreClient,
    storage_client: flame_hub.StorageClient,
    project_id: str,
    node_ids: list[str],
    node_labels: dict[str, str],
    combined_node_ids: list[str],
    script_name: str,
    raw_output_dir: str,
) -> dict[str, Any]:
    benchmark_path = os.path.join(BENCHMARKS_DIR, script_name)
    assert os.path.exists(benchmark_path), f"Benchmark script {script_name} not found."

    print(f"\n[>>>] RUNNING BENCHMARK: {script_name}")

    analysis_name = f"Benchmark-{uuid.uuid4().hex[:8]}"
    images = call_with_retries(core_client.get_master_images)
    assert len(images) > 0, "No master images found."
    master_image_id = images[0].id

    analysis = call_with_retries(
        core_client.create_analysis,
        name=analysis_name, project_id=project_id, master_image_id=master_image_id,
    )
    print(f"    [+] Analysis Created: {analysis.id}")

    for node_id in node_ids:
        call_with_retries(core_client.create_analysis_node, analysis_id=analysis.id, node_id=node_id)
    print("    [+] Nodes Added")

    time.sleep(1)
    upload_benchmark_code(
        core_client=core_client,
        storage_client=storage_client,
        analysis_id=str(analysis.id),
        benchmark_path=benchmark_path,
        script_name=script_name,
    )

    time.sleep(2)
    analysis = call_with_retries(core_client.get_analysis, analysis.id)

    call_with_retries(core_client.send_analysis_command, analysis.id, "configurationLock")
    call_with_retries(core_client.send_analysis_command, analysis.id, "buildStart")

    print("    [*] Polling for completion...")
    start_exec_time = time.time()
    phase_timestamps: dict[str, float | None] = {
        "build_started_at": start_exec_time,
        "build_executed_at": None,
        "distribution_started_at": None,
        "distribution_executed_at": None,
        "execution_started_at": None,
        "execution_executed_at": None,
    }
    distribution_started = False

    while time.time() - start_exec_time < BENCHMARK_TIMEOUT_SECONDS:
        now = time.time()
        analysis = call_with_retries(core_client.get_analysis, analysis.id)
        _update_phase_timestamps(analysis=analysis, phase_timestamps=phase_timestamps, now=now)
        print(
            f"        Build: {analysis.build_status} | Dist: {analysis.distribution_status} | Exec: {analysis.execution_status}"
        )

        assert analysis.build_status != "failed", "Analysis Build Failed."
        assert analysis.distribution_status != "failed", "Analysis Distribution Failed."
        assert analysis.execution_status != "failed", "Analysis Run Failed."

        if analysis.build_status == "executed":
            if not distribution_started and analysis.distribution_status is None:
                call_with_retries(core_client.send_analysis_command, analysis.id, "distributionStart")
                distribution_started = True

            if analysis.distribution_status == "executed":
                if analysis.execution_status in ["executed", "finished"]:
                    phase_timestamps["execution_executed_at"] = time.time()
                    print("    [+] Analysis Finished Successfully!")
                    break

        time.sleep(POLL_INTERVAL_SECONDS)
    else:
        assert False, "Timeout waiting for analysis."

    duration = time.time() - start_exec_time
    local_result_path = download_result_tar(
        core_client=core_client,
        storage_client=storage_client,
        analysis_id=str(analysis.id),
        raw_output_dir=raw_output_dir,
    )

    payload = extract_result_payload(local_result_path)
    assert is_successful_payload(payload), f"Benchmark payload reports failure: {script_name}"
    timings, federation_tax = _derive_runtime_metrics(
        payload=payload,
        phase_timestamps=phase_timestamps,
        total_duration_seconds=duration,
    )

    return {
        "script": script_name,
        "benchmark": benchmark_slug(script_name),
        "status": "Success",
        "result_file": local_result_path,
        "analysis_id": str(analysis.id),
        "node_count": len(node_ids),
        "node_labels": node_labels,
        "combined_node_ids": combined_node_ids,
        "timings": timings,
        "federation_tax": federation_tax,
        "benchmark_metrics": payload,
    }
