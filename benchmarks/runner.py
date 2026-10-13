import os
import random
import time
from typing import Any

import flame_hub
import httpx2

from benchmarks.config import RAW_OUTPUT_DIR_NAME, SCALING_NODE_COUNTS
from benchmarks.execution import execute_benchmark, _derive_runtime_metrics
from benchmarks.hub_io import ensure_project
from benchmarks.measurements import run_metadata
from benchmarks.node_resolution import resolve_scaling_nodes, resolve_target_nodes
from benchmarks.summaries import write_summary
from benchmarks.utils import HTTP_RETRY_EVENTS, authenticate_clients, benchmark_slug

BENCHMARKS_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(BENCHMARKS_DIR)


def _raw_output_dir() -> str:
    path = os.path.join(PROJECT_ROOT, RAW_OUTPUT_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _discover_benchmarks() -> list[str]:
    return sorted(name for name in os.listdir(BENCHMARKS_DIR)
                  if name.endswith(".py") and name[0].isdigit())


def _select_benchmarks(all_benchmarks: list[str], benchmark_args: list[str]) -> list[str]:
    if "*" in benchmark_args:
        return all_benchmarks
    selected = []
    for benchmark_id in benchmark_args:
        matches = [name for name in all_benchmarks if name.startswith(f"{int(benchmark_id):02d}_")]
        assert matches, f"Unknown benchmark ID: {benchmark_id}"
        selected.extend(matches)
    assert selected
    return selected


def _run_one_with_retries(
    core_client: Any, storage_client: Any, project_id: str,
    node_ids: list[str], node_labels: dict[str, str], combined_node_ids: list[str],
    script_name: str, raw_output_dir: str, analysis_retries: int = 0,
) -> list[dict]:
    results = []
    for attempt in range(1, analysis_retries + 2):
        progress = {}
        events = []
        token = HTTP_RETRY_EVENTS.set(events)
        started = time.perf_counter()
        try:
            result = execute_benchmark(
                core_client, storage_client, project_id, node_ids, node_labels,
                combined_node_ids, script_name, raw_output_dir, progress=progress,
            )
        except (httpx2.TransportError, flame_hub.HubAPIError, RuntimeError, TimeoutError, OSError, ValueError) as error:
            timestamps = progress["phase_timestamps"]
            timings, _ = _derive_runtime_metrics({}, timestamps, time.perf_counter() - started)
            result = {
                "script": script_name, "benchmark": benchmark_slug(script_name),
                "status": "Failed", "error_type": type(error).__name__,
                "error_message": str(error), "failed_phase": progress["phase"],
                "result_file": "", "analysis_id": progress["analysis_id"],
                "node_count": len(node_ids), "node_labels": node_labels,
                "combined_node_ids": combined_node_ids, "timings": timings,
                "federation_tax": None, "benchmark_metrics": {}, "execution_record": progress.copy(),
            }
            print(f"[!] {script_name}: {result['failed_phase']} failed: {error}")
        finally:
            HTTP_RETRY_EVENTS.reset(token)
        result.update({"attempt": attempt, "http_retry_count": len(events),
                       "http_retry_events": events,
                       "final_attempt": result["status"] == "Success" or attempt == analysis_retries + 1})
        results.append(result)
        if result["final_attempt"]:
            break
    return results


def _run_suite(benchmark_args: list[str], scaling: bool, repeats: int, analysis_retries: int,
               environment: str, condition: str, node_info: dict, seed: int) -> str:
    assert repeats >= 1 and analysis_retries >= 0
    path = _raw_output_dir()
    core, storage = authenticate_clients()
    project = ensure_project(core)
    metadata = run_metadata(environment, condition, node_info)
    metadata["project_id"] = str(project.id)
    metadata["randomization_seed"] = seed
    metadata["registered_nodes"] = [{"id": str(n.id), "name": n.name, "type": n.type}
                                     for n in core.get_nodes()]
    scripts = _select_benchmarks(_discover_benchmarks(), benchmark_args) if not scaling else []
    results = []
    rng = random.Random(seed)
    for repetition in range(1, repeats + 1):
        jobs = SCALING_NODE_COUNTS.copy() if scaling else scripts.copy()
        rng.shuffle(jobs)
        if not scaling and "00_test_connection.py" in jobs:
            jobs.remove("00_test_connection.py")
            jobs.insert(0, "00_test_connection.py")
        for job in jobs:
            ids, labels, analyzer_ids = (
                resolve_scaling_nodes(core, str(project.id), job) if scaling
                else resolve_target_nodes(core, str(project.id))
            )
            attempts = _run_one_with_retries(
                core, storage, str(project.id), ids, labels, analyzer_ids,
                "05_node_scaling.py" if scaling else job, path, analysis_retries,
            )
            for row in attempts:
                row.update({"repetition": repetition, "run_metadata": metadata})
            results.extend(attempts)
            summary = write_summary(results, path, prefix="scaling_summary" if scaling else "summary")
    return summary


def run_benchmarks(benchmark_args: list[str], repeats: int = 3, analysis_retries: int = 0,
                   environment: str = "", condition: str = "unspecified",
                   node_info: dict | None = None, seed: int = 0) -> str:
    return _run_suite(benchmark_args, False, repeats, analysis_retries, environment,
                      condition, {} if node_info is None else node_info, seed)


def run_scaling_sweep(repeats: int = 3, analysis_retries: int = 0, environment: str = "",
                      condition: str = "unspecified", node_info: dict | None = None,
                      seed: int = 0) -> str:
    return _run_suite([], True, repeats, analysis_retries, environment,
                      condition, {} if node_info is None else node_info, seed)
