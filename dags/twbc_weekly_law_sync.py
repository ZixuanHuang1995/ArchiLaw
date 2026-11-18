"""Weekly corpus sync.

Airflow is optional. The task calls the same function as ``twbc ingest``.
A cron entry is equivalent and does not need Airflow installed::

    0 18 * * 5  cd /opt/twbc-rag && uv run twbc ingest --refresh

Friday 18:00 UTC is after the MOJ bulk file's usual refresh window.
Move the schedule if their publication day changes. This file targets
Airflow 2.x (``airflow.operators.python.PythonOperator``).
"""

from __future__ import annotations

from datetime import datetime, timedelta


def run_weekly_ingest() -> None:
    """Refresh the MOJ cache and ingest every pcode in config/laws.yaml."""
    from twbc_rag.ingest.pipeline import ingest_configured

    report = ingest_configured(refresh=True, fetch_pages=True, force=False)
    if report.missing_pcodes:
        joined = ", ".join(report.missing_pcodes)
        raise RuntimeError(f"Ingestion missed pcodes: {joined}")


try:
    from airflow import DAG
    from airflow.operators.python import PythonOperator
except ImportError:  # pragma: no cover - Airflow is not required locally or in CI
    DAG = None  # type: ignore[misc, assignment]
    PythonOperator = None  # type: ignore[misc, assignment]


if DAG is not None and PythonOperator is not None:
    with DAG(
        dag_id="twbc_weekly_law_sync",
        description="Refresh the Taiwan building-code corpus from the MOJ open API.",
        default_args={
            "owner": "twbc",
            "retries": 1,
            "retry_delay": timedelta(minutes=20),
        },
        start_date=datetime(2026, 1, 1),
        schedule="0 18 * * 5",
        catchup=False,
        tags=["twbc", "ingestion"],
    ) as dag:
        PythonOperator(task_id="ingest_configured_laws", python_callable=run_weekly_ingest)
