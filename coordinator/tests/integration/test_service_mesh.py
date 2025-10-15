import json
from pathlib import Path

import jubilant
import pytest
from pytest_jubilant import pack, get_resources
import yaml
from jubilant import Juju
from tenacity import retry, stop_after_attempt, wait_exponential

from helpers import WORKER_APP, deploy_monolithic_cluster, TEMPO_APP, deploy_istio, deploy_istio_beacon, ISTIO_APP, ISTIO_BEACON_APP
from tests.integration.helpers import get_traces_patiently

TESTER_METADATA = yaml.safe_load(
    Path("./tests/integration/tester/metadata.yaml").read_text()
)
TESTER_APP_NAME = TESTER_METADATA["name"]
TESTER_GRPC_METADATA = yaml.safe_load(
    Path("./tests/integration/tester-grpc/metadata.yaml").read_text()
)
TESTER_GRPC_APP_NAME = TESTER_GRPC_METADATA["name"]


@pytest.mark.setup
def test_build_deploy_tester(juju: Juju):
    path = "./tests/integration/tester/"
    charm = pack(path).absolute()
    resources = get_resources(path)
    juju.deploy(
        charm,
        TESTER_APP_NAME,
        resources=resources,
        num_units=3,
    )


@pytest.mark.setup
def test_build_deploy_tester_grpc(juju: Juju):
    path = "./tests/integration/tester-grpc/"
    charm = pack(path).absolute()
    resources = get_resources(path)
    juju.deploy(
        charm,
        TESTER_GRPC_APP_NAME,
        resources=resources,
        num_units=3,
    )


@pytest.mark.setup
def test_deploy_monolithic_cluster(juju: Juju, tempo_charm: Path):
    deploy_monolithic_cluster(juju)


@pytest.mark.setup
def test_scale_up_tempo(juju: Juju):
    juju.add_unit(TEMPO_APP, num_units=2)
    juju.wait(
        lambda status: jubilant.all_active(status, TEMPO_APP, WORKER_APP), timeout=1000
    )


@pytest.mark.setup
def test_relate(juju: Juju):
    juju.integrate(TEMPO_APP + ":tracing", TESTER_APP_NAME + ":tracing")
    juju.integrate(TEMPO_APP + ":tracing", TESTER_GRPC_APP_NAME + ":tracing")
    # Add tempo_api relation for testing API access
    juju.integrate(TEMPO_APP + ":tempo-api", TESTER_APP_NAME + ":tempo-api")

    juju.wait(
        lambda status: jubilant.all_active(
            status, TEMPO_APP, WORKER_APP, TESTER_APP_NAME, TESTER_GRPC_APP_NAME
        ),
        timeout=1000,
    )


@pytest.mark.setup
def test_deploy_istio(juju: Juju):
    # Deploy Istio core
    deploy_istio(juju)
    juju.wait(
        lambda status: jubilant.all_active(status, ISTIO_APP),
        timeout=1000,
    )


@pytest.mark.setup
def test_deploy_istio_beacon(juju: Juju):
    # Deploy Istio beacon
    deploy_istio_beacon(juju)
    # Configure istio-beacon to enable mesh for the model
    juju.config(ISTIO_BEACON_APP, {"model-on-mesh": "true"})
    juju.wait(
        lambda status: jubilant.all_active(status, ISTIO_BEACON_APP),
        timeout=1000,
    )
    # Relate istio-beacon with tempo for service mesh
    juju.integrate(ISTIO_BEACON_APP + ":service-mesh", TEMPO_APP + ":service-mesh")


@pytest.mark.setup
def test_add_mesh_policies_and_restart_relations(juju: Juju):
    import subprocess
    import tempfile

    # Create the tester-grpc peer policy - keep this manual here so the tester charm doesnt need a service mesh integration.
    # Get the model name from juju which is the namespace in k8s
    model_name = juju.model
    policy_content = f"""apiVersion: security.istio.io/v1
kind: AuthorizationPolicy
metadata:
  name: allow-tester-grpc-peer
  namespace: {model_name}
  labels:
    app.juju.is/created-by: tempo
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: tester-grpc
  rules:
  - from:
    - source:
        principals:
        - cluster.local/ns/{model_name}/sa/tester-grpc
    to:
    - operation:
        ports:
        - "3000"  # Tester service port
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(policy_content)
        f.flush()
        try:
            result = subprocess.run(['kubectl', 'apply', '-f', f.name],
                                  capture_output=True, text=True, check=True)
            print(f"Policy applied successfully: {result.stdout}")
        except subprocess.CalledProcessError as e:
            print(f"Failed to apply policy. Error: {e.stderr}")
            print(f"Policy content:\n{policy_content}")
            raise

    # Remove and re-add tester-grpc relation to pick up mesh changes
    juju.remove_relation(TEMPO_APP + ":tracing", TESTER_GRPC_APP_NAME + ":tracing")
    juju.wait(
        lambda status: jubilant.all_active(
            status, TEMPO_APP, WORKER_APP, TESTER_APP_NAME, TESTER_GRPC_APP_NAME
        ),
        timeout=1000,
    )

    # Re-add the relation with retry
    @retry(stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30))
    def add_relation_with_retry():
        juju.integrate(TEMPO_APP + ":tracing", TESTER_GRPC_APP_NAME + ":tracing")

    add_relation_with_retry()
    juju.wait(
        lambda status: jubilant.all_active(
            status, TEMPO_APP, WORKER_APP, TESTER_APP_NAME, TESTER_GRPC_APP_NAME, ISTIO_APP, ISTIO_BEACON_APP
        ),
        timeout=1000,
    )


def test_verify_traces_http_from_pod(juju: Juju):
    # Test traces using pod-based queries (through mesh)
    traces = get_traces_patiently(
        tempo_host=f"tempo.{juju.model}.svc.cluster.local",
        service_name="TempoTesterCharm",
        tls=False,
        source_pod=f"{WORKER_APP}/0",
        juju=juju
    )
    assert traces, (
        f"There's no trace of charm exec traces in tempo. {json.dumps(traces, indent=2)}"
    )


def test_verify_traces_grpc_from_pod(juju: Juju):
    # Test gRPC traces using pod-based queries (through mesh)
    traces = get_traces_patiently(
        tempo_host=f"tempo.{juju.model}.svc.cluster.local",
        service_name="tester_grpc",  # Fixed service name
        tls=False,
        source_pod=f"{WORKER_APP}/0",
        juju=juju
    )
    assert traces, (
        f"There's no trace of generated grpc traces in tempo. {json.dumps(traces, indent=2)}"
    )


def test_verify_api_readiness_from_tester(juju: Juju):
    # Test that tempo API readiness endpoint is accessible from tester pod
    result = juju.exec(
        f"curl -f http://tempo.{juju.model}.svc.cluster.local:3200/ready",
        unit=f"{TESTER_APP_NAME}/0"
    )
    assert "200" in result.stdout or "ready" in result.stdout.lower()


def test_verify_tempo_api_access_from_tester(juju: Juju):
    # Test that tester charm can access tempo API through mesh
    result = juju.exec(
        f"curl -f http://tempo.{juju.model}.svc.cluster.local:3200/api/search/tag/service.name/values",
        unit=f"{TESTER_APP_NAME}/0"
    )
    assert "200" in result.stdout or "TempoTesterCharm" in result.stdout


@pytest.mark.teardown
def test_remove_relation(juju: Juju):
    juju.remove_relation(TEMPO_APP + ":tracing", TESTER_APP_NAME + ":tracing")
    juju.remove_relation(TEMPO_APP + ":tracing", TESTER_GRPC_APP_NAME + ":tracing")
    juju.remove_relation(TEMPO_APP + ":tempo-api", TESTER_APP_NAME + ":tempo-api")

    juju.wait(lambda status: status.apps[TEMPO_APP].is_active, timeout=1000)
    juju.wait(
        lambda status: jubilant.all_active(status, TEMPO_APP),
        error=lambda status: jubilant.any_blocked(status, TEMPO_APP),
        timeout=1000,
    )
    juju.wait(
        lambda status: status.apps[TEMPO_APP].is_active,
        timeout=1000,
        error=lambda status: jubilant.any_blocked(
            status, TESTER_APP_NAME, TESTER_GRPC_APP_NAME
        ),
    )
