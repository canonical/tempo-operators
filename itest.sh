#!/usr/bin/env bash
# util for running a single integration test module locally in development

root="$(dirname $(realpath -s $0))"
export COORDINATOR_CHARM_PATH="${root}/coordinator/tempo-coordinator-k8s_ubuntu@24.04-amd64.charm"
export WORKER_CHARM_PATH="${root}/worker/tempo-worker-k8s_ubuntu@24.04-amd64.charm"

if [[ $# -eq 0 ]] ; then
    echo "usage: ./tests/integration/run_tests.sh [coordinator|worker] [PYTEST_ARGS]"
    echo "for example: ./tests/integration/run_tests.sh -k test_self_monitoring --model bar --no-teardown --switch"
    echo "for example: ./tests/integration/run_tests.sh coordinator -k test_self_tracing --model foo"
    exit 0
fi

if [[ $1 == "coordinator" ]] ; then
  echo $1
  echo "RUNNING (from ./coordinator):" tox -e integration -- "${@:2}"
  pushd ./coordinator
  tox -e integration -- "${@:2}"
  popd
elif [[ $1 == "worker" ]] ; then
  pushd ./worker
  echo "RUNNING (from ./worker):" tox -e integration -- "${@:2}"
  tox -e integration -- "${@:2}"
  popd
else
  # $1 could very well be "-k", so we need to pass it forward
  echo "RUNNING (from ./):" tox -e integration -- "${@:1}"
  tox -e integration -- "${@:1}"
fi
