# Sample files

Reference files for the three trispoke lead-intake paths and the review-queue export.

| File | What it is | When to use |
|---|---|---|
| [import_template.csv](import_template.csv) | The CSV shape trispoke's **Apollo CSV upload** intake accepts. 5 sample rows. | Match your own lead lists to these column headers before uploading via *Campaigns → New campaign → Apollo CSV upload*. |
| [export_sample.csv](export_sample.csv) | Mock of what the review-queue **Export to Excel** button produces. 26 columns, 5 sample leads at different pipeline stages. | Preview the export shape before clicking export on a real campaign. Use it to plan downstream parsing. |
| [apollo_search_result_sample.json](apollo_search_result_sample.json) | Mock of the response from `POST /api/v1/mixed_people/api_search` — what the **Apollo search query** intake receives, parses, and inserts into the `leads` table. | Reference for what data is available when wiring Apollo-search intake. Documents which fields trispoke reads and which leads get skipped (e.g. no email). |

## Import CSV details

Required column (case-insensitive):

- **Email** — the recipient address. Rows without this are skipped.

Recognized columns (any subset is fine; missing fields will be enriched by the Apollo enrichment step):

- `First Name`, `Last Name` — used in greetings + personalization
- `Title` — drives pain analysis bucketing
- `Company` — the org name (also accepts `Organization`)
- `Website` — company domain (also accepts `Domain` or `Company Domain`)
- `Industry` — drives pain analysis bucketing
- `Company Size` — drives pain analysis bucketing (especially for sub-30 small-business pain)
- `Location` — used by some prompts
- `LinkedIn URL` — useful for re-enrichment
- `Apollo ID` — optional; if present, trispoke caches it for later lookups

Headers are matched case-insensitively. Whitespace around values is trimmed.

## Export CSV details

Filename pattern: `trispoke_{campaign_slug}_{YYYY-MM-DD_HHMMSS}.xlsx`. Same 26 columns as `export_sample.csv` (note the actual file is `.xlsx`; we ship `.csv` here for easy git diff viewing).

The export respects whatever filter is active in the review queue when you click **Export (filtered)** — so a "⚠ QC flagged" filter gives you just the flagged rows. **Export all** ignores the filter.

Columns split into four logical groups:

| Group | Columns |
|---|---|
| **Lead** | Lead first name, Lead last name, Lead email, Lead title, Company name, Company domain, Industry, Headcount, Location, Intake source, LinkedIn URL |
| **Pain analysis** | Pain chronic, Pain acute, Pain trigger, Pain confidence |
| **Generated email** | Email subject, Email body, Model used, Generation seconds, Tokens used |
| **Pipeline state** | QC status, QC flags, Status, Created at, Apollo sequence ID, Apollo message ID |

## Apollo search result details

trispoke's "Apollo search query" intake POSTs to `https://api.apollo.io/api/v1/mixed_people/api_search` with a body like:

```json
{
  "person_titles": ["CEO", "VP Operations"],
  "person_locations": ["Bhubaneswar", "Mumbai"],
  "organization_num_employees_ranges": ["50,200"],
  "q_keywords": "pharmaceutical",
  "page": 1,
  "per_page": 25
}
```

The response carries up to 25 contacts per page in `people[]`. trispoke iterates with pagination up to 500 results per campaign (configurable). For each contact it reads:

- `email` — required; skipped if null/missing
- `first_name`, `last_name`
- `title`
- `organization.name`, `organization.website_url`
- `linkedin_url`
- `id` — stored as `apollo_contact_id` for later push-worker lookups

Unsubscribed addresses (anything in the `leads` table with `status='unsubscribed'`) are filtered out before insert via [src/trispoke/db/unsubscribe.py](../src/trispoke/db/unsubscribe.py).
