"""Custom checks for invoice_total: total = subtotal + tax (to the cent)."""

import json
import sys


def validate(inp, out):
    errors = []
    if abs(out["subtotal"] + out["tax"] - out["total"]) > 0.005:
        errors.append("total must equal subtotal + tax")
    if len(out["lines"]) != len(inp["items"]):
        errors.append("one output line per input item")
    return errors


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    errs = validate(payload["input"], payload["output"])
    print(json.dumps({"passed": not errs, "errors": errs}))
