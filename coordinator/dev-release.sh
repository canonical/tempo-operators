#!/usr/bin/env bash
charmcraft clean
CHARM=$(charmcraft pack --format json | jq ".charms[0]" | tr -d '"')
REV=$(charmcraft upload $CHARM --format json | jq ".revision")
charmcraft release tempo-coordinator-k8s -r $REV --resource nginx-image:3 --resource nginx-prometheus-exporter-image:3 --channel latest/edge/dev

