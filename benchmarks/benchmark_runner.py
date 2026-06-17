import argparse
import json
import os
import time
import traceback
import uuid
from typing import Any

import flame_hub

from analysis.plot_results import generate_plots
from benchmarks.config import (
    BENCHMARK_TIMEOUT_SECONDS,
    POLL_INTERVAL_SECONDS,
    PROJECT_NAME,
    RAW_OUTPUT_DIR_NAME,
    TARGET_NODE_NAMES,
    MAX_RETRIES,
)
from benchmarks.credentials import AUTH_URL, CORE_URL, PASSWORD, STORAGE_URL, USERNAME
from benchmarks.utils import (
    benchmark_slug,
    derive_internal_compute_seconds,
    extract_result_payload,
    is_successful_payload,
)

BENCHMARKS_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(BENCHMARKS_DIR)


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


def _ensure_code_bucket(core_client: flame_hub.CoreClient, analysis_id: str) -> Any:
    deadline = time.time() + 60.0
    while True:
        buckets = core_client.find_analysis_buckets(filter={"analysis_id": analysis_id})
        code_buckets = [bucket for bucket in buckets if bucket.type == "CODE"]
        if len(code_buckets) > 0:
            code_bucket = code_buckets[0]
            assert (
                code_bucket.bucket_id is not None
            ), f"CODE bucket {code_bucket.id} has no storage bucket_id assigned"
            return code_bucket
        assert (
            time.time() < deadline
        ), f"CODE bucket not auto-created for analysis {analysis_id} within timeout"
        time.sleep(1.0)


def _upload_benchmark_code(
    core_client: flame_hub.CoreClient,
    storage_client: flame_hub.StorageClient,
    analysis_id: str,
    benchmark_path: str,
    script_name: str,
) -> None:
    code_bucket = _ensure_code_bucket(core_client, analysis_id)
    code_bucket_data = code_bucket.model_dump()
    code_bucket_core_id = str(code_bucket_data["id"])

    if "external_id" in code_bucket_data and code_bucket_data["external_id"] is not None:
        storage_bucket_id = str(code_bucket_data["external_id"])
    else:
        storage_bucket_id = str(code_bucket_data["bucket_id"])

    with open(benchmark_path, "rb") as benchmark_file:
        script_bytes = benchmark_file.read()

    upload_response = storage_client.upload_to_bucket(
        storage_bucket_id, {"file_name": script_name, "content": script_bytes}
    )
    assert len(upload_response) > 0, "Upload did not return file metadata."

    time.sleep(2)
    response = core_client._client.get(
        f"analysis-bucket-files?filter[analysis_bucket_id]={code_bucket_core_id}"
    )
    bucket_file_payload = response.json()["data"]
    file_ids = [entry["id"] for entry in bucket_file_payload if entry["path"] == script_name]
    assert len(file_ids) == 1, f"Expected exactly one uploaded entrypoint file for {script_name}."

    core_client.update_analysis_bucket_file(
        analysis_bucket_file_id=file_ids[0],
        is_entrypoint=True,
    )


def _download_result_tar(
    core_client: flame_hub.CoreClient,
    storage_client: flame_hub.StorageClient,
    analysis_id: str,
    raw_output_dir: str,
) -> str:
    buckets = core_client.find_analysis_buckets(filter={"analysis_id": analysis_id})
    result_buckets = [bucket for bucket in buckets if bucket.type == "RESULT"]
    assert len(result_buckets) > 0, f"No RESULT bucket found for analysis {analysis_id}"
    result_bucket = result_buckets[0]

    print(f"    [+] Downloading results from bucket {result_bucket.id}...")
    result_path = os.path.join(raw_output_dir, f"results_{analysis_id}.tar")
    with open(result_path, "wb") as result_tar:
        for chunk in storage_client.stream_bucket_tarball(result_bucket.bucket_id):
            result_tar.write(chunk)

    print(f"    [+] Results saved to {result_path}")
    return result_path


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


def _authenticate_clients() -> tuple[flame_hub.CoreClient, flame_hub.StorageClient]:
    print(f"[*] Authenticating with {AUTH_URL}...")
    auth = flame_hub.auth.PasswordAuth(username=USERNAME, password=PASSWORD, base_url=AUTH_URL)
    core_client = flame_hub.CoreClient(base_url=CORE_URL, auth=auth)
    storage_client = flame_hub.StorageClient(base_url=STORAGE_URL, auth=auth)
    return core_client, storage_client


def _ensure_project(core_client: flame_hub.CoreClient) -> Any:
    print(f"[*] Ensuring project '{PROJECT_NAME}' exists...")
    projects = core_client.find_projects(name=PROJECT_NAME)
    matching_projects = [project for project in projects if project.name == PROJECT_NAME]

    if len(matching_projects) == 0:
        project = core_client.create_project(name=PROJECT_NAME)
        print(f"\n[!] New project '{PROJECT_NAME}' created.")
        print("[!] Please connect the MinIO Bucket by creating a Data Store for this project.")
        assert False, (
            f"Project {project.id} created but data store is not configured. "
            "Configure storage and rerun."
        )

    return matching_projects[0]


def _is_aggregator_node_name(node_name: str) -> bool:
    return node_name.lower().startswith("aggregator")


def _resolve_target_nodes(
    core_client: flame_hub.CoreClient, project_id: str
) -> tuple[list[str], dict[str, str], list[str]]:
    nodes = core_client.get_nodes()
    selected_nodes = [node for node in nodes if node.name in TARGET_NODE_NAMES]
    assert len(selected_nodes) > 0, f"No nodes matching {TARGET_NODE_NAMES} found."

    node_ids = [str(node.id) for node in selected_nodes]
    node_labels = {str(node.id): str(node.name) for node in selected_nodes}
    combined_node_ids = [
        str(node.id) for node in selected_nodes if not _is_aggregator_node_name(str(node.name))
    ]
    if len(combined_node_ids) == 0:
        combined_node_ids = node_ids.copy()

    print(f"[+] Using {len(node_ids)} nodes: {[node.name for node in selected_nodes]}")

    existing_project_nodes = core_client.get_project_nodes()
    existing_node_ids = [
        str(project_node.node_id)
        for project_node in existing_project_nodes
        if str(project_node.project_id) == str(project_id)
    ]

    for node_id in node_ids:
        if str(node_id) not in existing_node_ids:
            core_client.create_project_node(project_id=project_id, node_id=node_id)
            print(f"[+] Added node {node_id} to project")

    return node_ids, node_labels, combined_node_ids


def _discover_benchmarks() -> list[str]:
    benchmark_files = [
        filename
        for filename in os.listdir(BENCHMARKS_DIR)
        if filename.endswith(".py") and filename[0].isdigit()
    ]
    benchmark_files.sort()
    return benchmark_files


def _select_benchmarks(all_benchmarks: list[str], benchmark_args: list[str]) -> list[str]:
    if "*" in benchmark_args:
        return all_benchmarks

    selected = []
    for benchmark_id in benchmark_args:
        prefix = f"{int(benchmark_id):02d}_"
        selected.extend([benchmark for benchmark in all_benchmarks if benchmark.startswith(prefix)])

    assert len(selected) > 0, "No benchmarks matched the selection."
    return selected


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
    images = core_client.get_master_images()
    assert len(images) > 0, "No master images found."
    master_image_id = images[0].id

    analysis = core_client.create_analysis(
        name=analysis_name, project_id=project_id, master_image_id=master_image_id
    )
    print(f"    [+] Analysis Created: {analysis.id}")

    for node_id in node_ids:
        core_client.create_analysis_node(analysis_id=analysis.id, node_id=node_id)
    print("    [+] Nodes Added")

    time.sleep(1)
    _upload_benchmark_code(
        core_client=core_client,
        storage_client=storage_client,
        analysis_id=str(analysis.id),
        benchmark_path=benchmark_path,
        script_name=script_name,
    )

    time.sleep(2)
    analysis = core_client.get_analysis(analysis.id)

    core_client.send_analysis_command(analysis.id, "configurationLock")
    core_client.send_analysis_command(analysis.id, "buildStart")

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
        analysis = core_client.get_analysis(analysis.id)
        _update_phase_timestamps(analysis=analysis, phase_timestamps=phase_timestamps, now=now)
        print(
            f"        Build: {analysis.build_status} | Dist: {analysis.distribution_status} | Exec: {analysis.execution_status}"
        )

        assert analysis.build_status != "failed", "Analysis Build Failed."
        assert analysis.distribution_status != "failed", "Analysis Distribution Failed."
        assert analysis.execution_status != "failed", "Analysis Run Failed."

        if analysis.build_status == "executed":
            if not distribution_started and analysis.distribution_status is None:
                core_client.send_analysis_command(analysis.id, "distributionStart")
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
    local_result_path = _download_result_tar(
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


def _print_run_table(results: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 60)
    print(
        f"{'BENCHMARK':<30} | {'STATUS':<9} | {'PLATFORM':>9} | {'BUILD':>7} | {'COMPUTE':>8} | {'OVERHEAD':>8}"
    )
    print("-" * 60)
    for result in results:
        if result["status"] == "Success":
            platform_seconds = result["timings"]["platform_runtime_seconds"]
            build_seconds = result["timings"]["build_phase_seconds"]
            compute_seconds = result["timings"]["internal_compute_seconds"]
            overhead_seconds = result["timings"]["orchestration_overhead_seconds"]
            print(
                f"{result['script']:<30} | {result['status']:<9} | {platform_seconds:>8.2f}s | {build_seconds:>6.2f}s | {compute_seconds:>7.2f}s | {overhead_seconds:>7.2f}s"
            )
            print(f"  -> Results: {os.path.basename(result['result_file'])}")
        else:
            print(
                f"{result['script']:<30} | {result['status']:<9} | {'-':>9} | {'-':>7} | {'-':>8} | {'-':>8}"
            )
            print(f"  -> Error: {result['error_message']}")


def _failed_result(
    script_name: str,
    node_count: int,
    exception: Exception,
    node_labels: dict[str, str] | None = None,
    combined_node_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "script": script_name,
        "benchmark": benchmark_slug(script_name),
        "status": "Failed",
        "error_type": type(exception).__name__,
        "error_message": str(exception),
        "result_file": "",
        "analysis_id": "",
        "node_count": node_count,
        "node_labels": node_labels or {},
        "combined_node_ids": combined_node_ids or [],
        "timings": {
            "total_duration_seconds": 0.0,
            "build_phase_seconds": 0.0,
            "distribution_phase_seconds": 0.0,
            "execution_phase_seconds": 0.0,
            "platform_runtime_seconds": 0.0,
            "internal_compute_seconds": 0.0,
            "orchestration_overhead_seconds": 0.0,
            "end_to_end_overhead_seconds": 0.0,
        },
        "federation_tax": 0.0,
        "benchmark_metrics": {},
    }


def _write_summary(results: list[dict[str, Any]], raw_output_dir: str) -> str:
    summary_file = os.path.join(raw_output_dir, f"summary_{int(time.time())}.json")
    with open(summary_file, "w", encoding="utf-8") as summary_handle:
        json.dump(results, summary_handle, indent=2)
    print(f"\n[+] Run summary saved to {summary_file}")
    return summary_file


def _run_benchmarks(benchmark_args: list[str]) -> str:
    raw_output_dir = os.path.join(PROJECT_ROOT, RAW_OUTPUT_DIR_NAME)
    os.makedirs(raw_output_dir, exist_ok=True)

    core_client, storage_client = _authenticate_clients()
    project = _ensure_project(core_client)
    node_ids, node_labels, combined_node_ids = _resolve_target_nodes(
        core_client, project_id=str(project.id)
    )

    all_benchmarks = _discover_benchmarks()
    selected_benchmarks = _select_benchmarks(
        all_benchmarks=all_benchmarks, benchmark_args=benchmark_args
    )
    print(f"[*] Selected benchmarks to run: {selected_benchmarks}")

    results: list[dict[str, Any]] = []
    for benchmark_script in selected_benchmarks:
        retries = 0
        success = False
        while retries < MAX_RETRIES and not success:
            try:
                result = execute_benchmark(
                    core_client=core_client,
                    storage_client=storage_client,
                    project_id=str(project.id),
                    node_ids=node_ids,
                    node_labels=node_labels,
                    combined_node_ids=combined_node_ids,
                    script_name=benchmark_script,
                    raw_output_dir=raw_output_dir,
                )
                results.append(result)
                success = True
            except Exception as exception:
                retries += 1
                print(
                    f"    [!] Benchmark {benchmark_script} failed with {type(exception).__name__}: {exception} (Attempt {retries}/{MAX_RETRIES})"
                )
                traceback.print_exc()
                if retries >= MAX_RETRIES:
                    result = _failed_result(
                        script_name=benchmark_script,
                        node_count=len(node_ids),
                        exception=exception,
                        node_labels=node_labels,
                        combined_node_ids=combined_node_ids,
                    )
                    results.append(result)
                else:
                    print("    [*] Retrying benchmark...")
                    time.sleep(5)

    _print_run_table(results)
    return _write_summary(results, raw_output_dir=raw_output_dir)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run FLAME Benchmarks")
    parser.add_argument(
        "-b",
        "--benchmarks",
        nargs="+",
        default=[1, 2, 3, 4, 5],
        help="Select benchmarks to run (e.g., -b 0 3 or -b * for all)",
    )
    parser.add_argument(
        "--plot-only",
        action="store_true",
        help="Skip benchmark execution and generate plots from --summary.",
    )
    parser.add_argument(
        "--summary",
        default="",
        help="Path to summary JSON for plotting (required with --plot-only).",
    )
    return parser


def _run_plotting(summary_path: str) -> None:
    absolute_summary_path = os.path.abspath(summary_path)
    assert os.path.exists(absolute_summary_path), f"Summary file not found: {absolute_summary_path}"
    print("\n[*] Generating performance plots...")
    generate_plots(summary_path=absolute_summary_path)


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()

    if args.plot_only:
        assert args.summary != "", "--summary is required when --plot-only is set."
        _run_plotting(summary_path=args.summary)
        return

    generated_summary_path = _run_benchmarks(benchmark_args=args.benchmarks)
    _run_plotting(summary_path=generated_summary_path)
