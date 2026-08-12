# Changelog

Changes on `track/2.10` since the common ancestor with `track/2` (`8c4fdd3`).

## Features

- feat(tf): base input variable ([#381](https://github.com/canonical/tempo-operators/pull/381)) (#383)
- feat: add charms blueprint ([#363](https://github.com/canonical/tempo-operators/pull/363))
- feat: bump to 26.04 ([#339](https://github.com/canonical/tempo-operators/pull/339))
- feat(terraform): Support for Juju provider v2 ([#332](https://github.com/canonical/tempo-operators/pull/332))
- feat: TF resources variable ([#327](https://github.com/canonical/tempo-operators/pull/327))
- feat: TF service mesh outputs ([#323](https://github.com/canonical/tempo-operators/pull/323))
- feat(terraform): add channel validation ([#310](https://github.com/canonical/tempo-operators/pull/310))
- feat: add juju doctor probe ([#283](https://github.com/canonical/tempo-operators/pull/283))
- feat: rework testing process in monorepo ([#270](https://github.com/canonical/tempo-operators/pull/270))

## Fixes

- fix(tf): validate for correct track name ([#384](https://github.com/canonical/tempo-operators/pull/384))
- fix(charm_tracing): use public API for Python version compatibility ([#356](https://github.com/canonical/tempo-operators/pull/356))
- fix: correct invalid LogQL expression in tempo-operational dashboard ([#351](https://github.com/canonical/tempo-operators/pull/351))
- fix: update docs link ([#335](https://github.com/canonical/tempo-operators/pull/335))
- fix: invalid IPv6 host when S3 endpoint uses default ports ([#318](https://github.com/canonical/tempo-operators/pull/318))
- fix(terraform): update tfdocs and add comment ([0308999](https://github.com/canonical/tempo-operators/commit/030899985b03d4111db4ff466c7e6726711e379b))
- fix: Split TF endpoints output to requires/provides ([#274](https://github.com/canonical/tempo-operators/pull/274))
- fix: use localhost for querier frontend_address when all queriers co-locate with query-frontend ([#304](https://github.com/canonical/tempo-operators/pull/304))
- fix: make service-mesh and require-cmr-mesh optional ([#297](https://github.com/canonical/tempo-operators/pull/297))
- fix: coordinator scrape jobs ([#259](https://github.com/canonical/tempo-operators/pull/259))

## Others

- chore: update terraform-docs ([915de46](https://github.com/canonical/tempo-operators/commit/915de4691c8edc2248fd49e1b7859e544215575c))
- chore(blueprints): refresh charms.just ([4f74f63](https://github.com/canonical/tempo-operators/commit/4f74f63fd05e99812a57d7903eda5230ff6a7a10))
- chore: refresh charms.just from canonical/observability ([2ac0904](https://github.com/canonical/tempo-operators/commit/2ac0904cf52a67414199f787c9195a0a5e4f1e1c))
- chore: remove unused grafana-source v0 library ([2047c55](https://github.com/canonical/tempo-operators/commit/2047c5596e40323f9ddd13340dde52079929e17f))
- ci: adjust terraform tagging and trigger release ([9e38d95](https://github.com/canonical/tempo-operators/commit/9e38d95791af8cc35278b9acfac2383c461a7de0))
- chore: trigger release ci ([5e3a18f](https://github.com/canonical/tempo-operators/commit/5e3a18fb81707a369d3f4f59178dbc1874a39a01))
- chore: upgrade grafana_source library to v1 for stable datasource UIDs ([#364](https://github.com/canonical/tempo-operators/pull/364))
- chore: update charm libraries ([#359](https://github.com/canonical/tempo-operators/pull/359))
- chore: bump coordinated workers to 4.1.2 ([#360](https://github.com/canonical/tempo-operators/pull/360))
- chore: update charm libraries ([#347](https://github.com/canonical/tempo-operators/pull/347))
- chore: bump coordinated workers ([#357](https://github.com/canonical/tempo-operators/pull/357))
- chore: update charm libraries ([#344](https://github.com/canonical/tempo-operators/pull/344))
- chore: update charm libraries ([#343](https://github.com/canonical/tempo-operators/pull/343))
- chore: update charm libraries ([#338](https://github.com/canonical/tempo-operators/pull/338))
- chore: update charm libraries ([#336](https://github.com/canonical/tempo-operators/pull/336))
- chore: update charm libraries ([#319](https://github.com/canonical/tempo-operators/pull/319))
- chore: Remove tester charms, use tracegen for testing tracing ([#320](https://github.com/canonical/tempo-operators/pull/320))
- chore: update charm libraries ([#317](https://github.com/canonical/tempo-operators/pull/317))
- chore: upgrade coordinated-workers to 4.0.0 ([#315](https://github.com/canonical/tempo-operators/pull/315))
- docs: improve charmcraft.yaml description fields ([#307](https://github.com/canonical/tempo-operators/pull/307))
- chore: bump lockfiles ([#306](https://github.com/canonical/tempo-operators/pull/306))
- chore: update charm libraries ([#300](https://github.com/canonical/tempo-operators/pull/300))
- chore: update charm libraries ([#298](https://github.com/canonical/tempo-operators/pull/298))
- chore: update charm libraries ([#279](https://github.com/canonical/tempo-operators/pull/279))
- chore(deps): lock file maintenance ([#212](https://github.com/canonical/tempo-operators/pull/212))
- chore: join tempo projects in tiobe configuration ([#255](https://github.com/canonical/tempo-operators/pull/255))
- chore: deprecate charm_tracing ([#262](https://github.com/canonical/tempo-operators/pull/262))
- feature: add service mesh support ([#210](https://github.com/canonical/tempo-operators/pull/210))
- chore: update charm libraries ([#243](https://github.com/canonical/tempo-operators/pull/243))

