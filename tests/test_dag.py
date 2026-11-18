import importlib.util

from twbc_rag.paths import REPO_ROOT


def test_weekly_dag_imports_without_airflow() -> None:
    path = REPO_ROOT / "dags" / "twbc_weekly_law_sync.py"
    spec = importlib.util.spec_from_file_location("twbc_weekly_law_sync", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.run_weekly_ingest)
