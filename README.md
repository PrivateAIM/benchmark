# FLAME Technical Benchmarking Suite

Maintainer: [Jules Kreuer](https://github.com/not-a-feature)

This project contains a suite of benchmarks to quantify the performance and overhead of the FLAME federated learning platform.

> **Prototyping defaults:** rounds, payload sizes, file counts, and concurrency
> levels in `benchmarks/config.py` and the `NN_*.py` scripts are set small for
> fast iteration. Scale them up for real measurement runs.

## Run everything

```bash
python run_benchmark.py -b *          # all node benchmarks 00–06 (3-node topology)
python run_benchmark.py --scaling     # node-count latency sweep (aggregator-1 + node-1..N)
python run_benchmark.py --storage-bench   # raw MinIO read/write micro-benchmark
python run_benchmark.py --concurrent      # build/distribution queuing under concurrent launches
```

Each command authenticates, runs, writes a summary JSON to `raw_output/`, and
auto-generates plots under `plots/<benchmark>/`.

Other usage:
```bash
python run_benchmark.py -b 1 3                                  # specific benchmarks
python run_benchmark.py --plot-only --summary raw_output/<f>.json   # replot, no run
python analysis/plot_results.py [--summary raw_output/<f>.json]     # plot latest/given
```

## Setup
1. Install `flame-hub-client`.
2. Put credentials in `benchmarks/credentials.json` (`FLAME_USERNAME`,
   `FLAME_PASSWORD`) or the matching env vars; endpoints live in
   `benchmarks/credentials.py`.

## Architecture
- `run_benchmark.py` → `benchmarks/cli.py` — entrypoint + arg parsing/dispatch.
- `benchmarks/runner.py` — multi-benchmark selection, retries, scaling sweep.
- `benchmarks/execution.py` — runs one analysis end-to-end (upload → build →
  distribute → execute polling → phase timings).
- `benchmarks/node_resolution.py` — resolve target/scaling node sets.
- `benchmarks/hub_io.py` — generic project/bucket/upload/download primitives.
- `benchmarks/summaries.py` — shared result-envelope + summary-file helpers.
- `benchmarks/utils.py` — auth, tar parsing, metric extraction.
- `analysis/plot_results.py` — per-benchmark plots under `plots/<benchmark>/`.

**Script conventions:** `NN_*.py` are *node-side* scripts uploaded to and run on
the nodes (self-contained: stdlib + `flame`/`flamesdk` only). `storage_benchmark.py`
and `concurrent_launch.py` are *orchestrator-side* modules (no number prefix)
that drive the hub directly.

## Benchmarks

Node benchmarks share the Star pattern: a `StarAnalyzer` on each analyzer node
returns a payload to a `StarAggregator` on the aggregator; one analyzer →
aggregator cycle is a "round". Per-round wall-clock time is measured on the
*aggregator* between consecutive aggregations (`time.perf_counter()`), so it
captures the full round trip: analyzer compute + both network hops + broker/
scheduler dispatch — not just network latency.

| # | Script | Measures |
|---|--------|----------|
| 00 | `00_test_connection.py` | Reachability — every node round-trips a trivial payload. |
| 01 | `01_latency_overhead.py` | Control-plane round-trip latency floor (empty payload). |
| 02 | `02_large_data_handling.py` | Throughput vs. payload size; `size_fit` plot fits `round_time = a + b·size` (fixed overhead `a` vs. marginal cost `b`). |
| 03 | `03_concurrent_scaling.py` | Scheduler steady-state under many rapid rounds (drift via moving average). |
| 04 | `04_straggler_sensitivity.py` | Synchronization cost when nodes have heterogeneous (seeded) compute times. |
| 05 | `05_node_scaling.py` | Round latency vs. analyzer-node count (`--scaling`; fails fast if a configured `node-N` is unregistered). |
| 06 | `06_no_star_baseline.py` | Star-pattern tax control — same protocol as 01 but hand-rolled on `FlameCoreSDK` (no `StarModel` data-fetch step). Compare against 01. |
| — | `storage_benchmark.py` | Raw MinIO per-file write/read speed vs. size, no analysis lifecycle. Baseline vs. 02's end-to-end throughput. |
| — | `concurrent_launch.py` | Build/distribution queuing: fire N analyses at once (thread pool, one auth per thread) and record each one's build/distribution latency vs. N. |

### What each benchmark measures, in detail

**00 — `test_connection`**
Every node sends a single trivial result (`{"success": true, "node_id", "status"}`)
back to the aggregator, which checks that all nodes responded. There is no
timing payload — this is a pass/fail liveness/wiring check, not a performance
measurement. Use it before trusting numbers from any other benchmark.

**01 — `latency_overhead`**
Analyzer rounds carry no payload (`data_type="none"`); the aggregator records
the wall-clock gap between consecutive aggregation calls
(`round_interval_seconds`) and, per node, the analyzer-side gap between
consecutive `analysis_method` calls (`per_node_latency_samples_seconds`).
This is the cheapest possible round FLAME can run, so it estimates the
fixed per-round overhead (auth/dispatch/serialization/broker hops) with
payload size and compute time driven to ~0. Plots divide the interval by 2
to approximate a one-way latency.

**02 — `large_data_handling`**
Each round, every analyzer generates a random payload of a configured size
and ships it to the aggregator, which sums received bytes and divides by the
round interval to get `effective_application_throughput_mbps`. Because round
size is varied across a small fixed set of sizes, the `size_fit` plot
regresses `round_time = a + b·size`: the intercept `a` is the same fixed
per-round overhead measured in 01, and the slope `b` is the marginal
transfer cost per byte. A meaningful split between `a` and `b` requires the
largest configured size to be well above the noise floor set by 01.

**03 — `concurrent_scaling`**
A fixed small (1KB) payload is reused every round — deliberately, so that
network/serialization cost stays constant and any drift in round interval
can only come from the broker/aggregator/scheduler accumulating state or
contention across many rapid rounds back-to-back. The aggregator tracks
both the raw per-round interval and a trailing moving average so a slow
upward trend (degradation under sustained load) is visible even with noisy
individual samples.

**04 — `straggler_sensitivity`**
Each analyzer sleeps for a duration deterministically derived from
`sha256(node_id:round_index)` (so it's reproducible across runs without
nodes coordinating) before responding. The aggregator records, per round,
the max/min/spread of reported compute times across nodes
(`compute_spread_seconds`) and accumulates the per-round maximum into
`total_critical_compute_time_seconds` — i.e. the time the *whole* round was
gated on the slowest ("straggler") node, since a Star round cannot
aggregate until every analyzer has replied. This is the only benchmark
that reports a nonzero `internal_compute_seconds`, which feeds the
`orchestration_overhead_seconds` derivation (see Summary schema below).

**05 — `node_scaling`**
Identical protocol to 01 (empty payload, round-interval timing), but driven
by `--scaling` over `SCALING_NODE_COUNTS`: for each `N`, the aggregator plus
`node-1..node-N` run the same latency-floor measurement, and `node_count` is
recorded alongside the timings. The `scaling_sweep` plot shows how the
control-plane round time grows as more analyzers must report in before the
aggregator can proceed — this is fan-in cost, not per-node compute, since
the per-node payload is empty.

**06 — `no_star_baseline`**
Re-implements 01's exact protocol (empty payload, same round-interval
timing) directly on `FlameCoreSDK`'s `send_intermediate_data` /
`await_intermediate_data`, bypassing `StarModel`/`StarAnalyzer`/
`StarAggregator` entirely. Comparing 01 against 06 isolates whatever
overhead the Star abstraction itself adds on top of the raw SDK primitives
("Star pattern tax").

**`storage_benchmark`** *(orchestrator-side, no analysis lifecycle)*
Writes and reads a set of files per configured size directly against a
scratch MinIO bucket via `StorageClient`, timing each write and read call
individually. No analysis is built, distributed, or executed — this isolates
raw object-storage I/O speed as a baseline to compare against 02's
end-to-end (analysis + storage + network) throughput numbers.

**`concurrent_launch`** *(orchestrator-side, no analysis lifecycle)*
For each configured concurrency level `N`, launches `N` trivial analyses at
once from a thread pool (one authenticated client per thread) and records
each analysis's build-phase and distribution-phase duration. This measures
hub-side queuing/contention when many analyses are submitted simultaneously,
independent of anything that happens once an analysis is actually running.

**Plots:** each benchmark emits `summary.{svg,png}`; those with repeated round
measurements (01, 02, 04, 06) also emit `timeseries.{svg,png}` (jitter + moving
average); 02 also emits `size_fit.{svg,png}`; 05 emits `scaling_sweep.{svg,png}`.

**Latency note:** latency plots divide the round-trip interval by 2 to
approximate one-way latency.

## Summary schema
Each summary entry carries `timings` (phase-level + derived runtime metrics) and
`benchmark_metrics` (benchmark-specific distributions). Derived metrics:
platform runtime = distribution + execution; orchestration overhead = platform
runtime − internal compute.

## Known overhead / measurement caveats
- **Orchestration overhead dominates short benchmarks.** `platform_runtime_seconds`
  is measured from analysis-status polling and spans the *entire* analysis run
  (build → distribute → execute, all rounds combined), not just the rounds
  themselves. The one-time build/distribution setup cost can be much larger
  than the actual round time being studied, especially for benchmarks with
  few rounds or trivial payloads (00, 01, 05, 06) — read `build_phase_seconds`
  and `distribution_phase_seconds` separately from round-level
  `benchmark_metrics` rather than only looking at `total_duration_seconds`.
- **`orchestration_overhead_seconds` is only meaningful for 04.**
  `internal_compute_seconds` (`derive_internal_compute_seconds` in
  [utils.py](benchmarks/utils.py)) is derived from
  `total_critical_compute_time_seconds`, which only 04 reports. For every
  other benchmark it defaults to `0.0`, so `orchestration_overhead_seconds`
  collapses to `platform_runtime_seconds` and carries no extra information —
  don't treat it as a real "overhead vs. compute" split outside of 04.
- **Round timing includes scheduler/broker dispatch, not just network.**
  Because intervals are measured aggregator-side between aggregation calls,
  a "latency floor" (01) or "scaling" (05) number already bundles in
  whatever the FLAME scheduler/broker does to dispatch the next round — it
  cannot be reduced to pure network RTT. Use 06 vs. 01 to separate the
  Star-pattern's own overhead from this floor.
- **Polling resolution.** Phase timestamps (`build_started_at`,
  `distribution_executed_at`, etc., in [execution.py](benchmarks/execution.py))
  are only as precise as `POLL_INTERVAL_SECONDS` — phase boundaries can be
  off by up to one poll interval, which matters when comparing short phases
  across benchmarks.
- **Straggler spread is a lower bound.** 04's deterministic sleep schedule is
  fixed per `(node_id, round_index)`, so `compute_spread_seconds` reflects
  the configured sleep distribution, not real-world heterogeneous hardware
  or data-size-driven compute variance.

## Hub endpoints
Auth / Core / Storage at `https://{auth,core,storage}.staging.privateaim.net`.
