#!/usr/bin/env bash

# util for running a single integration test module locally
export COORDINATOR_CHARM_PATH=./coordinator/tempo-coordinator-k8s_ubuntu@24.04-amd64.charm
export WORKER_CHARM_PATH=./worker/tempo-worker-k8s_ubuntu@24.04-amd64.charm

if [[ $# -eq 0 ]] ; then
    echo "usage: ./tests/integration/run_tests.sh test_foo [PYTEST_JUBILANT_OPTIONS]"
    echo "for example: ./tests/integration/run_tests.sh test_self_monitoring --model bar --no-teardown --switch"
    exit 0
fi

echo "RUNNING:" tox -e integration -- -k $1 "${@:2}"
tox -e integration -- -k $1 "${@:2}"