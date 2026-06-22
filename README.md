# FLAME Technical Benchmarking Suite

Maintainer: [Jules Kreuer](https://github.com/not-a-feature)

This project contains a suite of benchmarks to quantify the performance and overhead of the FLAME federated learning platform.

## Architecture
- **Orchestrator (`run_benchmark.py`):** Top-level benchmark entrypoint.
- **Runner Core (`benchmarks/benchmark_runner.py`):** Split benchmark execution logic, summary generation, and plot-only flow.
- **Shared Helpers (`benchmarks/utils.py`):** Centralized tar payload parsing and metric extraction.
- **Analysis (`analysis/plot_results.py`):** Generates one PNG per benchmark in `plots/`, with metric-specific visualizations.
- **Benchmarks (`benchmarks/NN_*.py`):** see [What the Benchmarks Measure](#what-the-benchmarks-measure).

## Setup
1. Ensure the `flame-hub-client` is installed in your environment.
2. Update credentials and endpoints in `benchmarks/credentials.py` if necessary.

## Usage
Run the benchmarking suite targeting specific numbers:
```bash
# Run all benchmarks
python run_benchmark.py -b *

# Run specific benchmarks (e.g. 1 and 3)
python run_benchmark.py -b 1 3

# Skip benchmark execution and only plot a given summary file
python run_benchmark.py --plot-only --summary raw_output/summary_<timestamp>.json
```

Generate plots from latest summary:
```bash
python analysis/plot_results.py
```

Generate plots from a specific summary:
```bash
python analysis/plot_results.py --summary raw_output/summary_<timestamp>.json
```

## What the Benchmarks Measure

Every benchmark follows the same Star pattern: a `StarAnalyzer` runs on each selected analyzer node and returns a payload to a `StarAggregator` running on the aggregator node. A "round" is one full analyzer → aggregator cycle. The aggregator decides when to stop via `has_converged`. The runner wraps this with hub-level phase timings (build / distribution / execution), so each benchmark contributes both an application-level signal (collected inside the aggregator) and a platform-level signal (measured by the runner).

### `00_test_connection.py` — Reachability
- **What it does:** One round. Each analyzer returns `{node_id, status: "ok"}`; the aggregator records which nodes responded.
- **What it measures:** Whether every targeted node is registered, reachable, and able to round-trip a trivial payload through the hub. No timing claims.
- **Use it to:** Confirm cluster setup before running any of the timed benchmarks.

### `01_latency_overhead.py` — Control-plane latency
- **What it does:** 10 rounds with an empty payload. The analyzer records the wall-clock interval between consecutive invocations on its node; the aggregator records the interval between consecutive aggregation calls.
- **What it measures:** The fixed per-round cost of an aggregator → analyzer → aggregator hop when there is essentially no data and no compute. This captures broker dispatch, message delivery, and scheduling overhead — the *floor* of round-trip latency on the platform.
- **Key metrics:**
  - `per_node_latency_samples_seconds` — per-analyzer round intervals (boxplotted per node)
  - `effective_communication_latency_max_samples_seconds` — aggregator-side round intervals (worst-case per round)
- **Note on units:** Plot divides by 2 to approximate one-way latency from the round-trip interval.

### `02_large_data_handling.py` — Bulk transfer throughput
- **What it does:** 6 rounds. Each analyzer returns a ~10 MB hex payload; the aggregator divides total bytes returned by the round interval to estimate throughput.
- **What it measures:** Effective application-level throughput across the analyzer → aggregator path for non-trivial payloads. This stresses the message bus / object transport, not the network link in isolation — anything the platform does between `return payload` in the analyzer and `analysis_results` arriving at the aggregator shows up here.
- **Key metrics:**
  - `per_node_throughput_mbps_samples` — Mbps achieved per analyzer per round
  - `effective_application_throughput_mbps_samples` — aggregate Mbps across all responding analyzers per round
- **Note on units:** Computed in Mbps; the plot converts to MB/s (÷8) for display.

### `03_concurrent_scaling.py` — Scheduler steady-state under high round frequency
- **What it does:** 200 rounds with a tiny 1 KB payload (intentionally small to remove network bound). The aggregator records the interval between consecutive rounds and a 10-round moving average.
- **What it measures:** Whether the scheduler / broker / aggregator can sustain a high round frequency over time without per-round latency drifting upward. The moving average exposes degradation patterns (queue growth, memory pressure, backpressure) that single-round samples would hide.
- **Key metrics:**
  - `round_interval_seconds` — per-round wall-clock interval
  - `moving_average_intervals` — 10-round trailing average, plotted as a line over the round axis

### `04_straggler_sensitivity.py` — Synchronization cost under heterogeneous nodes
- **What it does:** 120 rounds. Each analyzer sleeps for a deterministic per-(node, round) duration in `[0, 250ms]` (seeded by SHA-256 of `node_id:round_index`), simulating heterogeneous compute. The aggregator records per-round max, min, and spread, and accumulates the critical-path (max) compute time.
- **What it measures:** How much a synchronous round is bottlenecked by the slowest analyzer. With deterministic per-node sleep distributions, any *additional* spread observed at the aggregator is attributable to platform jitter on top of the planned skew.
- **Key metrics:**
  - `per_node_compute_samples_seconds` — per-analyzer compute time distribution (boxplotted per node)
  - `compute_spread_seconds` — `max - min` analyzer compute time per round
  - `total_critical_compute_time_seconds` — sum of per-round maxima (lower bound on synchronous wall-clock cost)

## Metrics
- **Platform Phase Timing:** Build, distribution, and execution durations are tracked separately.
- **Platform Runtime:** `distribution_phase_seconds + execution_phase_seconds`.
- **Internal Compute:** Extracted from benchmark result payloads.
- **Orchestration Overhead:** `platform_runtime_seconds - internal_compute_seconds`.
- **Data Speed:** Communication-heavy benchmarks emit throughput distributions used for boxplots.

## Runtime Notes
- Current stress settings use large payloads and many rounds, so benchmark completion can take significantly longer.
- The benchmark timeout is configured in `benchmarks/config.py` and currently set for long-running runs.

Each summary entry now includes:
- `timings`: phase-level and derived runtime metrics
- `benchmark_metrics`: benchmark-specific payload (round-level distributions, throughput, node metrics)

## Hub Service Endpoints
- **Auth:** `https://auth.staging.privateaim.net`
- **Core:** `https://core.staging.privateaim.net`
- **Storage:** `https://storage.staging.privateaim.net`
