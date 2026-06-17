import time
import json
import os
from flame.star import StarModel, StarAnalyzer, StarAggregator


PAYLOAD_BYTES = 10 * 1024 * 1024
LOCAL_THROUGHPUT_SAMPLES_PER_NODE = 6


class LargeDataAnalyzer(StarAnalyzer):
    def analysis_method(self, data, aggregator_results):
        if not hasattr(self, "cached_payload"):
            self.cached_payload = os.urandom(PAYLOAD_BYTES // 2).hex()
        payload = self.cached_payload

        return {
            "success": True,
            "node_id": self.flame.get_id(),
            "payload": payload,
            "payload_bytes": PAYLOAD_BYTES,
        }


class LargeDataAggregator(StarAggregator):
    def aggregation_method(self, analysis_results):
        if not hasattr(self, "current_round"):
            self.current_round = 0
            self.last_mark = time.perf_counter()
            self.effective_application_throughput_mbps_samples = []
            self.per_node_throughput_mbps_samples = {}

        now = time.perf_counter()
        if self.current_round == 0:
            round_interval_seconds = 0.0
        else:
            round_interval_seconds = now - self.last_mark
            assert round_interval_seconds > 0.0, "Round interval must be positive."

        node_payload_sizes = {res["node_id"]: int(res["payload_bytes"]) for res in analysis_results}

        assert len(node_payload_sizes) > 0, "No large-data results received from nodes."
        total_size_bytes = sum(node_payload_sizes.values())

        if self.current_round > 0:
            round_throughput_mbps = (total_size_bytes * 8) / (round_interval_seconds * 1_000_000)
            self.effective_application_throughput_mbps_samples.append(round_throughput_mbps)
            
            for node_id, size in node_payload_sizes.items():
                node_throughput = (size * 8) / (round_interval_seconds * 1_000_000)
                if node_id not in self.per_node_throughput_mbps_samples:
                    self.per_node_throughput_mbps_samples[node_id] = []
                self.per_node_throughput_mbps_samples[node_id].append(node_throughput)
        else:
            round_throughput_mbps = 0.0

        self.current_round += 1
        self.last_mark = now

        return json.dumps(
            {
                "overall_success": True,
                "round": self.current_round,
                "total_rounds": LOCAL_THROUGHPUT_SAMPLES_PER_NODE,
                "payload_bytes_per_node": PAYLOAD_BYTES,
                "node_payload_sizes": node_payload_sizes,
                "total_size_bytes": total_size_bytes,
                "round_interval_seconds": round_interval_seconds,
                "effective_round_trip_throughput_mbps": round_throughput_mbps,
                "effective_application_throughput_mbps_samples": self.effective_application_throughput_mbps_samples,
                "per_node_throughput_mbps_samples": self.per_node_throughput_mbps_samples,
                "timestamp": time.time(),
            }
        )

    def has_converged(self, result, last_result, num_iterations=None):
        if not hasattr(self, "current_round"):
            return False
        return self.current_round >= LOCAL_THROUGHPUT_SAMPLES_PER_NODE


def main():
    StarModel(
        analyzer=LargeDataAnalyzer,
        aggregator=LargeDataAggregator,
        data_type="none",
        output_type="str",
        simple_analysis=False,
    )


if __name__ == "__main__":
    main()
