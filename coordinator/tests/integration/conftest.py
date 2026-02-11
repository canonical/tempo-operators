# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import os
import random
import shlex
import shutil
from subprocess import check_output

from pytest import fixture

from tests.integration.helpers import _get_tempo_charm

SSC_APP_NAME = "ssc"

logger = logging.getLogger(__name__)


@fixture(scope="session")
def tempo_charm():
    """Tempo charm used for integration testing.

    Build once per session and reuse it in all integration tests to save some minutes/hours.
    You can also set `CHARM_PATH` env variable to use an already existing built charm.
    """
    return _get_tempo_charm()


@fixture(scope="module", autouse=True)
def copy_charm_libs_into_tester_charm():
    """Ensure the tester charm has the libraries it uses."""
    libraries = [
        "tls_certificates_interface/v4/tls_certificates.py",
        "tempo_coordinator_k8s/v0/charm_tracing.py",
        "tempo_coordinator_k8s/v0/tracing.py",
        "istio_beacon_k8s/v0/service_mesh.py",
    ]

    copies = []

    for lib in libraries:
        install_path = f"tests/integration/tester/lib/charms/{lib}"
        os.makedirs(os.path.dirname(install_path), exist_ok=True)
        shutil.copyfile(f"lib/charms/{lib}", install_path)
        copies.append(install_path)

    yield

    # cleanup: remove all libs
    check_output(shlex.split("rm -rf ./tests/integration/tester/lib"))


@fixture(scope="module", autouse=True)
def copy_charm_libs_into_tester_grpc_charm():
    """Ensure the tester GRPC charm has the libraries it uses."""
    libraries = [
        "tempo_coordinator_k8s/v0/tracing.py",
        "istio_beacon_k8s/v0/service_mesh.py",
    ]

    copies = []

    for lib in libraries:
        install_path = f"tests/integration/tester-grpc/lib/charms/{lib}"
        os.makedirs(os.path.dirname(install_path), exist_ok=True)
        shutil.copyfile(f"lib/charms/{lib}", install_path)
        copies.append(install_path)

    yield

    # cleanup: remove all libs
    check_output(shlex.split("rm -rf ./tests/integration/tester-grpc/lib"))


@fixture(scope="function")
def nonce():
    """Generate an integer nonce for easier trace querying."""
    return str(random.random())[2:]
