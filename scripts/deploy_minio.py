#!/usr/bin/env python3
"""Very simple utility script to deploy and configure minio+s3 integrator for local testing."""

import json
import os
import shlex
import subprocess
from time import sleep

from minio import Minio


def get_status():
    cmd = "juju status --format json"
    raw = subprocess.getoutput(cmd)
    status = json.loads(raw)
    return status


def get_model_name(status):
    try:
        return status['model']['name']
    except KeyError:
        return None


def get_s3_status(status):
    try:
        return status['applications']['s3']['units']['s3/0']
    except KeyError:
        return None


def get_minio_status(status):
    try:
        return status['applications']['minio']['units']['minio/0']
    except KeyError:
        return None


def _run(cmd: str):
    return subprocess.check_call(shlex.split(cmd))

def _check_juju_status(status, name):
    return status and (status['juju-status']['current'] == name)

def _check_workload_status(status, name):
    return status and (status['workload-status']['current'] == name)


def deploy(bucket_name: str,
           model: str = None):
    if model:
        _run(f"juju switch {model}")

    _run(f"juju deploy minio --channel edge --trust --config access-key=accesskey --config secret-key=secretkey")
    _run("juju deploy s3-integrator --channel edge --trust s3")

    print("waiting for minio to become active...", end='')
    while True:
        status = get_status()
        minio = get_minio_status(status)
        try:
            if _check_juju_status(get_s3_status(status), 'idle') and _check_workload_status(minio, "active"):
                break
        except KeyError:
            pass

        print('.', end='')
        sleep(1)

    print("minio and s3 integrator ready.")

    minio_addr = minio['address']

    mc_client = Minio(
        f"{minio_addr}:9000",
        access_key="accesskey",
        secret_key="secretkey",
        secure=False,
    )

    # create tempo bucket
    found = mc_client.bucket_exists(bucket_name)
    if not found:
        mc_client.make_bucket(bucket_name)

    # configure s3-integrator
    model_name = get_model_name(status)
    _run(f"juju config s3 endpoint=minio-0.minio-endpoints.{model_name}.svc.cluster.local:9000 bucket={bucket_name}")
    _run(f"juju run s3/0 sync-s3-credentials access-key=accesskey secret-key=secretkey")


if __name__ == '__main__':
    deploy(
        bucket_name=os.getenv("MINIO_ENDPOINT", "tempo"),
        model=os.getenv("MINIO_MODEL", None),
    )
