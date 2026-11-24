# Reproducible engineering geology screening

**Synthetic Data · Mock GeologyCloud · Preliminary screening**

Can an engineer reproduce a screening result for the same published worksite after panning or zooming the map? This case fixes a 10,000 m² polygon and a 50 m search buffer, then compares the implementation with independent geometry calculations. The five input layers are synthetic engineering boreholes, liquefaction areas, dip slopes, rockfall features and debris-flow tracks.

## Inputs and method

1. Load the project's selected published snapshot. Keep viewport exploration separate from this fixed analysis.
2. Read GeoJSON in EPSG:4326 longitude–latitude order. Project to EPSG:3826 for the metric computation. Reproject the buffered envelope for a conservative candidate query.
3. Test intersection with the original worksite polygon. Include candidates whose shortest projected distance to the worksite is at most 50 m.
4. Intersect each qualifying liquefaction polygon with the worksite and union the intersections before computing the area ratio. If the analysis set is incomplete or administrative coverage is unknown, return `null`.
5. In the local spatial index, SQLite RTree selects envelope candidates and Shapely refines them against original WKB. The display limit is applied after exact filtering. The candidate scan budget can independently mark the result as incomplete.

[Input fixtures](fixtures.json) · [Fixture provenance and SHA-256](manifest.json) · [Independent expected results](expected.json)

## Independent reference

| Quantity | Expected result |
| --- | --- |
| Worksite | 100 m × 100 m = 10,000 m² |
| Boreholes | 0, 30, 50 and 51 m from the worksite. The first three are within the 50 m search buffer. |
| Liquefaction | The two intersecting features contribute 5,000 and 2,500 m² with 1,250 m² counted in both. Union area is **6,250 m²**, or **62.5%**. A feature touching only the boundary adds 0 m². |
| Remaining layers | A dip-slope polygon contains the worksite, a rockfall feature lies 15 m from it, and a debris-flow track touches its boundary. |
| Unknown completeness | Liquefaction area and percentage are `null`. |

The independent reference uses axis-aligned rectangle intersections and distances. The case checks EPSG:3826 against three control points computed with an independent GRS80 transverse Mercator series. The stated tolerances are 3 cm for the control points, 1 μm for the distance boundary, 0.02 m² for area, and 10⁻⁶ for the ratio. These tolerances apply to the synthetic case.

## Run the case offline

Install dependencies first. From an activated Python 3.13 environment in the repository root, run:

```sh
python docs/cases/synthetic-geological-screening/run.py
node docs/cases/synthetic-geological-screening/check-ui.mjs
PYTHONPATH=. python docs/cases/synthetic-geological-screening/benchmark.py
```

The runner uses isolated SQLite, Mock GeologyCloud and local fixtures. The benchmark generates 5,200 synthetic candidates for a scan budget of 5,000 and checks that an exact hit and the incomplete-scan flag can coexist. It measures this workload on the current host.

## Interpretation

The case supports reproducibility of a preliminary screening method with known inputs. Site-specific conditions, production coverage, upstream pagination and publication dates, and engineering safety require independent evidence. A successful empty response describes the returned candidate set. It cannot establish complete coverage on its own.

[Demo and role walkthrough](../../demo-runtime.md) · [Validation scope and exact exclusions](../../validation-summary.md)
