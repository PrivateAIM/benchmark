import hashlib
import importlib.metadata
import os
import platform
import subprocess
from collections import defaultdict
from pathlib import Path

import numpy as np


def sample_statistics(values: list[float]) -> dict:
    assert values, "Cannot summarize empty measurements."
    samples = np.asarray(values, dtype=float)
    assert np.isfinite(samples).all(), "Non-finite measurement."
    interval = None
    if len(samples) > 1:
        rng = np.random.default_rng(0)
        medians = np.median(rng.choice(samples, size=(2000, len(samples))), axis=1)
        interval = np.percentile(medians, [2.5, 97.5]).tolist()
    return {
        "count": len(values), "median": float(np.median(samples)),
        "p95": float(np.percentile(samples, 95)),
        "median_bootstrap_ci95": interval,
    }


def run_metadata(environment: str, condition: str, node_info: dict) -> dict:
    root = Path(__file__).resolve().parent.parent
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
    )
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True)
    installed = {
        distribution.metadata["Name"].lower(): distribution.version
        for distribution in importlib.metadata.distributions()
    }
    return {
        "environment_label": environment, "condition": condition,
        "node_resources_and_placement": node_info,
        "orchestrator": {
            "python": platform.python_version(), "platform": platform.platform(),
            "machine": platform.machine(), "cpu_count": os.cpu_count(),
            "packages": {name: installed[name] for name in (
                "flame-hub-client", "httpx2", "numpy", "matplotlib", "cryptography"
            ) if name in installed},
        },
        "git_revision": revision.stdout.strip() if revision.returncode == 0 else None,
        "git_revision_error": revision.stderr.strip() if revision.returncode else None,
        "git_dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
        "configuration_sha256": hashlib.sha256((root / "benchmarks/config.py").read_bytes()).hexdigest(),
        "source_sha256": {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for folder in (root / "benchmarks", root / "analysis") for path in folder.glob("*.py")
        },
        "polling_timestamps": "observed; bounded by polling interval, not server event timestamps",
    }


def script_digest(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def experiment_statistics(results: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for result in results:
        parameter = result["sweep_parameter"] if "sweep_parameter" in result else None
        groups[(result["benchmark"], result["node_count"], parameter)].append(result)
    summaries = []
    for (benchmark, node_count, parameter), rows in groups.items():
        successful = [row for row in rows if row["status"] == "Success"]
        final_rows = [row for row in rows if row["final_attempt"]]
        repetitions = {row["repetition"] for row in final_rows}
        failed_repetitions = {row["repetition"] for row in final_rows if row["status"] == "Failed"}
        summary = {
            "benchmark": benchmark, "node_count": node_count,
            "sweep_parameter": parameter,
            "attempt_count": len(rows), "analysis_runs": len(final_rows),
            "independent_runs": len(repetitions),
            "failed_attempts": sum(row["status"] == "Failed" for row in rows),
            "failed_runs": len(failed_repetitions),
            "attempt_failure_rate": sum(row["status"] == "Failed" for row in rows) / len(rows),
            "run_failure_rate": len(failed_repetitions) / len(repetitions),
            "statistics_unit": "complete successful repetitions; concurrent launches aggregated within each repetition",
            "total_duration_seconds": None, "run_median_round_seconds": None,
        }
        complete = [row for row in successful if row["repetition"] not in failed_repetitions]
        if complete:
            by_repetition = defaultdict(list)
            round_by_repetition = defaultdict(list)
            for row in complete:
                by_repetition[row["repetition"]].append(row["timings"]["total_duration_seconds"])
                if "round_interval_seconds" in row["benchmark_metrics"] and row["benchmark_metrics"]["round_interval_seconds"]:
                    round_by_repetition[row["repetition"]].append(float(np.median(row["benchmark_metrics"]["round_interval_seconds"])))
            summary["total_duration_seconds"] = sample_statistics([float(np.median(values)) for values in by_repetition.values()])
            rounds = [float(np.median(values)) for values in round_by_repetition.values()]
            if rounds:
                summary["run_median_round_seconds"] = sample_statistics(rounds)
        summaries.append(summary)
    return summaries
