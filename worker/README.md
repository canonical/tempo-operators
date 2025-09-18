# Tempo Worker charm for Kubernetes

[![CharmHub Badge](https://charmhub.io/tempo-worker-k8s/badge.svg)](https://charmhub.io/tempo-worker-k8s)
[![Release](https://github.com/canonical/tempo-worker-k8s-operator/actions/workflows/release.yaml/badge.svg)](https://github.com/canonical/tempo-worker-k8s-operator/actions/workflows/release.yaml)
[![Discourse Status](https://img.shields.io/discourse/status?server=https%3A%2F%2Fdiscourse.charmhub.io&style=flat&label=CharmHub%20Discourse)](https://discourse.charmhub.io)

## Description

The Tempo Worker charm provides a scalable long-term storage using [Tempo](https://github.com/grafana/tempo).
This charm is part of the Tempo HA deployment, and is meant to be deployed together with the [tempo-coordinator-k8s](https://github.com/canonical/tempo-coordinator-k8s-operator) charm.

A Tempo Worker can assume any role that the Tempo binary can take on, and it should always be related to a Tempo Coordinator.

## OCI Images

This charm by default uses the latest release of the [ubuntu/tempo](https://hub.docker.com/r/ubuntu/tempo/) image.
