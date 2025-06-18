#!/usr/bin/env bash
CHARM=$(charmcraft pack --format json | jq ".charms[0]" | tr -d '"')
REV=$(charmcraft upload $CHARM --format json | jq ".revision")
charmcraft release tempo-worker-k8s -r $REV --resource tempo-image:3 --channel latest/edge/dev

