# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
import functools
import json
import logging
import os
import random
import shutil
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from pytest import fixture
from pytest_operator.plugin import OpsTest

from tests.integration.helpers import get_relation_data

APP_NAME = "tempo"
SSC = "self-signed-certificates"
SSC_APP_NAME = "ssc"

logger = logging.getLogger(__name__)


class Store(defaultdict):
    def __init__(self):
        super(Store, self).__init__(Store)

    def __getattr__(self, key):
        """Override __getattr__ so dot syntax works on keys."""
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def __setattr__(self, key, value):
        """Override __setattr__ so dot syntax works on keys."""
        self[key] = value


store = Store()


def timed_memoizer(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        fname = func.__qualname__
        logger.info("Started: %s" % fname)
        start_time = datetime.now()
        if fname in store.keys():
            ret = store[fname]
        else:
            logger.info("Return for {} not cached".format(fname))
            ret = await func(*args, **kwargs)
            store[fname] = ret
        logger.info("Finished: {} in: {} seconds".format(fname, datetime.now() - start_time))
        return ret

    return wrapper


@fixture(scope="module")
@timed_memoizer
async def tempo_charm(ops_test: OpsTest):
    """Tempo charm used for integration testing.

    @timed_memoizer is used to cache the built charm and reuse it in all integration tests to save some minutes/hours.
    """
    count = 0
    # Intermittent issue where charmcraft fails to build the charm for an unknown reason.
    # Retry building the charm
    while True:
        try:
            charm = await ops_test.build_charm(".")
            return charm
        except RuntimeError:
            logger.warning("Failed to build Tempo coordinator. Trying again!")
            count += 1

            if count == 3:
                raise


@fixture(scope="module", autouse=True)
def copy_charm_libs_into_tester_charm(ops_test):
    """Ensure the tester charm has the libraries it uses."""
    libraries = [
        "observability_libs/v1/cert_handler.py",
        "tls_certificates_interface/v3/tls_certificates.py",
        "tempo_k8s/v1/charm_tracing.py",
        "tempo_k8s/v2/tracing.py",
    ]

    copies = []

    for lib in libraries:
        install_path = f"tests/integration/tester/lib/charms/{lib}"
        os.makedirs(os.path.dirname(install_path), exist_ok=True)
        shutil.copyfile(f"lib/charms/{lib}", install_path)
        copies.append(install_path)

    yield

    # cleanup: remove all libs
    for path in copies:
        Path(path).unlink()


@fixture(scope="module", autouse=True)
def copy_charm_libs_into_tester_grpc_charm(ops_test):
    """Ensure the tester GRPC charm has the libraries it uses."""
    libraries = [
        "tempo_k8s/v2/tracing.py",
    ]

    copies = []

    for lib in libraries:
        install_path = f"tests/integration/tester-grpc/lib/charms/{lib}"
        os.makedirs(os.path.dirname(install_path), exist_ok=True)
        shutil.copyfile(f"lib/charms/{lib}", install_path)
        copies.append(install_path)

    yield

    # cleanup: remove all libs
    for path in copies:
        Path(path).unlink()


@fixture(scope="function")
def server_cert(ops_test: OpsTest):
    data = get_relation_data(
        requirer_endpoint=f"{APP_NAME}/0:certificates",
        provider_endpoint=f"{SSC_APP_NAME}/0:certificates",
        model=ops_test.model.name,
    )
    cert = json.loads(data.provider.application_data["certificates"])[0]["certificate"]

    with tempfile.NamedTemporaryFile() as f:
        p = Path(f.name)
        p.write_text(cert)
        yield p


@fixture(scope="function")
def nonce():
    """Generate an integer nonce for easier trace querying."""
    return str(random.random())[2:]
