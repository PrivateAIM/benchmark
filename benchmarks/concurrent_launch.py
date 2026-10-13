import random
from concurrent.futures import ThreadPoolExecutor

from benchmarks.config import CONCURRENCY_LEVELS, CONCURRENT_LAUNCH_SCRIPT
from benchmarks.hub_io import ensure_project
from benchmarks.measurements import run_metadata
from benchmarks.node_resolution import resolve_target_nodes
from benchmarks.runner import _raw_output_dir, _run_one_with_retries
from benchmarks.summaries import write_summary
from benchmarks.utils import authenticate_clients


def _launch_one(project_id, ids, labels, analyzer_ids, path, analysis_retries):
    core, storage = authenticate_clients()
    return _run_one_with_retries(core, storage, project_id, ids, labels, analyzer_ids,
                                 CONCURRENT_LAUNCH_SCRIPT, path, analysis_retries)


def run_concurrent_launch(repeats: int = 3, analysis_retries: int = 0, environment: str = "",
                          condition: str = "unspecified", node_info: dict | None = None,
                          seed: int = 0) -> str:
    path = _raw_output_dir()
    core, _ = authenticate_clients()
    project = ensure_project(core)
    ids, labels, analyzer_ids = resolve_target_nodes(core, str(project.id))
    metadata = run_metadata(environment, condition, {} if node_info is None else node_info)
    results = []
    rng = random.Random(seed)
    for repetition in range(1, repeats + 1):
        levels = CONCURRENCY_LEVELS.copy()
        rng.shuffle(levels)
        for level in levels:
            print(f"[>>>] Concurrent launches: {level}; repetition {repetition}")
            with ThreadPoolExecutor(max_workers=level) as executor:
                futures = [executor.submit(_launch_one, str(project.id), ids, labels,
                                            analyzer_ids, path, analysis_retries) for _ in range(level)]
                for launch_index, future in enumerate(futures, 1):
                    for result in future.result():
                        connection_metrics = result["benchmark_metrics"]
                        metrics = {"connection_metrics": connection_metrics, "concurrency_levels": [level]}
                        for key, timing in [("build_seconds_by_level", "build_phase_seconds"),
                                            ("distribution_seconds_by_level", "distribution_phase_seconds")]:
                            value = result["timings"][timing]
                            metrics[key] = {str(level): [value] if result["status"] == "Success" and value is not None else []}
                        result.update({"benchmark": "concurrent_launch", "script": "concurrent_launch.py",
                                       "benchmark_metrics": metrics, "sweep_parameter": level,
                                       "repetition": repetition, "launch_index": launch_index, "run_metadata": metadata})
                        results.append(result)
            summary = write_summary(results, path, prefix="concurrent_summary")
    return summary
