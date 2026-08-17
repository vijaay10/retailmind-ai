# Quick start for a new company

Plain-English version. The engineering detail is in
[`company-onboarding.md`](company-onboarding.md).

---

## What do I give RetailMind?

**Required** — without these, nothing works:

| File | What it is |
|---|---|
| **Sales** | One row per line on a receipt or order: what sold, where, when, for how much |
| **Products** | Your product list: id, name, category, brand |
| **Stores** | Your store list: id, name, city, region |

**Recommended** — unlocks stock and supply insight:

| File | What it is |
|---|---|
| **Inventory** | Stock on hand per product per store |
| **Purchase orders** | What you have ordered from suppliers and when it is due |

**Optional** — adds context:

| File | What it is |
|---|---|
| **Fulfilment** | Deliveries and their timing |
| **Weather** | Daily observations, for correlating demand with conditions |

**CSV is the supported upload format today.** Not Excel, not a database
connection — export to CSV first.

**Your column names are fine.** If you call it `transaction_id` and we call it
`order_id`, that is handled — RetailMind recognises common variations and shows
you the match it made before anything is imported.

**We do not yet accept** returns, promotions, pricing, customer, or
targets/budgets files. There is no format for them.

---

## What happens?

1. **You upload.** One CSV per dataset.
2. **RetailMind checks the files.** It works out which dataset each file is,
   matches your column names to its own, and validates the contents.
3. **You review.** You see a plain report: how many records, how many are
   usable, what is wrong with the rest, and which column mapped to what.
4. **You confirm.** Nothing is imported until you say so. If too many rows have
   problems, RetailMind tells you *before* importing rather than after.
5. **RetailMind imports the data** into your company's own storage, separate
   from every other company's.
6. **Analytics become available** once the analytics layer is built over your
   imported data.

**This is batch, not real-time.** Everything is organised around a business
date. You upload a period of history; you do not stream live transactions.

**Re-uploading is safe.** Sending the same file twice changes nothing — it is
recognised and skipped. Sending a corrected file for the same dates *replaces*
those dates rather than adding a second copy.

---

## What do I get?

- **Analytics** — revenue, units, orders and margin, by day, store, region,
  category and channel, with period-over-period comparison.
- **Forecasts** — demand and revenue ahead, with a range rather than a single
  number, and a published record of how accurate past forecasts were.
- **Investigations** — when a number moves, a breakdown of which stores,
  regions, categories or channels actually account for it.
- **Recommendations** — ranked actions with the expected gain, how confident
  RetailMind is and why, what the outcome looks like if the assumption is
  wrong, and when *not* to act.
- **Decision intelligence** — accept or dismiss each recommendation; the
  decision is recorded.

---

## Today's limits, plainly

- Onboarding runs from the **command line**, not from the browser. The upload
  screen in the console does not yet perform the import.
- Creating a company is an **operator step**, not self-serve signup.
- **CSV only.**
- Building the analytics layer after import is a **separate command**, not
  automatic.

None of these change what the platform does with your data once it is in — they
are about how it gets there.
