# Example 1: rate mismatch

On 3 Oct 2026, Ganga Traders sends rate list v2. Sunrise Glucose Biscuit drops from ₹410 to ₹395 a
carton, starting 3 Oct.

On 9 Oct, bill GT/1187 charges 20 cartons at the old ₹410.

The rate valid on 9 Oct is ₹395, so the bill is ₹15 a carton too high: ₹15 × 20 = **₹300** to claim back.

**The look-alike:** bill GT/1179 from 2 Oct also charges ₹410, for 10 cartons. That one is fine,
because v1 (₹410) was still valid on 2 Oct. Flagging it would mean we'd only learned "₹410 is wrong"
instead of "check the rate on the bill's date".
