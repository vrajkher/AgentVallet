# Workflow: invoice_total

1. For each line item, amount = qty × unit_price, rounded half-up to cents.
2. subtotal = Σ line amounts.
3. tax = subtotal × tax_rate, rounded to cents.
4. total = subtotal + tax.
5. Currency is passed through unchanged (ISO 4217 code).

Uses `Decimal` arithmetic to avoid binary floating-point rounding errors.
