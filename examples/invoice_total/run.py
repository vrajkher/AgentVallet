"""Compute invoice subtotal, tax and total from line items.

Contract: stdin JSON {"currency", "tax_rate", "items": [{"sku", "qty", "unit_price"}]} -> stdout JSON.
"""

import json
import sys
from decimal import ROUND_HALF_UP, Decimal


def money(x: Decimal) -> float:
    return float(x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def run(data: dict) -> dict:
    lines = []
    for item in data["items"]:
        amount = Decimal(str(item["qty"])) * Decimal(str(item["unit_price"]))
        lines.append({"sku": item["sku"], "amount": money(amount)})
    subtotal = sum(Decimal(str(line["amount"])) for line in lines)
    tax = subtotal * Decimal(str(data.get("tax_rate", 0)))
    return {
        "currency": data["currency"],
        "lines": lines,
        "subtotal": money(subtotal),
        "tax": money(tax),
        "total": money(subtotal + Decimal(str(money(tax)))),
    }


if __name__ == "__main__":
    print(json.dumps(run(json.load(sys.stdin))))
