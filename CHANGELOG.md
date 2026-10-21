# Changelog

## 0.1.0 (2026-10-21)


### Features

* **evaluation:** assign reference buildings and score vertical RMSE and coverage ([#80](https://github.com/grigorybaluev/mtl-roofs/issues/80)) ([114bc42](https://github.com/grigorybaluev/mtl-roofs/commit/114bc421a3dd2da1f89d4fecba6fc0b0e7d86738))
* **evaluation:** evaluate a study area and write the breakdown report ([#86](https://github.com/grigorybaluev/mtl-roofs/issues/86)) ([06a1fe0](https://github.com/grigorybaluev/mtl-roofs/commit/06a1fe0075373073f2c165f9c8ff513e21e9e0ea))
* **evaluation:** plane orientation error, plane matching and face-count agreement ([#81](https://github.com/grigorybaluev/mtl-roofs/issues/81)) ([ba6ba5c](https://github.com/grigorybaluev/mtl-roofs/commit/ba6ba5c66a8ab3d41eb8aab7eb2041ba10bd2362))
* **evaluation:** render the failure gallery with a diagnosis per building ([#87](https://github.com/grigorybaluev/mtl-roofs/issues/87)) ([94025f2](https://github.com/grigorybaluev/mtl-roofs/commit/94025f2eb7aad6265a9ba49db85dd48a36d094bd))
* **geometry:** batch-reconstruct a study area with per-footprint status logging ([#85](https://github.com/grigorybaluev/mtl-roofs/issues/85)) ([94c68df](https://github.com/grigorybaluev/mtl-roofs/commit/94c68df3ee2b329c44908f9937b90558a353e8ee))
* **geometry:** clip each footprint's points and keep only its roof ([#58](https://github.com/grigorybaluev/mtl-roofs/issues/58)) ([840414a](https://github.com/grigorybaluev/mtl-roofs/commit/840414a8d38cca3b34d2a5f5c62273fa521f81c5))
* **geometry:** make plane detection hold up on real roofs ([#60](https://github.com/grigorybaluev/mtl-roofs/issues/60)) ([4593dff](https://github.com/grigorybaluev/mtl-roofs/commit/4593dffa6b8e9df013632329cec2acb111749532))
* **geometry:** partition the roof into faces and extrude a watertight LOD2 solid ([#78](https://github.com/grigorybaluev/mtl-roofs/issues/78)) ([5a07a3c](https://github.com/grigorybaluev/mtl-roofs/commit/5a07a3c3b6d1b110fd661d56528b694511f90d2d))
* **geometry:** refine roof planes jointly with a topology solver ([#70](https://github.com/grigorybaluev/mtl-roofs/issues/70)) ([5af9eb8](https://github.com/grigorybaluev/mtl-roofs/commit/5af9eb8008f27cb814f22fff0e885322dedfc8cf))
* **ingest:** fetch the footprint layer with the rest of a study area ([#54](https://github.com/grigorybaluev/mtl-roofs/issues/54)) ([e8ea44b](https://github.com/grigorybaluev/mtl-roofs/commit/e8ea44b7d37c533dc4a5c16623e7dc512a6abd38))
* **ingest:** load footprints and the reference model into PostGIS ([#68](https://github.com/grigorybaluev/mtl-roofs/issues/68)) ([acb8683](https://github.com/grigorybaluev/mtl-roofs/commit/acb8683ae7213d320fbc2e8ce2ee96fb55b6b1e3))
* **ingest:** verify every fetched artefact against a pinned checksum ([#48](https://github.com/grigorybaluev/mtl-roofs/issues/48)) ([47d2e01](https://github.com/grigorybaluev/mtl-roofs/commit/47d2e019ddba9d5e808b0665ab3123992d17d4a5))


### Bug Fixes

* **ci:** make eval-smoke run the CLI and fail when it can't measure ([#76](https://github.com/grigorybaluev/mtl-roofs/issues/76)) ([36914d4](https://github.com/grigorybaluev/mtl-roofs/commit/36914d46ed66b75e4ac911bb5abedf5c08488a56))
* **geometry:** put the floor at the downhill ground, not the median ([#88](https://github.com/grigorybaluev/mtl-roofs/issues/88)) ([1a7e700](https://github.com/grigorybaluev/mtl-roofs/commit/1a7e700652093c1cb1c0394f0d15ff0eae2db431))
* **geometry:** split saddle vertices so every solid is a manifold ([#89](https://github.com/grigorybaluev/mtl-roofs/issues/89)) ([714ac18](https://github.com/grigorybaluev/mtl-roofs/commit/714ac18b609ecb07e4e3850c666f0f1220ae3950))
* **ingest:** load multi-tile areas with tile-local groupe ids and degenerate rings ([#93](https://github.com/grigorybaluev/mtl-roofs/issues/93)) ([83656e1](https://github.com/grigorybaluev/mtl-roofs/commit/83656e1ea34fbaf1611fc878a25cdf523ac01c25))


### Documentation

* **evaluation:** add an error status for exceptions in a batch run ([#82](https://github.com/grigorybaluev/mtl-roofs/issues/82)) ([7794c02](https://github.com/grigorybaluev/mtl-roofs/commit/7794c02c71ca2ef7b105e0f1b725e298af587510))
* **evaluation:** fix sampling, aggregation and the temporal flag before implementing them ([#79](https://github.com/grigorybaluev/mtl-roofs/issues/79)) ([a6801bc](https://github.com/grigorybaluev/mtl-roofs/commit/a6801bce4869374c9acd9a2117d14a13ddc48f6b))
* pick issues by milestone and labels, not a project board ([#59](https://github.com/grigorybaluev/mtl-roofs/issues/59)) ([51877cf](https://github.com/grigorybaluev/mtl-roofs/commit/51877cf0b53ba31f516e6270fbac0fbdd9db09c2))
* reconstruct per footprint, score per reference building (ADR 0005) ([#55](https://github.com/grigorybaluev/mtl-roofs/issues/55)) ([5db9eef](https://github.com/grigorybaluev/mtl-roofs/commit/5db9eef302f505ce3810d4ffea8406b7eb32b093))
* specify the topology solver before implementing it (ADR 0006) ([#69](https://github.com/grigorybaluev/mtl-roofs/issues/69)) ([5eefb97](https://github.com/grigorybaluev/mtl-roofs/commit/5eefb97e4628300d4852685e8d27d3294cfaab56))

## Changelog

This file is managed by [release-please](https://github.com/googleapis/release-please)
from Conventional Commit messages. Do not edit it by hand — an edit will be overwritten
by the next release PR.
