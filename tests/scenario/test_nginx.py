import logging
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from nginx_config import NginxConfig, _get_dns_ip_address
from tempo import Tempo

logger = logging.getLogger(__name__)
sample_dns_ip = "198.18.0.0"


def test_nginx_config_is_list_before_crossplane(context, nginx_container, coordinator):
    nginx = NginxConfig("localhost")
    prepared_config = nginx._prepare_config(coordinator)
    assert isinstance(prepared_config, List)


def test_nginx_config_is_parsed_by_crossplane(context, nginx_container, coordinator):
    nginx = NginxConfig("localhost")
    prepared_config = nginx.config(coordinator)
    assert isinstance(prepared_config, str)


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
def test_nginx_config_is_parsed_with_workers(context, nginx_container, coordinator, addresses):
    coordinator.cluster.gather_addresses_by_role.return_value = addresses

    nginx = NginxConfig("localhost")

    prepared_config = nginx.config(coordinator)
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
def test_nginx_config_contains_upstreams_and_proxy_pass(
    context, nginx_container, coordinator, addresses, tls, ipv6
):
    coordinator.cluster.gather_addresses_by_role.return_value = addresses
    coordinator.nginx.are_certificates_on_disk = tls

    with mock_ipv6(ipv6):
        with mock_resolv_conf(f"nameserver {sample_dns_ip}"):
            nginx = NginxConfig("localhost")

    prepared_config = nginx.config(coordinator)
    assert f"resolver {sample_dns_ip};" in prepared_config

    for role, addresses in addresses.items():
        for address in addresses:
            if role == "distributor":
                _assert_config_per_role(Tempo.receiver_ports, address, prepared_config, tls, ipv6)
            if role == "query-frontend":
                _assert_config_per_role(Tempo.server_ports, address, prepared_config, tls, ipv6)


def _assert_config_per_role(source_dict, address, prepared_config, tls, ipv6):
    # as entire config is in a format that's hard to parse (and crossplane returns a string), we look for servers,
    # upstreams and correct proxy/grpc_pass instructions.
    for port in source_dict.values():
        assert f"server {address}:{port};" in prepared_config
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
            assert f"set $backend grpc{'s' if tls else ''}://{sanitised_protocol}"
            assert "grpc_pass $backend" in prepared_config
        else:
            assert f"set $backend http{'s' if tls else ''}://{sanitised_protocol}"
            assert "proxy_pass $backend" in prepared_config


@contextmanager
def mock_resolv_conf(contents: str):
    with tempfile.NamedTemporaryFile() as tf:
        Path(tf.name).write_text(contents)
        with patch("nginx_config.RESOLV_CONF_PATH", tf.name):
            yield


@contextmanager
def mock_ipv6(enable: bool):
    with patch("nginx_config.is_ipv6_enabled", MagicMock(return_value=enable)):
        yield


@pytest.mark.parametrize(
    "mock_contents, expected_dns_ip",
    (
        (f"foo bar\nnameserver {sample_dns_ip}", sample_dns_ip),
        (f"nameserver {sample_dns_ip}\n foo bar baz", sample_dns_ip),
        (
            f"foo bar\nfoo bar\nnameserver {sample_dns_ip}\nnameserver 198.18.0.1",
            sample_dns_ip,
        ),
    ),
)
def test_dns_ip_addr_getter(mock_contents, expected_dns_ip):
    with mock_resolv_conf(mock_contents):
        assert _get_dns_ip_address() == expected_dns_ip


def test_dns_ip_addr_fail():
    with pytest.raises(RuntimeError):
        with mock_resolv_conf("foo bar"):
            _get_dns_ip_address()
