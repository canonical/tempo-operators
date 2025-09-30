import json
import shlex
import subprocess
from pathlib import Path

import jubilant
import pytest
import requests
from pytest_jubilant import pack, get_resources
import yaml
from jubilant import Juju

from helpers import WORKER_APP, deploy_monolithic_cluster, TEMPO_APP
from tempo import Tempo
from tests.integration.helpers import get_traces_patiently, get_app_ip_address

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
    # Given a fresh build of the charm
    # When deploying it together with testers
    # Then applications should eventually be created
    deploy_monolithic_cluster(juju)


@pytest.mark.setup
# scaling the coordinator before ingesting traces to verify that scaling won't stop traces ingestion.
def test_scale_up_tempo(juju: Juju):
    # GIVEN we scale up tempo
    juju.add_unit(TEMPO_APP, num_units=2)
    # THEN all units become active
    juju.wait(
        lambda status: jubilant.all_active(status, TEMPO_APP, WORKER_APP), timeout=1000
    )


@pytest.mark.setup
def test_relate(juju: Juju):
    # given a deployed charm
    # when relating it together with the tester
    # then relation should appear
    juju.integrate(TEMPO_APP + ":tracing", TESTER_APP_NAME + ":tracing")
    juju.integrate(TEMPO_APP + ":tracing", TESTER_GRPC_APP_NAME + ":tracing")

    juju.wait(
        lambda status: jubilant.all_active(
            status, TEMPO_APP, WORKER_APP, TESTER_APP_NAME, TESTER_GRPC_APP_NAME
        ),
        timeout=1000,
    )


def test_verify_traces_http(juju: Juju):
    # given a relation between charms
    # when traces endpoint is queried
    # then it should contain traces from the tester charm
    app_ip = get_app_ip_address(juju, TEMPO_APP)
    traces = get_traces_patiently(
        tempo_host=app_ip, service_name="TempoTesterCharm", tls=False
    )
    assert traces, (
        f"There's no trace of charm exec traces in tempo. {json.dumps(traces, indent=2)}"
    )


@pytest.mark.skip(reason="fails because search query results are not stable")
# keep an eye on https://github.com/grafana/tempo/issues/3777 and see if they fix it
def test_verify_buffered_charm_traces_http(juju: Juju):
    # given a relation between charms
    # when traces endpoint is queried
    # then it should contain all traces from the tester charm since the setup phase, thanks to the buffer
    traces = get_traces_patiently(
        tempo_host=get_app_ip_address(juju, TEMPO_APP),
        service_name="TempoTesterCharm",
        tls=False,
    )

    # charm-tracing trace names are in the format:
    # "mycharm/0: <event-name> event"
    captured_events = {trace["rootTraceName"].split(" ")[1] for trace in traces}
    expected_setup_events = {
        "start",
        "install",
        "leader-elected",
        "tracing-relation-created",
        "replicas-relation-created",
    }
    assert expected_setup_events.issubset(captured_events)


def test_verify_traces_grpc(juju: Juju):
    # the tester-grpc charm emits a single grpc trace in its common exit hook
    # we verify it's there
    traces = get_traces_patiently(
        tempo_host=get_app_ip_address(juju, TEMPO_APP),
        service_name="TempoTesterGrpcCharm",
        tls=False,
    )
    assert traces, (
        f"There's no trace of generated grpc traces in tempo. {json.dumps(traces, indent=2)}"
    )


def test_verify_only_requested_receiver_endpoints_listed(juju: Juju):
    # requested receivers are listed
    expect_open = ["otlp_grpc", "otlp_http", "jaeger_thrift_http"]
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
    expect_open = [
        "otlp_grpc",
    ]
    tempo_ip = get_app_ip_address(juju, TEMPO_APP)
    tempo_worker_ip = get_app_ip_address(juju, WORKER_APP)

    for proto in ("otlp_http", "jaeger_thrift_http"):
        # these status codes mean there is something listening, but we have the wrong url, which is ok
        listening_server_status_codes = {404, 415}
        port = f":{Tempo.receiver_ports[proto]}"
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

    expect_closed = ["zipkin", "jaeger_grpc"]
    for proto in expect_closed:
        port = f":{Tempo.receiver_ports[proto]}"

        # we can't connect to tempo
        with pytest.raises(requests.exceptions.ConnectionError):
            requests.get("http://" + tempo_ip + port, timeout=0.5)

        # or the worker
        with pytest.raises(requests.exceptions.ConnectionError):
            requests.get("http://" + tempo_worker_ip + port, timeout=0.5)


@pytest.mark.teardown
def test_remove_relation(juju: Juju):
    # given related charms
    # when relation is removed
    # then both charms should become active again
    juju.remove_relation(TEMPO_APP + ":tracing", TESTER_APP_NAME + ":tracing")
    juju.remove_relation(TEMPO_APP + ":tracing", TESTER_GRPC_APP_NAME + ":tracing")

    juju.wait(lambda status: status.apps[TEMPO_APP].is_active, timeout=1000)

    (
        juju.wait(
            lambda status: jubilant.all_active(status, TEMPO_APP),
            error=lambda status: jubilant.any_blocked(status, TEMPO_APP),
            timeout=1000,
        ),
    )
    # for tester, depending on the result of race with tempo it's either waiting or active
    juju.wait(
        lambda status: status.apps[TEMPO_APP].is_active,
        timeout=1000,
        error=lambda status: jubilant.any_blocked(
            status, TESTER_APP_NAME, TESTER_GRPC_APP_NAME
        ),
    )
