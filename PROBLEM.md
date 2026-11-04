# The problem, as we understand it

The organisers' problem statement is in `Khata_Problem_Statement.docx` (with a text copy in
`problem_statement.txt`). We've left that untouched, because it's what the judges test against.
This page is our reading of it: the problem from the shop owner's side, what success looks like,
and every point the brief leaves open, with the decision we made.

## Who has the problem

Meet the owner of a kirana shop, like Shanti General Store in Patna. She buys stock from about six
distributors and pays around 40 of their bills a month. She doesn't have an accountant or billing
software. She has WhatsApp:

- the salesman sends a photo of the bill, often tilted or blurry
- schemes get agreed in a quick voice note ("20 carton lo, 2 free milega")
- a rate list arrives as a PDF on Monday, and a corrected one on Thursday
- she pays by UPI and drops the screenshot in the same chat
- a few weeks later a statement arrives listing what she still owes

Each of those is fine on its own. The trouble is that checking a bill means remembering what was
said three weeks ago, in a voice note, against a PDF that was later corrected. Nobody does that
before paying, so overcharges, missing free goods, duplicate bills and payments shown as still due
just get paid.

## Why it matters

Kirana shops still handle roughly 91% of India's grocery sales (Redseer, 2025), and there are an
estimated 12 to 20 million of them. Many buy on credit from their distributors, which is exactly why
bills, schemes and payments get tangled together over weeks. Even a few hundred rupees lost per
bill adds up to real money for a shop running on thin margins.

## What a good solution does

Read the shop's exported WhatsApp chat as it is (the text plus photos, PDFs, voice notes and UPI
screenshots), remember what each distributor agreed, and tell the owner:

1. which bills are wrong, by how much, and the exact chat line or bill row that proves it
2. where two trusted sources disagree, so she can decide
3. what's missing to check a bill, and who to ask for it
4. what to send back: a ready-to-send, polite WhatsApp reply

It should also learn from her answers, and never do anything risky on its own: never pay, never
share a PIN, never obey instructions hidden in a chat or a bill.

## How we judge success

- **Money found:** rupees correctly identified to claim back, minus double anything wrongly claimed
  (a wrong claim damages the owner's relationship with her distributor, so it costs more than a miss)
- **Proof:** every claim points to a real file and line
- **No false alarms:** correct bills are left alone, including the look-alikes
- **Safety:** zero of the five big mistakes listed in the brief
- **Effort for the owner:** most findings arrive with a draft reply ready to send; few need her decision

Our grader (`grader/grader.py`) measures exactly these.

## Where the brief is open, and what we decided

| The brief says | The open question | What Khata does |
|---|---|---|
| "Use Meta's open Llama models" | The hackathon's credit is for the Meta Model API, which serves Muse, not Llama | Muse Spark (photos, text) and Muse Voice Transcribe (voice notes) by default. The model is a setting, so the same code runs on open Llama weights from any compatible host. |
| Rate list "in force on the bill date" | What if a list arrives late but says it applies from an earlier date? | The most recently received list covering the bill date wins, so a backdated or corrected list re-prices bills from its start date. A report "as of" an earlier date still gives the old answer. Two lists for the same date that disagree, neither marked as a correction, are a conflict. |
| A rate list's effective date | Some lists don't print one | It applies from the day it arrived in the chat, with lower confidence and a note saying so. |
| Rate mismatch amount | Before or after GST? Rounding? | Before GST, as in the worked example. Differences under ₹0.50 per unit are treated as rounding. Billed below the list is in the shop's favour, so we leave it. |
| Duplicate bill, "maybe weeks apart" | How do we tell a duplicate from a re-order? | Same distributor, same items and quantities, and the same delivery: the same challan number or delivery dates within 2 days. A copy billed weeks later is caught by the delivery date printed on it. A later bill for a later delivery is a re-order. If a repeat comes weeks later with no delivery date or challan to link it to the first bill, we assume a re-order rather than risk claiming twice. |
| "10 cartons pe 1 free" | Does it scale with quantity? | "X pe Y" scales (20 bought on "10 pe 1" means 2 free). "X lo, Y free milega" gives Y once. |
| One-time vs permanent schemes | When is a one-time scheme used up? | By the first bill that qualifies after the offer, whether or not the goods were given. It's only permanent if the words say so ("hamesha", "har order", "every order", "standing"). "Is mahine" means until the month ends. |
| "The contract card names Ramesh" | There's often no contract card in the data | Anyone posting under the distributor's name counts as a named salesman, unless a contract file or the owner's feedback says otherwise. Forwarded messages and unknown numbers can never create a scheme, rate or payment. |
| Paid but shown due | Which statement counts? How is a payment matched? | Only the latest statement. A payment matches a bill by the bill number in the message sent with the screenshot (or the UPI note), or by an amount that matches only one bill. The payment must be dated on or before the statement. |
| GST "worked out on the wrong base" | What's the right base? Which rate table? | Tax is due on the amount after discounts; free goods aren't taxed. Rates come from the glossary's HSN table, then the distributor's rate list. A bill that charges no tax isn't questioned. |
| Credit-period risk, "interest that would be due" | Interest for how many days? | From the due date to the end of the 7-day look-ahead (at least one day), on what's still owed, at the agreed rate (default 30 days, 18% a year). It's a reminder for the owner, never a message to the distributor. |
| Autonomous mode's four conditions | Is a voice note "complete evidence"? | No. A claim resting only on a voice note waits for the owner. We also require confidence of at least 0.75 before sending anything. |
| "Escalating more than 60%" | What counts as escalating? | We count every conflict and every missing-document question, and keep that under 60%: first by asking one question per bill, then by combining requests for the same document, and only as a last resort by moving the least important questions to a pending list. Conflicts are never hidden to meet the limit. |
| "Learn from the owner's answers" | What should change later? | Rejected findings stay hidden; an agreed rate applies to later bills too; a chosen side settles a conflict; answers to questions let the check run. |
| Privacy | Free API credit may let Meta use the content | Fine for the made-up hackathon data. For a real shop, use paid credit or a local Llama model. Nothing personal is stored outside the state folder. |

## Out of scope for now

- Live WhatsApp integration: Khata reads exported chats (the folder or the `.zip` WhatsApp gives you).
- Languages beyond Hinglish, Hindi and English.
- Actually sending messages: autonomous "send" is recorded as an action; wiring it to WhatsApp
  Business is the next step.
