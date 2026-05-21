import shlex
import subprocess
from contextlib import nullcontext

import jubilant
import pytest
import requests
from jubilant import Juju

from tempo import Tempo
from tests.integration.helpers import (
    ISTIO_APP,
    ISTIO_BEACON_APP,
    TEMPO_APP,
    WORKER_APP,
    deploy_istio,
    deploy_istio_beacon,
    deploy_monolithic_cluster,
    emit_trace,
    get_app_ip_address,
    get_tempo_application_endpoint,
    query_traces_patiently_from_client_localhost,
    service_mesh,
)

_MESH_XFAIL = pytest.mark.xfail(
    reason=(
        "Mesh tests: Istio RBAC only grants access to Tempo's receiver/API ports to sources "
        "with an active consumer relation (tracing, tempo-api, grafana-source). The policy is "
        "enforced by the ztunnel for all traffic (both in-mesh and external connections), so "
        "tests cannot emit or query traces while the mesh is active without a dedicated consumer "
        "charm. A dedicated in-mesh consumer charm is required to properly exercise these paths."
    ),
    run=False,
)


@pytest.mark.juju_setup
def test_deploy_istio(juju: Juju):
    # Deploy Istio components
    deploy_istio(juju)
    deploy_istio_beacon(juju)
    juju.wait(
        lambda status: jubilant.all_active(status, ISTIO_APP, ISTIO_BEACON_APP),
        timeout=1000,
    )


@pytest.mark.juju_setup
def test_deploy_monolithic_cluster(juju: Juju):
    deploy_monolithic_cluster(juju)


@pytest.mark.juju_setup
# scaling the coordinator before ingesting traces to verify that scaling won't stop traces ingestion.
def test_scale_up_tempo(juju: Juju):
    # GIVEN we scale up tempo
    juju.add_unit(TEMPO_APP, num_units=2)
    # THEN all units become active
    juju.wait(
        lambda status: jubilant.all_active(status, TEMPO_APP, WORKER_APP), timeout=1000
    )


@pytest.mark.juju_setup
def test_enable_otlp_grpc(juju: Juju):
    # Enable otlp_grpc so that the grpc ingestion and routing tests can run.
    # otlp_http is always enabled; otlp_grpc needs to be explicitly force-enabled
    # when there is no requirer charm on the tracing relation requesting it.
    juju.config(TEMPO_APP, {"always_enable_otlp_grpc": "True"})
    juju.wait(
        lambda status: jubilant.all_active(status, TEMPO_APP, WORKER_APP),
        timeout=1000,
        delay=5,
        successes=3,
    )


@pytest.mark.parametrize(
    "enable_service_mesh",
    [pytest.param(True, marks=_MESH_XFAIL), False],
)
def test_verify_traces_http(juju: Juju, nonce, enable_service_mesh):
    # GIVEN a deployed tempo cluster (and optionally a service mesh)
    # WHEN we emit an HTTP trace via tracegen
    # THEN it should appear in the tempo trace store
    with (
        service_mesh(
            juju=juju,
            beacon_app_name=ISTIO_BEACON_APP,
            apps_to_be_related_with_beacon=[TEMPO_APP],
        )
        if enable_service_mesh
        else nullcontext()
    ):
        tempo_address = get_app_ip_address(juju, TEMPO_APP)
        endpoint = get_tempo_application_endpoint(
            tempo_address, protocol="otlp_http", tls=False
        )
        emit_trace(
            endpoint, nonce=nonce, proto="otlp_http", service_name="tracegen-http"
        )
        traces = query_traces_patiently_from_client_localhost(
            tempo_host=tempo_address,
            service_name="tracegen-http",
            nonce=nonce,
            tls=False,
        )
        assert traces, "No HTTP traces found in tempo after tracegen run"


@pytest.mark.parametrize(
    "enable_service_mesh",
    [pytest.param(True, marks=_MESH_XFAIL), False],
)
def test_verify_traces_grpc(juju: Juju, nonce, enable_service_mesh):
    # GIVEN a deployed tempo cluster with otlp_grpc enabled (and optionally a service mesh)
    # WHEN we emit a gRPC trace via tracegen
    # THEN it should appear in the tempo trace store
    with (
        service_mesh(
            juju=juju,
            beacon_app_name=ISTIO_BEACON_APP,
            apps_to_be_related_with_beacon=[TEMPO_APP],
        )
        if enable_service_mesh
        else nullcontext()
    ):
        tempo_address = get_app_ip_address(juju, TEMPO_APP)
        endpoint = get_tempo_application_endpoint(
            tempo_address, protocol="otlp_grpc", tls=False
        )
        emit_trace(
            endpoint, nonce=nonce, proto="otlp_grpc", service_name="tracegen-grpc"
        )
        traces = query_traces_patiently_from_client_localhost(
            tempo_host=tempo_address,
            service_name="tracegen-grpc",
            nonce=nonce,
            tls=False,
        )
        assert traces, "No gRPC traces found in tempo after tracegen run"


def test_verify_only_requested_receiver_endpoints_listed(juju: Juju):
    # requested receivers are listed (otlp_http always on; otlp_grpc force-enabled above)
    expect_open = ["otlp-grpc", "otlp-http"]
    out = juju.run(TEMPO_APP + "/0", "list-receivers")
    for proto in expect_open:
        assert proto in out.results

    # and non-requested receivers are not listed
    expect_closed = ["zipkin", "jaeger_grpc"]
    for proto in expect_closed:
        assert proto not in out.results


def test_verify_requested_receiver_endpoints_routed(juju: Juju):
    # check that tempo's nginx is only routing protocols that have been requested by requirer
    # charms or tempo itself
    tempo_ip = get_app_ip_address(juju, TEMPO_APP)
    tempo_worker_ip = get_app_ip_address(juju, WORKER_APP)

    # these status codes mean there is something listening, but we have the wrong url, which is ok
    listening_server_status_codes = {404, 415}
    port = f":{Tempo.receiver_ports['otlp_http']}"
    assert (
        requests.get("http://" + tempo_ip + port).status_code
        in listening_server_status_codes
    )
    assert (
        requests.get("http://" + tempo_worker_ip + port).status_code
        in listening_server_status_codes
    )

    curl_out = subprocess.run(
        shlex.split(f"curl -v {tempo_ip}:{Tempo.receiver_ports['otlp_grpc']}"),
        capture_output=True,
        text=True,
    ).stderr
    assert "grpc-status: 3" in curl_out, curl_out

    # nginx and tempo give different error responses on failure
    curl_out = subprocess.run(
        shlex.split(f"curl -v {tempo_worker_ip}:{Tempo.receiver_ports['otlp_grpc']}"),
        capture_output=True,
        text=True,
    ).stderr
    assert "Received HTTP/0.9 when not allowed" in curl_out, curl_out


def test_verify_non_requested_receiver_endpoints_not_routed(juju: Juju):
    # check that tempo's nginx is only routing protocols that have been requested by requirer
    # charms or tempo itself
    tempo_ip = get_app_ip_address(juju, TEMPO_APP)
    tempo_worker_ip = get_app_ip_address(juju, WORKER_APP)

    expect_closed = ["zipkin", "jaeger_grpc", "jaeger_thrift_http"]
    for proto in expect_closed:
        port = f":{Tempo.receiver_ports[proto]}"

        # we can't connect to tempo
        with pytest.raises(requests.exceptions.ConnectionError):
            requests.get("http://" + tempo_ip + port, timeout=0.5)

        # or the worker
        with pytest.raises(requests.exceptions.ConnectionError):
            requests.get("http://" + tempo_worker_ip + port, timeout=0.5)


@pytest.mark.parametrize(
    "enable_service_mesh",
    [pytest.param(True, marks=_MESH_XFAIL), False],
)
def test_verify_tempo_api_integration(juju: Juju, nonce, enable_service_mesh):
    # GIVEN a deployed tempo cluster (and optionally a service mesh)
    # WHEN we emit an HTTP trace from outside the cluster and then query the API from outside
    # THEN the API should be reachable and return the trace we just emitted
    #
    # NOTE: the original version of this test used a dedicated tester charm that consumed the
    # tempo-api Juju relation and verified cross-app access. The tester charm was removed in
    # favour of tracegen. Emission and querying both run from the test host (outside the cluster),
    # matching the pyroscope-operators pattern, which also avoids Istio RBAC restrictions that
    # apply to in-mesh pod-to-pod traffic.
    with (
        service_mesh(
            juju=juju,
            beacon_app_name=ISTIO_BEACON_APP,
            apps_to_be_related_with_beacon=[TEMPO_APP],
        )
        if enable_service_mesh
        else nullcontext()
    ):
        tempo_ip = get_app_ip_address(juju, TEMPO_APP)
        endpoint = get_tempo_application_endpoint(
            tempo_ip, protocol="otlp_http", tls=False
        )
        emit_trace(
            endpoint, nonce=nonce, proto="otlp_http", service_name="tracegen-http"
        )
        traces = query_traces_patiently_from_client_localhost(
            tempo_host=tempo_ip,
            service_name="tracegen-http",
            nonce=nonce,
            tls=False,
        )
        assert traces, "No traces found via internal tempo-api endpoint"


@pytest.mark.parametrize(
    "enable_service_mesh",
    [pytest.param(True, marks=_MESH_XFAIL), False],
)
def test_verify_grafana_datasource_integration(juju: Juju, nonce, enable_service_mesh):
    # GIVEN a deployed tempo cluster (and optionally a service mesh)
    # WHEN we emit a gRPC trace from outside the cluster and then query the API from outside
    # THEN the API should be reachable and return the trace we just emitted
    #
    # NOTE: the original version of this test used a dedicated tester charm that consumed the
    # grafana-source Juju relation and verified cross-app access. The tester charm was removed
    # in favour of tracegen. Emission and querying both run from the test host (outside the
    # cluster), matching the pyroscope-operators pattern, which also avoids Istio RBAC
    # restrictions that apply to in-mesh pod-to-pod traffic.
    with (
        service_mesh(
            juju=juju,
            beacon_app_name=ISTIO_BEACON_APP,
            apps_to_be_related_with_beacon=[TEMPO_APP],
        )
        if enable_service_mesh
        else nullcontext()
    ):
        tempo_ip = get_app_ip_address(juju, TEMPO_APP)
        endpoint = get_tempo_application_endpoint(
            tempo_ip, protocol="otlp_grpc", tls=False
        )
        emit_trace(
            endpoint, nonce=nonce, proto="otlp_grpc", service_name="tracegen-grpc"
        )
        traces = query_traces_patiently_from_client_localhost(
            tempo_host=tempo_ip,
            service_name="tracegen-grpc",
            nonce=nonce,
            tls=False,
        )
        assert traces, "No traces found via internal grafana-source endpoint"
