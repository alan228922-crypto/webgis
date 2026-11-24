from pathlib import Path

from worksite_gis.data_pipeline.cleanup import plan_cleanup


def test_cleanup_plan_keeps_unvalidated_source():
    root = Path(".test-artifacts")
    validated = root / "validated.shp"
    failed = root / "failed.txt"
    root.mkdir(exist_ok=True)
    validated.write_text("shape", encoding="utf-8")
    plan = plan_cleanup(root, [validated, failed])
    assert validated.resolve() in plan.remove
    assert failed.resolve() in plan.keep
