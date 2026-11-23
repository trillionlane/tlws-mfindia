"""Explicit v9-from-v5 task routing; existing allocated runtimes stay frozen."""

import re

from mfdataindia.enrichment import (
    book_runtime as legacy,
    book_sources,
    kotak_runtime,
    multi_facts,
    multi_runtime,
    sbi_runtime_v2,
    uti_runtime_v4,
)
from mfdataindia.review.policy import fingerprint


def execute(dsn, execution, *, task_index):
    with book_sources.select_registry(9):
        registry = book_sources.registry()
        if (
            not re.fullmatch(re.escape(multi_facts.JOB) + r"-[a-z0-9-]{1,40}", execution)
            or type(task_index) is not int
            or not 0 <= task_index < registry["maximum_sources_per_execution"]
        ):
            raise ValueError("registered v9 execution and task index required")
        if task_index < 2:
            result = multi_runtime.execute(dsn, execution, task_index=task_index)
            source_key = "retained-multi-source-" + str(task_index)
        else:
            source = registry["sources"][task_index - 2]
            result = {
                "absl-full-book-v1": legacy.absl,
                "sbi-full-book-v2": sbi_runtime_v2.run,
                "kotak-monthly-book-v1": kotak_runtime.run,
                "uti-english-monthly-book-v4": uti_runtime_v4.run,
            }[source["adapter"]](dsn, execution)
            source_key = source["key"]
        return {
            "format": "official-amc-book-task-v2",
            "execution_id": execution,
            "task_index": task_index,
            "source_key": source_key,
            "result": result,
            "status": "failed" if result["status"] == "failed" else "completed",
            "registry_hash": fingerprint(registry),
            "source_rows_changed": 0,
            "all_amc_data_complete": False,
        }
