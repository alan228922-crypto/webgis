# Third-party software and attribution

Third-party terms apply to the individual components identified below. Project-wide public distribution and licensing are pending a separate rights decision.

| Component | Candidate use and terms |
| --- | --- |
| Leaflet 1.9.4 | BSD-2-Clause. Local JavaScript, CSS and upstream marker and layer-control images are distributed with the upstream notice. [Included license](home/static/home/vendor/leaflet/LICENSE) · [Upstream license](https://github.com/Leaflet/Leaflet/blob/v1.9.4/LICENSE) |
| Leaflet-Geoman Free 2.18.0 | MIT. Local JavaScript and CSS are distributed with the upstream notice. [Included license](home/static/home/vendor/geoman/LICENSE) · [Upstream license](https://github.com/geoman-io/leaflet-geoman/blob/v2.18.0/LICENSE) |
| hls.js 1.6.16 | Apache-2.0. The optional non-Demo CCTV interface references a fixed release on a CDN. No hls.js bundle is included. [Upstream license](https://github.com/video-dev/hls.js/blob/v1.6.16/LICENSE) |

The offline bundle records the source versions and npm archive integrity for the vendored mapping libraries. [Vendor version and integrity record](home/static/home/vendor/README.md)

Python packages are installed separately under their upstream terms. This source candidate includes dependency specifications. Distribution of wheels or bundled native GIS libraries would require a separate inventory of their notices and terms. System fonts are selected through CSS. No font file is bundled.

Outside Demo mode, the map can reference OSM, CARTO and Esri services. Their respective attribution and service terms apply when used. The offline demonstration uses no third-party basemap tiles or previews. The geological demonstration uses a local mock with synthetic geometry. Official geological imagery, GIS records and agency marks are outside the candidate. [OSM tile policy](https://operations.osmfoundation.org/policies/tiles/)
