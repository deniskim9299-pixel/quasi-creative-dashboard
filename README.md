# Quasi Creative Performance Dashboard

Ad-level creative performance dashboard for Quasi (officialquasi.com), ported
from the IM8 creative dashboard with the Northbeam data source swapped for
**Triple Whale**.

**Live:** https://deniskim9299-pixel.github.io/quasi-creative-dashboard/

## How it works

1. `.github/workflows/refresh-data.yml` runs 4×/day: `scripts/fetch_triplewhale.py`
   pulls 180 days of daily × ad rows from the Triple Whale Data-Out SQL API
   (`pixel_joined_tvf`), parses every ad name with `scripts/parse_ad_name.py`,
   dedupes edited/copied names, and writes `public/data/latest.json`.
2. `.github/workflows/deploy-pages.yml` builds the Vite/React app and deploys
   to GitHub Pages after every data refresh or push to `main`.
3. The **Data Clean Up** tab lets you merge misspelled dimension values; saves
   commit `data/mappings.json` back to this repo via the GitHub API (PAT held
   in your browser's localStorage only).

## Attribution lenses

Each ad record carries two lenses (per row from `pixel_joined_tvf`):

| metrics key | source | shown as |
|---|---|---|
| `meta_rev` / `meta_txns` | Meta channel-reported (matches Ads Manager) | headline Revenue / Purchases / ROAS |
| `nb_rev` / `nb_txns` | Triple Whale Pixel · Triple Attribution · 7-day window | alternate lens (kept under IM8's `nb_*` keys so the frontend is unchanged) |
| `nc_rev` / `nc_txns` | TW Pixel new-customer split | stored for a future column |

## Creation dates

Quasi ad names don't embed dates, so an ad's `date` (YYMMDD) is its
**first-seen day** in Triple Whale. `data/first_seen.json` persists those
birthdates so ads keep them after aging past the 180-day lookback.

## Ad-name parser

Five families of the (in-flux) naming convention are handled — see
`scripts/parse_ad_name.py`. Family detection is isolated per family, so the
upcoming reworked convention drops in as family #6. Tests:
`pytest scripts/` (they run in CI before every data refresh).

## Setup (once)

1. Repo secret `TRIPLEWHALE_API_KEY` — a Triple Whale API key with Data-Out
   scope. Optional: `TRIPLEWHALE_SHOP_ID` if the default candidates
   (officialquasi.com / e1c5f3-7e.myshopify.com) don't resolve.
2. GitHub Pages: Settings → Pages → Source = **GitHub Actions**.
3. Update `GITHUB_REPO` in `src/App.jsx` and `base` in `vite.config.js` if the
   repo owner/name differs.

## Local dev

```bash
npm install && npm run dev              # frontend
python3 scripts/fetch_triplewhale.py    # needs TRIPLEWHALE_API_KEY
python3 scripts/fetch_triplewhale.py --csv export.csv   # offline, from a TW UI export
pytest scripts/                         # parser tests
```
