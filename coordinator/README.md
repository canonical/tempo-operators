# Tempo Operator

[![CharmHub Badge](https://charmhub.io/tempo-coordinator-k8s/badge.svg)](https://charmhub.io/tempo-coordinator-k8s)
[![Release](https://github.com/canonical/tempo-coordinator-k8s-operator/actions/workflows/release.yaml/badge.svg)](https://github.com/canonical/tempo-k8s-operator/actions/workflows/release.yaml)
[![Discourse Status](https://img.shields.io/discourse/status?server=https%3A%2F%2Fdiscourse.charmhub.io&style=flat&label=CharmHub%20Discourse)](https://discourse.charmhub.io)

This repository contains the source code for a Charmed Operator that drives [Tempo] on Kubernetes. It is destined to work together with [tempo-worker-k8s](https://charmhub.io/tempo-worker-k8s) to deploy and operate Tempo, a distributed tracing backend backed by Grafana. See [Tempo HA documentation](https://discourse.charmhub.io/t/charmed-tempo-ha/15531) for more details.

## Usage

Assuming you have access to a bootstrapped Juju controller on Kubernetes, you can:

```bash
$ juju deploy tempo-coordinator-k8s # --trust (use when cluster has RBAC enabled)
```

See [Deploy Tempo on top of COS-Lite](https://discourse.charmhub.io/t/tutorial-deploy-tempo-ha-on-top-of-cos-lite/15489) for the full tutorial.

## OCI Images

This charm, by default, deploys `ghcr.io/canonical/nginx:dev` and `nginx/nginx-prometheus-exporter:1.1.0`.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and the
[contributing] doc for developer guidance.

[Tempo]: https://grafana.com/traces/
[contributing]: https://github.com/canonical/tempo-coordinator-k8s-operator/blob/main/CONTRIBUTING.md
