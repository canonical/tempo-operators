# Contributing

This guide applies to both charms contained in this repository: ./coordinator and ./worker.

## Overview

This documents explains the processes and practices recommended for contributing enhancements to
this operator.

- Generally, before developing enhancements to this charm, you should consider [opening an issue
  ](https://github.com/canonical/tempo-operators/issues) explaining your use case.
- If you would like to chat with us about your use-cases or proposed implementation, you can reach
  us at the [Canonical Observability Matrix public channel](https://matrix.to/#/#cos:ubuntu.com)
  or on [Discourse](https://discourse.charmhub.io/).
- Familiarising yourself with the [Charmed Operator Framework](https://juju.is/docs/sdk) library
  will help you a lot when working on new features or bug fixes.
- All enhancements require review before being merged. Code review typically examines
  - code quality
  - test coverage
  - user experience for Juju administrators this charm.
- Please help us out in ensuring easy to review branches by rebasing your pull request branch onto
  the `main` branch. This also avoids merge commits and creates a linear Git commit history.

## Developing

You can use the environments created by `tox` for development:

```shell
tox --notest -e unit
source .tox/unit/bin/activate
```

### Container images

We are using the following images built by [oci-factory](https://github.com/canonical/oci-factory):
- `ubuntu/tempo`
  - [source](https://github.com/canonical/tempo-rock)
  - [dockerhub](https://hub.docker.com/r/ubuntu/tempo)
- `ubuntu/nginx`
  - [source](https://github.com/canonical/nginx-rock)
  - [dockerhub](https://hub.docker.com/r/ubuntu/nginx)
- `nginx/nginx-prometheus-exporter`
  - (upstream image) (WIP: https://github.com/canonical/nginx-prometheus-exporter-rock)
  - [dockerhub](https://hub.docker.com/r/nginx/nginx-prometheus-exporter)


### Testing

```shell
tox -e fmt           # update your code according to formatting rules
tox -e lint          # lint the codebase
tox -e unit          # run the unit testing suite
tox -e integration   # run the integration testing suite
tox                  # runs 'lint' and 'unit' environments
```

## Build charm

Build the charm in this git repository using:

```shell
cd ./worker; charmcraft pack
cd ./coordinator; charmcraft pack
```

This will create files like:
- `./worker/tempo-worker-k8s_ubuntu-24.04-amd64.charm`
- `./coordinator/tempo-coordinator-k8s_ubuntu-24.04-amd64.charm`


### Deploy

```bash
# Create a model
juju add-model dev
# Enable DEBUG logging
juju model-config logging-config="<root>=INFO;unit=DEBUG"
# Deploy the charms
juju deploy --trust \ 
    ./coordinator/tempo-coordinator-k8s_ubuntu-24.04-amd64.charm \
    --resource nginx-image=ghcr.io/canonical/nginx@sha256:6415a2c5f25f1d313c87315a681bdc84be80f3c79c304c6744737f9b34207993 \
    --resource nginx-prometheus-exporter-image=nginx/nginx-prometheus-exporter:1.1.0 tempo
    
juju deploy --trust \
    ./worker/tempo-worker-k8s_ubuntu-24.04-amd64.charm \
    --resource tempo-image=ghcr.io/canonical/oci-factory/tempo:2.7.1-24.04_5 tempo-worker
    
# alternatively, with jhack: https://github.com/canonical/jhack?tab=readme-ov-file#deploy
# cd ./coordinator; jhack deploy -- --trust 
# cd ./worker; jhack deploy -- --trust    
```

Tempo requires an integration with S3 to function.
To quickly get one up, run:
`./coordinator/scripts/deploy_minio.py`

And finally:
`juju relate tempo s3`