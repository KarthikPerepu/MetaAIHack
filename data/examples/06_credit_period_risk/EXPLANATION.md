# Example 6: Credit Period Risk

## Scenario
On 10 Sep 2026, distributor Ganga Traders issued bill GT/1040 for ₹20,000.
The agreed credit period in the contract card is 30 days at 18% p.a. interest.
Due date: 10 Sep + 30 days = **10 Oct 2026**.
As-of report date: **5 Oct 2026**.

## Discrepancy
- Due date 10 Oct falls within 7 days of 5 Oct (window: [5 Oct, 12 Oct]).
- The bill is currently unpaid.
- Overdue interest formula: `bill_amount × (annual_rate / 365) × overdue_days`
- Projected interest if unpaid = ₹20,000 × (0.18 / 365) × 1 day overdue = **₹9.86** (rounded: ₹10).
- **Internal Action**: Reminder to shop owner to pay before 10 Oct to avoid penal interest.
