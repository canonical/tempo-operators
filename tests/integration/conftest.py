# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import random
import shutil
from contextlib import contextmanager
from pathlib import Path

import jubilant
import pytest
from jubilant import Juju
from pytest import fixture

from tests.integration.helpers import (
    ALL_ROLES,
    ALL_WORKERS,
    S3_APP,
    SSC_APP,
    TEMPO_APP,
    WORKER_APP,
    charm_and_channel_and_resources,
    deploy_distributed_cluster,
    deploy_monolithic_cluster,
)

logger = logging.getLogger(__name__)


def pytest_addoption(parser):
    group = parser.getgroup("jubilant-compat")
    group.addoption(
        "--keep-models",
        action="store_true",
        default=False,
        help="Alias for --no-juju-teardown (kept for backward compatibility).",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Map --keep-models to --no-juju-teardown before the plugin's hook runs."""
    if config.getoption("--keep-models"):
        config.option.no_juju_teardown = True


@fixture(scope="session")
def coordinator_charm():
    """Tempo coordinator used for integration testing."""
    return charm_and_channel_and_resources(
        "coordinator", "COORDINATOR_CHARM_PATH", "COORDINATOR_CHARM_CHANNEL"
    )


@fixture(scope="session")
def worker_charm():
    """Tempo worker used for integration testing."""
    return charm_and_channel_and_resources(
        "worker", "WORKER_CHARM_PATH", "WORKER_CHARM_CHANNEL"
    )


@contextmanager
def _tls_ctx(active: bool, juju: Juju, distributed: bool):
    """Context manager to set up TLS integration for tempo and the S3 backend."""
    if not active:  # a bit ugly, but nicer than using a nullcontext
        yield
        return

    logger.info("adding TLS")
    juju.deploy("self-signed-certificates", SSC_APP)
    juju.integrate(SSC_APP + ":certificates", TEMPO_APP + ":certificates")

    logger.info("waiting for active...")
    juju.wait(
        lambda status: jubilant.all_active(
            status, TEMPO_APP, *(ALL_WORKERS if distributed else (WORKER_APP,)), S3_APP
        ),
        timeout=2000,
        delay=5,
        successes=3,
    )
    logger.info("TLS ready")

    yield

    juju.remove_application(SSC_APP)


@pytest.fixture
def do_setup(pytestconfig):
    return not pytestconfig.getoption("--no-juju-setup")


@pytest.fixture
def do_teardown(pytestconfig):
    return not pytestconfig.getoption("--no-juju-teardown")


@contextmanager
def deployment_factory(tls, distributed, juju, do_setup, do_teardown):
    if do_setup:
        if distributed:
            logger.info("deploying distributed cluster...")
            deploy_distributed_cluster(juju, roles=ALL_ROLES)
        else:
            logger.info("deploying monolithic cluster...")
            deploy_monolithic_cluster(juju)
        logger.info("cluster deployed.")

    with _tls_ctx(tls, juju=juju, distributed=distributed):
        yield juju


@fixture(scope="module")
def copy_charm_libs_into_tester_charm():
    """Ensure the tester charm has the libraries it uses."""
    libraries = [
        "tls_certificates_interface/v4/tls_certificates.py",
        "tempo_coordinator_k8s/v0/charm_tracing.py",
        "tempo_coordinator_k8s/v0/tracing.py",
        "istio_beacon_k8s/v0/service_mesh.py",
    ]

    for lib in libraries:
        install_path = Path("tests/integration/tester/lib/charms") / lib
        install_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path("lib/charms") / lib, install_path)

    yield

    shutil.rmtree(Path("tests/integration/tester/lib"), ignore_errors=True)


@fixture(scope="module")
def copy_charm_libs_into_tester_grpc_charm():
    """Ensure the tester GRPC charm has the libraries it uses."""
    libraries = [
        "tempo_coordinator_k8s/v0/tracing.py",
        "istio_beacon_k8s/v0/service_mesh.py",
    ]

    for lib in libraries:
        install_path = Path("tests/integration/tester-grpc/lib/charms") / lib
        install_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path("lib/charms") / lib, install_path)

    yield

    shutil.rmtree(Path("tests/integration/tester-grpc/lib"), ignore_errors=True)


@fixture(scope="function")
def nonce():
    """Generate an integer nonce for easier trace querying."""
    return str(random.random())[2:]
