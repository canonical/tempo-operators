import logging

import jubilant
import pytest
import requests
from jubilant import Juju

from tests.integration.helpers import (
    WORKER_APP,
    api_endpoints,
    emit_trace,
    get_tempo_ingestion_endpoint,
    get_traces_patiently,
    protocols_endpoints,
    TEMPO_APP,
    get_tempo_hostname,
)


logger = logging.getLogger(__name__)


def test_force_enable_protocols(juju: Juju):
    config = {
        f"always_enable_{protocol}": "True"
        for protocol in list(protocols_endpoints.keys())
    }

    juju.config(TEMPO_APP, config)
    juju.wait(
        lambda status: jubilant.all_active(status, TEMPO_APP, WORKER_APP),
        error=jubilant.any_error,
        timeout=2000,
        # wait for an idle period
        delay=5,
        successes=3,
    )


@pytest.mark.parametrize("protocol", protocols_endpoints.keys())
def test_receivers_ingestion(juju: Juju, nonce, protocol, tls, ingress):
    if protocol == "jaeger_thrift_http":
        pytest.skip(
            "SSL error on jaeger_thrift_http: "
            "see https://github.com/canonical/tempo-coordinator-k8s-operator/issues/176"
        )
        # this early-exits, but for clarity:
        return
    tempo_host = get_tempo_hostname(juju, ingress)
    logger.info(f"emitting & verifying trace using {protocol} protocol.")
    tempo_endpoint = get_tempo_ingestion_endpoint(
        tempo_host,
        protocol=protocol,
        tls=tls,
    )

    # emit a trace
    service_name = f"tracegen{'-tls' if tls else ''}-{protocol}"
    emit_trace(
        tempo_endpoint,
        juju,
        nonce=nonce,
        verbose=1,
        proto=protocol,
        use_cert=tls,
        service_name=service_name,
    )
    # verify it's been ingested
    get_traces_patiently(tempo_host, service_name=service_name, nonce=nonce, tls=tls)


@pytest.mark.parametrize(
    "protocol",
    # test all ports on the coordinator
    set(protocols_endpoints.keys()).union(api_endpoints.keys()),
)
def test_plain_request_redirect(juju: Juju, protocol, tls, ingress):
    if not tls:
        pytest.skip("this test only makes sense if we have tls")
    if "grpc" in protocol:
        pytest.skip("there's no simple way to run this test with a gRPC client")

    tempo_host = get_tempo_hostname(juju, ingress)
    tempo_endpoint = get_tempo_ingestion_endpoint(
        tempo_host, protocol=protocol, tls=False
    )
    req = requests.get(
        tempo_endpoint,
        verify=False,
        allow_redirects=False,
    )
    # Permanent Redirect codes
    assert req.status_code == 301 or req.status_code == 308
