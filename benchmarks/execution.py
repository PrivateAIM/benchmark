import os
import time
import uuid
import runpy
import json
from pathlib import Path
from typing import Any

import flame_hub

from benchmarks.config import BENCHMARK_TIMEOUT_SECONDS, POLL_INTERVAL_SECONDS
from benchmarks.hub_io import download_result_tar, resolve_master_image_id, upload_benchmark_code
from benchmarks.measurements import script_digest
from benchmarks.utils import (
    benchmark_slug,
    call_with_retries,
    derive_internal_compute_seconds,
    extract_result_payload,
    is_successful_payload,
)

BENCHMARKS_DIR = os.path.dirname(__file__)


def _phase_duration(start_time: float | None, end_time: float | None, phase_name: str) -> float | None:
    if start_time is None or end_time is None:
        return None
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
        analysis.distribution_status in ["starting", "started", "executing"]
        and phase_timestamps["distribution_started_at"] is None
    ):
        phase_timestamps["distribution_started_at"] = now

    if (
        analysis.distribution_status == "executed"
        and phase_timestamps["distribution_executed_at"] is None
    ):
        phase_timestamps["distribution_executed_at"] = now

    if (
        analysis.execution_status in ["starting", "started", "executing"]
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
) -> tuple[dict, float | None]:
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

    platform_runtime_seconds = (
        distribution_phase_seconds + execution_phase_seconds
        if distribution_phase_seconds is not None and execution_phase_seconds is not None else None
    )
    internal_compute_seconds = derive_internal_compute_seconds(payload)
    compute_known = "total_critical_compute_time_seconds" in payload
    orchestration_overhead_seconds = (
        max(platform_runtime_seconds - internal_compute_seconds, 0.0)
        if platform_runtime_seconds is not None and compute_known else None
    )
    end_to_end_overhead_seconds = (
        max(total_duration_seconds - internal_compute_seconds, 0.0) if compute_known else None
    )
    steady_state_seconds = sum(payload["round_interval_seconds"]) if "round_interval_seconds" in payload else None
    steady_compute = payload["steady_state_critical_compute_seconds"] if "steady_state_critical_compute_seconds" in payload else None

    return (
        {
            "total_duration_seconds": total_duration_seconds,
            "build_phase_seconds": build_phase_seconds,
            "distribution_phase_seconds": distribution_phase_seconds,
            "execution_phase_seconds": execution_phase_seconds,
            "platform_runtime_seconds": platform_runtime_seconds,
            "internal_compute_seconds": internal_compute_seconds if compute_known else None,
            "steady_state_seconds": steady_state_seconds,
            "startup_and_completion_seconds": (
                execution_phase_seconds - steady_state_seconds
                if execution_phase_seconds is not None and steady_state_seconds is not None
                and execution_phase_seconds >= steady_state_seconds else None
            ),
            "steady_state_overhead_seconds": (
                max(steady_state_seconds - steady_compute, 0.0)
                if steady_state_seconds is not None and steady_compute is not None else None
            ),
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
    progress: dict | None = None,
) -> dict[str, Any]:
    progress = {} if progress is None else progress
    started = time.perf_counter()
    progress.update({"analysis_id": "", "phase": "create", "started_at": time.time(),
                     "phase_timestamps": {key: None for key in (
                         "build_started_at", "build_executed_at", "distribution_started_at",
                         "distribution_executed_at", "execution_started_at", "execution_executed_at"
                     )}, "status_history": []})
    benchmark_path = os.path.join(BENCHMARKS_DIR, script_name)
    assert os.path.exists(benchmark_path), f"Benchmark script {script_name} not found."

    print(f"\n[>>>] RUNNING BENCHMARK: {script_name}")

    analysis_name = f"Benchmark-{uuid.uuid4().hex[:8]}"
    master_image_id = resolve_master_image_id(core_client)
    progress["master_image_id"] = master_image_id
    progress["script_sha256"] = script_digest(benchmark_path)
    local_baseline = None
    if script_name == "07_compute_baseline.py":
        local_baseline = runpy.run_path(benchmark_path)["run_workload"]()
        started = time.perf_counter()
        progress["started_at"] = time.time()

    analysis = call_with_retries(
        core_client.create_analysis,
        name=analysis_name, project_id=project_id, master_image_id=master_image_id,
    )
    print(f"    [+] Analysis Created: {analysis.id}")
    progress["analysis_id"] = str(analysis.id)
    progress["phase"] = "configure"
    journal_path = Path(raw_output_dir) / f"progress_{analysis.id}.json"
    journal_path.write_text(json.dumps(progress, indent=2), encoding="utf-8")

    for node_id in node_ids:
        call_with_retries(core_client.create_analysis_node, analysis_id=analysis.id, node_id=node_id)
    print("    [+] Nodes Added")

    time.sleep(1)
    progress["phase"] = "upload"
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
    progress["phase"] = "build"
    call_with_retries(core_client.send_analysis_command, analysis.id, "buildStart")

    print("    [*] Polling for completion...")
    start_exec_time = time.perf_counter()
    phase_timestamps = progress["phase_timestamps"]
    phase_timestamps["build_started_at"] = start_exec_time
    distribution_started = False

    while time.perf_counter() - start_exec_time < BENCHMARK_TIMEOUT_SECONDS:
        analysis = call_with_retries(core_client.get_analysis, analysis.id)
        now = time.perf_counter()
        _update_phase_timestamps(analysis=analysis, phase_timestamps=phase_timestamps, now=now)
        progress["image_build_hash"] = analysis.build_hash
        state = [analysis.build_status, analysis.distribution_status, analysis.execution_status]
        if not progress["status_history"] or progress["status_history"][-1]["state"] != state:
            progress["status_history"].append({"state": state, "timestamp": time.time(), "elapsed_seconds": now - started})
        progress["phase"] = ("execute" if analysis.distribution_status == "executed"
                             else "distribute" if analysis.build_status == "executed" else "build")
        journal_path.write_text(json.dumps(progress, indent=2), encoding="utf-8")
        print(
            f"        Build: {analysis.build_status} | Dist: {analysis.distribution_status} | Exec: {analysis.execution_status}"
        )

        if "failed" in state or "stopped" in state:
            raise RuntimeError(f"Analysis failed in {progress['phase']}: {state}")

        if analysis.build_status == "executed":
            if not distribution_started and analysis.distribution_status is None:
                call_with_retries(core_client.send_analysis_command, analysis.id, "distributionStart")
                phase_timestamps["distribution_started_at"] = time.perf_counter()
                distribution_started = True

            if analysis.distribution_status == "executed":
                if analysis.execution_status in ["executed", "finished"]:
                    print("    [+] Analysis Finished Successfully!")
                    break

        time.sleep(POLL_INTERVAL_SECONDS)
    else:
        raise TimeoutError(f"Timeout in {progress['phase']} for analysis {analysis.id}.")

    progress["phase"] = "download"
    local_result_path = download_result_tar(
        core_client=core_client,
        storage_client=storage_client,
        analysis_id=str(analysis.id),
        raw_output_dir=raw_output_dir,
    )

    payload = extract_result_payload(local_result_path)
    progress["phase"] = "validate"
    if not is_successful_payload(payload):
        raise RuntimeError(f"Benchmark payload reports failure: {script_name}")
    if script_name == "00_test_connection.py":
        if set(payload["node_results"]) != set(combined_node_ids):
            raise RuntimeError(f"Connection responses incomplete: {payload['node_results']}")
        if not all(status == "ok" for status in payload["node_results"].values()):
            raise RuntimeError(f"Connection statuses unsuccessful: {payload['node_results']}")
    timings, federation_tax = _derive_runtime_metrics(
        payload=payload,
        phase_timestamps=phase_timestamps,
        total_duration_seconds=time.perf_counter() - started,
    )
    if local_baseline is not None:
        assert all(result["digest"] == local_baseline["digest"] for result in payload["per_node_compute"].values())
        payload["local_baseline"] = local_baseline
        payload["federated_execution_seconds"] = timings["execution_phase_seconds"]
        payload["federated_end_to_end_seconds"] = timings["total_duration_seconds"]
        payload["end_to_end_to_local_ratio"] = timings["total_duration_seconds"] / local_baseline["compute_total_seconds"]
        payload["execution_to_local_ratio"] = (
            timings["execution_phase_seconds"] / local_baseline["compute_total_seconds"]
            if timings["execution_phase_seconds"] is not None else None
        )
    progress["phase"] = "finished"
    journal_path.write_text(json.dumps(progress, indent=2), encoding="utf-8")

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
        "execution_record": progress.copy(),
    }
