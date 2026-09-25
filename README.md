# NAFDAC Regulatory Assistant (rRx)

**Ask a question in plain English. Get a verified answer straight from NAFDAC's official website — with the source link attached.**

NAFDAC (Nigeria's National Agency for Food and Drug Administration and Control) publishes some of the most important safety and compliance information in the country: product recalls, counterfeit alerts, withdrawn drugs, blacklisted and watchlisted companies, field safety notices for medical devices, drug registration records, fees, and regulatory guidelines.

The problem is that none of it is searchable in one place. Alerts sit in dated cards across multiple tabs. Withdrawn-product lists run to hundreds of rows in a single table. Many notices exist only as PDFs. And the Greenbook — NAFDAC's official register of approved drugs — isn't even a normal web page; it's a search form that only returns results after you submit it in a browser.

This Actor solves that. It reads the live NAFDAC website the way a person would — filling in search forms, opening tabs, following PDF links — and turns what it finds into a direct, sourced answer to your question in seconds.

---

## Table of contents

- [What it can answer](#what-it-can-answer)
- [Who this is for](#who-this-is-for)
- [Why not just use ChatGPT or Google?](#why-not-just-use-chatgpt-or-google)
- [How to use it](#how-to-use-it)
- [Input parameters](#input-parameters)
- [What you get back](#what-you-get-back)
- [Example outputs](#example-outputs)
- [What makes the answers trustworthy](#what-makes-the-answers-trustworthy)
- [Automating it: schedules, webhooks & the API](#automating-it-schedules-webhooks--the-api)
- [Good to know before you run it](#good-to-know-before-you-run-it)
- [Pricing](#pricing)
- [FAQ](#faq)
- [Support & feedback](#support--feedback)

---

## What it can answer

The Actor understands questions about every major category NAFDAC publishes on, including:

| Category | Example question |
|---|---|
| **Public alerts & recalls** | "What are the latest public alerts?" |
| **Drug/product registration** | "Is Aspirin Cardio approved by NAFDAC?" |
| **Withdrawn products** | "List products withdrawn from the market in 2025" |
| **Blacklisted companies** | "Is [Company Name] on the NAFDAC blacklist?" |
| **Watchlisted companies** | "Which companies are on the current watchlist?" |
| **Field safety notices** | "Are there any field safety notices for infusion pumps?" |
| **Fees & tariffs** | "What is the NAFDAC registration fee for a new drug?" |
| **Guidelines & regulations** | "What are the labelling requirements for cosmetics?" |
| **Chemicals, pesticides & narcotics** | "Is [chemical name] a restricted substance?" |
| **General agency info** | "Who is the NAFDAC Director General?" |

You can also ask for a specific number of results — "top 5 latest recalls", "last 10 withdrawn products" — and the Actor will honor that count.

You don't need to know which section of nafdac.gov.ng holds the answer, or that the Greenbook exists as a separate search tool at all. Just ask the question naturally, in the way you'd ask a colleague.

## Who this is for

- **Pharmacies, hospitals & procurement teams** — confirm a product hasn't been withdrawn or flagged before you stock or dispense it.
- **Importers, distributors & manufacturers** — check a supplier or product against the blacklist and watchlist before committing to a deal, or confirm your own product's registration status is current.
- **Compliance & regulatory affairs teams** — verify registration details, check fees, and pull the latest guidelines without manually digging through the NAFDAC site.
- **Journalists & researchers** — trace the history of recalls, alerts, and enforcement actions, with working source links for every claim you publish.
- **Consumers & patients** — quickly check whether a warning has been issued about a product you or your family use.
- **Developers, startups & platforms** — plug live, sourced NAFDAC data into an app, inventory system, procurement tool, or compliance dashboard via the Apify API.

## Why not just use ChatGPT or Google?

General-purpose AI tools were trained on data that goes stale, and NAFDAC's alerts, withdrawals, and registrations change continuously. A chatbot answering from memory has no way to know about a recall published last week — or, worse, may guess and sound confident while doing it.

Google gets you to the right page eventually, but not to the answer: you still have to click through tabs, scroll long tables, and open PDFs by hand, and there's no way to search across alerts, withdrawals, and the Greenbook at once.

This Actor is different because:

- **It reads the live site on every run.** You always get today's data, not a snapshot from months ago.
- **It reaches the parts of the site a search engine can't index** — the Greenbook's search-only database, JavaScript-rendered tabs, and content buried inside PDFs.
- **Every answer is sourced.** You get a direct link to the exact NAFDAC page or document the answer came from, so you (or your compliance team, or your readers) can verify it in one click.
- **Structured answers aren't watered down by AI paraphrasing.** When NAFDAC has published an actual record — a registration, an alert, a row in a table — that's exactly what you get back, not a summary that might drop a detail that matters.

## How to use it

1. Open the Actor and click **Start** (or call it via the API — see below).
2. Type your question into the **Question** field, in plain language.
3. Run the Actor. Within moments you'll get back:
   - A direct, human-readable answer.
   - The full underlying record(s) it was built from — e.g. the complete Greenbook entry, the matching table rows, or the alert details.
   - Links to every NAFDAC source page or document the answer relied on.

That's the entire workflow for a one-off question. The optional settings below exist for people who want more control, but you can safely ignore all of them for everyday use.

## Input parameters

| Field | Required? | What it does |
|---|---|---|
| **Question** | Yes | Your question, in plain English. This is the only field you need to fill in. |
| **Maximum results** | No | Caps how many records come back for list-style answers (e.g. alerts, withdrawn products). Leave blank to let the Actor use the number in your question ("top 5") or a sensible default. |
| **Default results per answer** | No | A fallback count used only when your question has no number in it and Maximum results is blank — e.g. set this to 20 so "latest recalls" always returns 20 unless you specifically ask for a different number. |
| **Choose sections automatically** | No (on by default) | Lets the Actor decide which parts of the NAFDAC site to check based on your question. Recommended for almost everyone — turn this off only if you want to restrict the search to specific pages you provide yourself. |
| **Extra start URLs** | No | Additional NAFDAC pages to check, on top of whatever the Actor selects automatically. Useful for advanced or very specific searches. |
| **AI model** | No | Which language model is used for the small number of questions that have no direct record to answer from (e.g. "What does the labelling guideline require?"). The default works well for most people. |
| **Max pages to crawl** | No | An upper limit on how many pages the Actor is allowed to visit in one run. Raise it for very broad questions; lower it for a faster, cheaper run. |
| **Crawl depth** | No | How many links deep the Actor follows from its starting pages. |
| **Page timeout** | No | How long to wait for a single page to load before giving up on it. |
| **Save debug HTML snapshots** | No (off by default) | Stores a copy of each page's raw HTML for troubleshooting. Only useful if an answer looks wrong and you want to inspect what the Actor actually saw. |

## What you get back

Every run produces one clear answer record containing:

- **`answer`** — a plain-language response to your question.
- **`answerDetail`** — the full structured record(s) behind that answer (e.g. every field of a Greenbook registration, or every matching table row), so you're never limited to just the summary sentence.
- **`sources`** — direct links to the NAFDAC pages and/or documents the answer came from.
- **`notes`** — any extra context worth knowing, such as when a brand name was matched to its registered active ingredient.

Alongside your answer, the Actor also saves every page it crawled and a short run summary, in case you want to dig into the underlying data yourself.

## Example outputs

**Checking a product's registration status:**

```json
{
  "query": "Is Aspirin Cardio approved by NAFDAC?",
  "answer": "Yes. Aspirin Cardio (NAFDAC Reg. No. A4-1234) is registered to Example Pharma Ltd. Status: Active. Active ingredient: Aspirin. Approved March 3, 2019.",
  "sources": [
    { "title": "NAFDAC Greenbook – search: Aspirin Cardio", "url": "https://greenbook.nafdac.gov.ng/" }
  ]
}
```

**Asking for the latest alert:**

```json
{
  "query": "What is the latest public alert?",
  "answer": "Public Alert No. 14/2025 – Alert on Falsified Antimalarial Products in Circulation (August 12, 2025)",
  "sources": [
    { "title": "NAFDAC Public Alerts", "url": "https://nafdac.gov.ng/category/recalls-and-alerts/" }
  ]
}
```

**A list-style question:**

```json
{
  "query": "List products withdrawn in 2025",
  "answer": "12 entries in \"Withdrawn Products\" (147 rows total).",
  "sources": [
    { "title": "Market Authorization Withdrawal", "url": "https://nafdac.gov.ng/our-services/market-authorization-withdrawal/" }
  ]
}
```

## What makes the answers trustworthy

- **Answers come from real NAFDAC records, not guesses.** Where NAFDAC has published a structured record, that's exactly what comes back — not an AI paraphrase of it.
- **Every answer is sourced**, with a direct link to the original page or document, so nothing needs to be taken on faith.
- **List answers aren't summarized away.** Ask for every product withdrawn by a manufacturer and you get the actual matching rows, not a truncated overview.
- **It only reaches for AI when there's genuinely nothing structured to answer from** — for open-ended questions like "what does this guideline require?" — and even then, the model is restricted to only the material actually retrieved from NAFDAC's site, not its own general knowledge.
- **It says so when it can't find something**, rather than guessing.

## Automating it: schedules, webhooks & the API

Because this is an Apify Actor, it isn't limited to one-off manual runs:

- **Schedule it** to run daily or weekly and check for new alerts automatically.
- **Trigger it from your own application** via the Apify API, so a procurement system, pharmacy app, or compliance dashboard can ask questions programmatically.
- **Chain it with a webhook** to notify your team the moment a new recall or blacklist entry matching your interests appears.

## Good to know before you run it

- Answers reflect what is published on nafdac.gov.ng and the Greenbook **at the moment you run the Actor** — this is a strength (you always get current data) but also means results can change between runs if NAFDAC updates its site.
- Very broad questions ("tell me everything about NAFDAC") will take longer and crawl more pages than a specific one. Narrowing your question usually gets you a faster, more precise answer.
- Some very old notices exist only as scanned, image-based PDFs; text extraction may not be possible for those.
- If a brand name isn't recognized, try asking with the active ingredient instead (e.g. "Paracetamol" instead of a lesser-known brand name).

## Pricing

This is a paid Actor. Current pricing is shown on this Actor's page before you run it, and you're only charged for the runs you make.

## FAQ

**Does this Actor make decisions for me, like whether a product is safe?**
No. It surfaces exactly what NAFDAC has published, with sources, so you can make an informed decision yourself. It does not offer medical, legal, or regulatory advice.

**How current is the data?**
As current as NAFDAC's own website — the Actor reads it live on every run rather than working from a stored copy.

**Can I ask about a product by brand name instead of its official registered name?**
Yes, for well-known brands the Actor automatically maps common brand names to their registered active ingredient before searching.

**What if my question doesn't match any category?**
The Actor falls back to NAFDAC's own site search and homepage, so it will still make a reasonable attempt and let you know if nothing relevant was found.

## Support & feedback

If an answer looks wrong, incomplete, or NAFDAC has changed its site in a way that affects results, please get in touch — feedback directly improves the accuracy of future runs.