# tempo-operators

This repository hosts two charms following the [coordinated-workers](https://discourse.charmhub.io/t/cos-lite-docs-managing-deployments-of-cos-lite-ha-addons/15213) pattern.
Together, they deploy and operate Tempo, a distributed tracing backend by Grafana labs.
 
The charm in `./coordinator` deploys and operates a configurator charm and an nginx instance responsible for routing traffic to the worker nodes.

The charm in `./worker` deploys and operates one or multiple roles of Tempo's distributed architecture.

