# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest

from ops.testing import Harness

from charm import TempoCoordinatorCharm

CONTAINER_NAME = "nginx"


class TestTempoCoordinatorCharm(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(TempoCoordinatorCharm)
        self.harness.set_model_name("testmodel")
        self.addCleanup(self.harness.cleanup)
        self.harness.set_leader(True)
        self.harness.begin_with_initial_hooks()
        self.harness.add_relation("s3", "s3-integrator")
        self.harness.add_relation("tempo-cluster", "tempo-worker-k8s")
        self.maxDiff = None  # we're comparing big traefik configs in tests

    def test_entrypoints_are_generated_with_sanitized_names(self):
        expected_entrypoints = {
            "entryPoints": {
                "tempo-http": {"address": ":3200"},
                "tempo-grpc": {"address": ":9096"},
                "zipkin": {"address": ":9411"},
                "otlp-grpc": {"address": ":4317"},
                "otlp-http": {"address": ":4318"},
                "jaeger-thrift-http": {"address": ":14268"},
                "jaeger-grpc": {"address": ":14250"},
                "opencensus": {"address": ":55678"},
            }
        }
        self.assertEqual(self.harness.charm._static_ingress_config, expected_entrypoints)
