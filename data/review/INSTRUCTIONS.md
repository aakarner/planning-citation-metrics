# Reviewing the two spreadsheets

Two Excel files need a person to work through them. Both follow the same
conventions, and each has a **"How to use this"** tab with the full detail —
read that tab before starting each one.

## Conventions for both sheets

- **Only type in the yellow cells.** Everything else is evidence, pulled from
  the data; leave it alone.
- Use the **dropdowns exactly**; don't type variants like "Assoc Prof".
- **Leave a row blank** if you can't decide. Blank means "not yet", and is
  always better than a guess. If you looked and still can't tell, say so in
  Notes.
- Put your **initials in "Reviewed by"** and **today's date in "Date"** on every
  row you decide. Notes is free text and is kept.
- **Never delete or reorder rows.** Filtering and sorting are fine.
- Save as `.xlsx` under the **same filename**.
- Work **Band A first**, then B, then C (and D). Bands are ordered easiest and
  most valuable first, so partial progress is still useful.
- You are only reading public web pages (Google Scholar profiles, university
  staff pages). Don't contact anyone.

---

## 1. `openalex_review.xlsx` — which OpenAlex record is theirs?

**75 people, 218 rows.** Each person has several candidate OpenAlex author
records; most are either namesakes or fragments of the same person. Your job
is to say which one is the person's *main* record.

For each person:

1. Click the **OpenAlex** link on each of their rows and compare it with what
   we know: their name, program ("Department"), and our citation figure
   ("Our figure").
2. Mark **at most one** row `accepted` — the record that is clearly this
   person and holds most of their work. Mark the rest `rejected`.
3. If none of the candidates is them, reject them all.
4. `Suggested = best match` is the computer's guess — check it, don't trust it.

Things that help: **Inst = 1** means the record's institution matches their
program. **Ratio** is the candidate's citations divided by ours; OpenAlex
normally counts *less* than Google Scholar, so a ratio well under 1 is normal
and does not mean the wrong person. A ratio far *above* 1 usually does.

Rough time: about 2 minutes per person.

---

## 2. `rank_review.xlsx` — is this person's rank still right?

**139 people, one row each.** We hold each of them as an assistant or
associate professor and have for seven years or more, so a promotion may have
happened without our knowing. Some will turn out not to be tenure-line faculty
at all.

For each row:

1. Click **Scholar profile** and **Program website** and find the person's
   current title. The staff or faculty page of the program is the best
   source; the Scholar profile's own affiliation line ("Scholar profile
   says") is second best.
2. Pick the title in **Actual rank**. If the page gives the year they were
   promoted, put it in **Since (year)**.
3. Paste the address of the page you used in **Where you saw it**.

The dropdown options:

| Option | Use when |
|---|---|
| assistant / associate / full | their tenure-line rank today — including when it matches "Rank on file"; that confirmation is useful |
| emeritus | retired with the title |
| adjunct or lecturer | any non-tenure-line post: lecturer, instructor, professor of practice, adjunct, research or clinical |
| left the program | no longer there — note where they went if it's shown |
| unclear | you looked and couldn't tell; say why in Notes |

**Pct in rank** is the person's citation count as a percentile among people we
hold at the same rank. Something like 0.95 for an "assistant" is a strong hint
the rank is out of date.

Rough time: about 3 minutes per row.

---

## When you're done

Send both files back (or put them in the shared folder). Don't run anything
yourself — importing the decisions is a one-line step on our side, and a
second import can't double-apply, so partial batches are fine to hand in.
