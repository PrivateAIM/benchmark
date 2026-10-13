# FLAME Technical Benchmarking Suite

Maintainer: [Jules Kreuer](https://github.com/not-a-feature)

Technical measurements of the FLAME platform. All standard node benchmarks use
`node-1` as aggregator and `node-2` through `node-6` as analyzers, with image
group `python`, image `base`. They do not require input datasets. The current
SDK still requires working project datastore discovery during startup.

## Setup and commands

```powershell
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt

.\.venv\Scripts\python.exe run_benchmark.py -b "*" --repeats 3
.\.venv\Scripts\python.exe run_benchmark.py --scaling --repeats 3
.\.venv\Scripts\python.exe run_benchmark.py --storage-bench --repeats 3
.\.venv\Scripts\python.exe run_benchmark.py --concurrent --repeats 3
.\.venv\Scripts\python.exe run_benchmark.py --encryption-bench --repeats 10
```

Put `FLAME_USERNAME` and `FLAME_PASSWORD` in the ignored
`benchmarks/credentials.json` or environment variables, as before.
API URLs are `https://hub.benchmark.privateaim.dev/{auth,core,storage}/`.
The [Hub UI](https://hub.benchmark.privateaim.dev/projects) hosts the configured
`performance-benchmark-suite` project.

Run one mode at a time. Other examples:

```powershell
.\.venv\Scripts\python.exe run_benchmark.py -b 0 --repeats 1
.\.venv\Scripts\python.exe run_benchmark.py -b 1 2 7 --repeats 5 --environment benchmark-cluster
.\.venv\Scripts\python.exe run_benchmark.py --plot-only --summary raw_output/summary_<run>.json
```

## What is measured

| Mode | Measurement |
|---|---|
| 00 connection | One response from every analyzer, verified against the configured participant set. |
| 01 latency | Empty-payload round-cycle intervals, including both communication directions and scheduler/processing time. |
| 02 large data | Application throughput for payloads from 1 KB to 100 MB per analyzer; three rounds per size. |
| 03 scheduler | Drift over 100 consecutive rounds with fixed small payloads. |
| 04 stragglers | Deterministic delays with maxima 0, 0.25, 1 and 5 seconds; ten samples per level after warm-up. |
| 05 scaling | Round intervals with varying analyzer counts. Use `--scaling` for one through five analyzers plus the aggregator. |
| 06 Core SDK baseline | Same communication protocol as 01 without the Star pattern. This remains a federated benchmark. |
| 07 local/FLAME baseline | Identical deterministic CPU computation locally and on every analyzer, with matching output verification and runtime ratios. |
| `--storage-bench` | Direct hub object-storage write/read timing using temporary benchmark objects. Does not execute on nodes. |
| `--concurrent` | Simultaneous full analysis launches at concurrency 1, 2, 3, 5 and 10; every analysis targets all six nodes. |
| `--encryption-bench` | Local reference encryption primitives, without network or node execution. |

All node scripts are self-contained and uploaded individually. Payloads generated
by throughput benchmarks and temporary storage objects are part of the measurement;
no synthetic input CSVs are uploaded to the project datastores.

Latency plots show complete round intervals. They do not divide measurements by
two or infer one-way network latency. The throughput fit uses measured round
intervals directly against per-analyzer payload size, avoiding confusion between
aggregate throughput and per-analyzer bytes. These application-level intervals
also include payload construction, serialization, transport and scheduling.

The compute baseline uses PBKDF2-SHA256 with fixed inputs and 200,000 iterations,
one untimed warm-up and five timed computations per analyzer. The local denominator
is one analyzer's sequential workload; federated analyzers execute in parallel.
It reports execution-phase/local-compute and full-lifecycle/local-compute ratios.
Hardware differences also affect these ratios. This is a CPU baseline rather than
a clinical workload or predictive-model evaluation.

Encryption measurements follow the [node storage-service reference](https://github.com/PrivateAIM/node-storage-service/blob/main/project/crypto.py):
ECDH, HKDF-SHA256, AES-256-GCM, a 12-byte random IV, and 1 MiB encrypted frames.
The local reference uses ephemeral P-256 keys. It records copy, encryption and
decryption times, per-chunk key derivation, and ciphertext expansion. Key generation
is outside timing and one warm-up is excluded. These are orchestration-host
primitive measurements, not deployed service overhead or an encryption-on/off
cluster comparison.

## Repetitions, failures and reproducibility

`--repeats` defaults to three independent full runs. Transport errors are retried
within an existing analysis. `--analysis-retries` defaults to zero; enabling it
starts fresh analyses after failed attempts and preserves every attempt. These
retries do not count as independent repetitions. Benchmark/scaling/concurrency
order is shuffled using `--seed` (default zero); selected connection checks stay
first. Three repetitions are exploratory; increase the count for more precise
estimates.

Results retain analysis IDs, failed phases, elapsed time, HTTP retry counts/events,
and observed status transitions. They record script/configuration hashes, image
ID/build hash, client package versions, Git revision/dirty state when accessible,
and orchestration-host information. `progress_<analysis-id>.json` journals identify
remote analyses if interrupted. Failed remote analyses remain available for
inspection. Final failed attempts cause exit status 1.

Use `--environment` for a cluster label and `--condition cold|warm` for an
operator-established condition. The condition flag does not clear caches. Supply
actual hardware, placement and deployed component versions with
`--node-info node-info.json`, for example:

```json
{"node-1": {"cpu": "record actual model", "ram_gb": 16, "placement": "worker-a"}}
```

Unrecorded resources remain unknown. Benchmark 07 also reports actual node
Python/package versions. Summaries have `.statistics.json` sidecars with failure
rates, median, p95 and seeded 95% bootstrap intervals for the median across
independent repetitions. Individual rounds are not treated as independent runs.
Concurrent launches are aggregated within each repetition; incomplete batches
remain in failure counts but are excluded from performance statistics. A single
repetition has no confidence interval.

## Timing and outputs

Full lifecycle duration covers analysis creation, upload, build, distribution,
execution and download. Initial project setup/authentication and local baseline
computation are outside that timer. Durations use a monotonic clock; status-history
timestamps use Unix time. Raw phase timestamps are host-local monotonic readings.

Phase boundaries are observed through polling (`POLL_INTERVAL_SECONDS`, currently
10 seconds), so short phases have substantial timing uncertainty. Unknown phase
durations are `null`, not zero. Platform runtime means distribution plus execution;
build remains separate. Compute-derived overhead is available only when compute is
measured (04 and 07); other benchmarks leave it unknown.

`steady_state_seconds` sums recorded post-warm-up round intervals.
`startup_and_completion_seconds` is the remaining observed execution duration.
Benchmark 04 subtracts matching rounds' critical compute to estimate steady-state
overhead. This is distinct from full lifecycle overhead.

Raw summaries and journals are in `raw_output/`. Plots are under `plots/<benchmark>/`
with separate directories for each repeated attempt. `plots/run_statistics/`
contains reliability counts, including failures, and duration uncertainty plots.
Scaling plots summarize per-run medians; they do not pool all rounds as independent
observations. Successful runs can be replotted with `--plot-only`.

Offline verification:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
