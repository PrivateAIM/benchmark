import json
import time
from flame.star import StarModel, StarAnalyzer, StarAggregator


class ConnectionAnalyzer(StarAnalyzer):
    def analysis_method(self, data, aggregator_results):
        return {"success": True, "node_id": self.flame.get_id(), "status": "ok"}


class ConnectionAggregator(StarAggregator):
    def aggregation_method(self, analysis_results):
        node_results = {res["node_id"]: res["status"] for res in analysis_results}
        assert len(node_results) > 0, "No node connection result received."
        return json.dumps(
            {
                "overall_success": True,
                "node_results": node_results,
                "nodes_responded": len(node_results),
                "average_compute_time": 0.0,
                "timestamp": time.time(),
            }
        )

    def has_converged(self, result, last_result, num_iterations=None):
        # We only need one round to confirm the connection
        return True


def main():
    StarModel(
        analyzer=ConnectionAnalyzer,
        aggregator=ConnectionAggregator,
        data_type="none",
        output_type="str",
    )


if __name__ == "__main__":
    main()
