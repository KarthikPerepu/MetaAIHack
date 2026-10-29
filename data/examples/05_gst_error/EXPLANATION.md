# Example 5: GST Error

## Scenario
On 8 Oct 2026, distributor Bengal Confectionery (BC) bills 20 packs of Cadbury Dairy Milk under HSN 1704.
The bill charges GST @ 28% instead of the statutory GST rate of 18% for sugar confectionery (HSN 1704).
Total taxable value: ₹10,000.
GST charged @ 28% = ₹2,800.
Correct GST @ 18% = ₹1,800.

## Discrepancy
- Incorrect GST slab applied (28% instead of 18%).
- Excess tax charged: ₹2,800 - ₹1,800 = **₹1,000**.
- **Claim amount**: Excess tax charged = **₹1,000**.

## Special Case: Cannot Determine
If the bill items or rate list lack HSN codes entirely, the harness outputs `cannot_determine` asking the distributor for HSN details.
