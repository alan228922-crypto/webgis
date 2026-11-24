from pathlib import Path
import sqlite3
import statistics
import tempfile
from time import perf_counter

from shapely.geometry import Point, Polygon, box

from worksite_gis.data_pipeline.spatial_index import _create_schema, query_bbox


with tempfile.TemporaryDirectory(prefix="worksite-index-benchmark-") as directory:
    index = Path(directory) / "synthetic.sqlite3"
    window = (121.008,24.998,121.012,25.002)
    donut = Polygon(box(121,24.99,121.02,25.01).exterior.coords,
                    [box(121.003,24.993,121.017,25.007).exterior.coords])
    with sqlite3.connect(index) as db:
        _create_schema(db)
        for i in range(5200):
            geometry = donut if i < 4999 else Point(121.01,25)
            db.execute("INSERT INTO risk_features VALUES(?, 'bench', 'L', ?, ?, ?, '{}')",
                       (i+1,i,geometry.wkb,geometry.wkb))
            x0,y0,x1,y1 = geometry.bounds
            db.execute("INSERT INTO risk_features_rtree VALUES(?,?,?,?,?)",(i+1,x0,x1,y0,y1))
    timings=[]
    for _ in range(7):
        t=perf_counter()
        results,quality=query_bbox(index,"bench",window,5,with_quality=True)
        timings.append((perf_counter()-t)*1000)
    assert quality["candidate_count_scanned"]==5000
    assert quality["scan_incomplete"] and quality["exact_match_count"] == 1 and results[0]["id"] == 4999
    print(f"Synthetic RTree benchmark: 5200 candidates; 5000 scanned; {len(results)} shown; "
          f"median {statistics.median(timings):.1f} ms, range {min(timings):.1f}–{max(timings):.1f} ms (7 runs).")
