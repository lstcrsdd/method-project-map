"""Report export formats."""

from __future__ import annotations

import csv
import io
import json
from typing import List

from tallyhub.core.registry import REPORT_EXPORTERS
from tallyhub.reports.sales import SalesRow

COLUMNS = ["day", "currency", "orders", "gross", "refunded", "net", "tax"]


def _row(row: SalesRow) -> dict:
    return {
        "day": row.day,
        "currency": row.currency,
        "orders": row.orders,
        "gross": str(row.gross.amount),
        "refunded": str(row.refunded.amount),
        "net": str(row.net.amount),
        "tax": str(row.tax.amount),
    }


@REPORT_EXPORTERS.register("csv")
def export_csv(rows: List[SalesRow]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow(_row(row))
    return buffer.getvalue()


@REPORT_EXPORTERS.register("json")
def export_json(rows: List[SalesRow]) -> str:
    return json.dumps([_row(r) for r in rows], indent=2)


def export(rows: List[SalesRow], fmt: str) -> str:
    return REPORT_EXPORTERS.get(fmt)(rows)
