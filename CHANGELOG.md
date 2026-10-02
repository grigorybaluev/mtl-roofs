# Changelog

## 0.1.0 (2026-10-02)


### Features

* **geometry:** clip each footprint's points and keep only its roof ([#58](https://github.com/grigorybaluev/mtl-roofs/issues/58)) ([840414a](https://github.com/grigorybaluev/mtl-roofs/commit/840414a8d38cca3b34d2a5f5c62273fa521f81c5))
* **geometry:** make plane detection hold up on real roofs ([#60](https://github.com/grigorybaluev/mtl-roofs/issues/60)) ([4593dff](https://github.com/grigorybaluev/mtl-roofs/commit/4593dffa6b8e9df013632329cec2acb111749532))
* **geometry:** refine roof planes jointly with a topology solver ([#70](https://github.com/grigorybaluev/mtl-roofs/issues/70)) ([5af9eb8](https://github.com/grigorybaluev/mtl-roofs/commit/5af9eb8008f27cb814f22fff0e885322dedfc8cf))
* **ingest:** fetch the footprint layer with the rest of a study area ([#54](https://github.com/grigorybaluev/mtl-roofs/issues/54)) ([e8ea44b](https://github.com/grigorybaluev/mtl-roofs/commit/e8ea44b7d37c533dc4a5c16623e7dc512a6abd38))
* **ingest:** load footprints and the reference model into PostGIS ([#68](https://github.com/grigorybaluev/mtl-roofs/issues/68)) ([acb8683](https://github.com/grigorybaluev/mtl-roofs/commit/acb8683ae7213d320fbc2e8ce2ee96fb55b6b1e3))
* **ingest:** verify every fetched artefact against a pinned checksum ([#48](https://github.com/grigorybaluev/mtl-roofs/issues/48)) ([47d2e01](https://github.com/grigorybaluev/mtl-roofs/commit/47d2e019ddba9d5e808b0665ab3123992d17d4a5))


### Documentation

* pick issues by milestone and labels, not a project board ([#59](https://github.com/grigorybaluev/mtl-roofs/issues/59)) ([51877cf](https://github.com/grigorybaluev/mtl-roofs/commit/51877cf0b53ba31f516e6270fbac0fbdd9db09c2))
* reconstruct per footprint, score per reference building (ADR 0005) ([#55](https://github.com/grigorybaluev/mtl-roofs/issues/55)) ([5db9eef](https://github.com/grigorybaluev/mtl-roofs/commit/5db9eef302f505ce3810d4ffea8406b7eb32b093))
* specify the topology solver before implementing it (ADR 0006) ([#69](https://github.com/grigorybaluev/mtl-roofs/issues/69)) ([5eefb97](https://github.com/grigorybaluev/mtl-roofs/commit/5eefb97e4628300d4852685e8d27d3294cfaab56))

## Changelog

This file is managed by [release-please](https://github.com/googleapis/release-please)
from Conventional Commit messages. Do not edit it by hand — an edit will be overwritten
by the next release PR.
