import logging
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tempo import Tempo
from coordinated_workers.nginx import NginxConfig
from nginx_config import upstreams, server_ports_to_locations

logger = logging.getLogger(__name__)
sample_dns_ip = "198.18.0.0"


@pytest.mark.parametrize(
    "addresses",
    (
        {},
        {
            "distributor": {"1.2.3.5"},
            "ingester": {"1.2.3.6"},
            "querier": {"1.2.4.7"},
            "query_frontend": {"1.2.5.1"},
            "compactor": {"1.2.6.6"},
            "metrics_generator": {"1.2.8.4"},
        },
        {
            "distributor": {"1.2.3.5"},
            "ingester": {"1.2.3.6"},
            "querier": {"1.2.4.7"},
            "query_frontend": {"1.2.5.1"},
            "compactor": {"1.2.6.6"},
            "metrics_generator": {"1.2.8.4"},
        },
        {
            "distributor": {"1.2.3.5"},
            "ingester": {"1.2.3.6"},
            "querier": {"1.2.4.7"},
            "query_frontend": {"1.2.5.1"},
            "compactor": {"1.2.6.6"},
            "metrics_generator": {"1.2.8.4"},
        },
        {
            "distributor": {"1.2.3.5", "1.2.3.7"},
            "ingester": {"1.2.3.6", "1.2.3.8"},
            "querier": {"1.2.4.7", "1.2.4.9"},
            "query_frontend": {"1.2.5.1", "1.2.5.2"},
            "compactor": {"1.2.6.6", "1.2.6.7"},
            "metrics_generator": {"1.2.8.4", "1.2.8.5"},
        },
    ),
)
def test_nginx_config_is_parsed_with_workers(
    context, nginx_container, coordinator, addresses
):
    coordinator.cluster.gather_addresses_by_role.return_value = addresses
    requested_protocols = {"otlp_grpc", "zipkin"}
    nginx = NginxConfig(
        "localhost",
        upstreams(requested_protocols),
        server_ports_to_locations(requested_protocols),
    )

    prepared_config = nginx.get_config(
        coordinator.cluster.gather_addresses_by_role(), False
    )
    assert isinstance(prepared_config, str)


@pytest.mark.parametrize("ipv6", (True, False))
@pytest.mark.parametrize(
    "addresses",
    (
        {
            "distributor": {"1.2.3.5"},
            "ingester": {"1.2.3.6"},
            "querier": {"1.2.4.7"},
            "query_frontend": {"1.2.5.1"},
            "compactor": {"1.2.6.6"},
            "metrics_generator": {"1.2.8.4"},
        },
        {
            "distributor": {"1.2.3.5"},
            "ingester": {"1.2.3.6"},
            "querier": {"1.2.4.7"},
            "query_frontend": {"1.2.5.1"},
            "compactor": {"1.2.6.6"},
            "metrics_generator": {"1.2.8.4"},
        },
    ),
)
@pytest.mark.parametrize("tls", (True, False))
@pytest.mark.parametrize(
    "requested_protos",
    (
        {"otlp_grpc"},
        {"otlp_grpc", "otlp_http"},
        {"otlp_grpc", "zipkin", "jaeger_thrift_http"},
    ),
)
def test_nginx_config_contains_upstreams_and_proxy_pass(
    context, nginx_container, coordinator, addresses, tls, ipv6, requested_protos
):
    coordinator.cluster.gather_addresses_by_role.return_value = addresses
    coordinator.nginx.are_certificates_on_disk = tls

    with mock_ipv6(ipv6):
        with mock_resolv_conf(f"nameserver {sample_dns_ip}"):
            nginx = NginxConfig(
                "localhost",
                upstreams(requested_protos),
                server_ports_to_locations(requested_protos),
            )

    prepared_config = nginx.get_config(
        coordinator.cluster.gather_addresses_by_role(), tls
    )
    assert f"resolver {sample_dns_ip};" in prepared_config

    for role, addresses in addresses.items():
        for address in addresses:
            if role == "distributor":
                enabled_routes = {
                    k: v
                    for k, v in Tempo.receiver_ports.items()
                    if k in requested_protos
                }
                disabled_routes = {
                    k: v
                    for k, v in Tempo.receiver_ports.items()
                    if k not in requested_protos
                }
                _assert_config_per_role(
                    enabled_routes, address, prepared_config, tls, ipv6
                )
                _assert_not_config_per_role(
                    disabled_routes, address, prepared_config, tls, ipv6
                )

            if role == "query-frontend":
                _assert_config_per_role(
                    Tempo.server_ports, address, prepared_config, tls, ipv6
                )


def _assert_config_per_role(source_dict, address, prepared_config, tls, ipv6):
    # as entire config is in a format that's hard to parse (and crossplane returns a string), we look for servers,
    # upstreams and correct proxy/grpc_pass instructions.
    for port in source_dict.values():
        assert f"server {address}:{port} resolve;" in prepared_config
        assert f"listen {port}" in prepared_config
        assert (
            (f"listen [::]:{port}" in prepared_config)
            if ipv6
            else (f"listen [::]:{port}" not in prepared_config)
        )

    for protocol in source_dict.keys():
        sanitised_protocol = protocol.replace("_", "-")
        assert f"upstream {sanitised_protocol}" in prepared_config

        if "grpc" in protocol:
            assert (
                f"set $backend grpc{'s' if tls else ''}://{sanitised_protocol}"
                in prepared_config
            )
            assert "grpc_pass $backend" in prepared_config
        else:
            assert (
                f"set $backend http{'s' if tls else ''}://{sanitised_protocol}"
                in prepared_config
            )
            assert "proxy_pass $backend" in prepared_config


def _assert_not_config_per_role(source_dict, address, prepared_config, tls, ipv6):
    for port in source_dict.values():
        assert f"server {address}:{port} resolve;" not in prepared_config
        assert f"listen {port}" not in prepared_config
        assert f"listen [::]:{port}" not in prepared_config

    for protocol in source_dict.keys():
        sanitised_protocol = protocol.replace("_", "-")
        assert f"upstream {sanitised_protocol}" not in prepared_config

        if "grpc" in protocol:
            assert (
                f"set $backend grpc{'s' if tls else ''}://{sanitised_protocol}"
                not in prepared_config
            )
        else:
            assert (
                f"set $backend http{'s' if tls else ''}://{sanitised_protocol}"
                not in prepared_config
            )


@contextmanager
def mock_resolv_conf(contents: str):
    with tempfile.NamedTemporaryFile() as tf:
        Path(tf.name).write_text(contents)
        with patch("coordinated_workers.nginx.RESOLV_CONF_PATH", tf.name):
            yield


@contextmanager
def mock_ipv6(enable: bool):
    with patch(
        "coordinated_workers.nginx.is_ipv6_enabled", MagicMock(return_value=enable)
    ):
        yield
