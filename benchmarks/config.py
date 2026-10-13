DEFAULT_NODE_NAMES = ["node-1", "node-2", "node-3", "node-4", "node-5", "node-6"]

PROJECT_NAME = "Performance-Benchmark-Suite"
MASTER_IMAGE_GROUP = "python"
MASTER_IMAGE_NAME = "base"
TARGET_NODE_NAMES = DEFAULT_NODE_NAMES

# Prototyping defaults: small/fast. Scale these up for real measurement runs.
BENCHMARK_TIMEOUT_SECONDS = 7200
CODE_BUCKET_TIMEOUT_SECONDS = 300
POLL_INTERVAL_SECONDS = 10
RAW_OUTPUT_DIR_NAME = "raw_output"

# Transient network errors (ReadTimeout, ConnectError, ...) on individual hub
# API calls are retried in place at this granularity, so a single dropped
# connection during a multi-hour poll loop doesn't discard a build/distribution
# that already succeeded and force a full benchmark restart.
HTTP_RETRY_MAX_ATTEMPTS = 5
HTTP_RETRY_BACKOFF_SECONDS = 5

# Node-count scaling sweep: node-1 aggregates node-2 through node-6.
SCALING_AGGREGATOR_NODE_NAME = "node-1"
SCALING_ANALYZER_NODE_NAMES = DEFAULT_NODE_NAMES[1:]
SCALING_NODE_COUNTS = [1, 2, 3, 4, 5]

# Storage micro-benchmark: file sizes (bytes) x file count per size.
STORAGE_BENCH_SIZES_BYTES = [1_024, 100_000, 1_000_000, 10_000_000]
STORAGE_BENCH_FILES_PER_SIZE = 10

# Concurrent-launch sweep: fire N trivial analyses at once and measure each
# one's build/distribution queuing latency.
CONCURRENCY_LEVELS = [1, 2, 3, 5, 10]
CONCURRENT_LAUNCH_SCRIPT = "00_test_connection.py"
