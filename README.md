# Bid Bond Validator

An AI-assisted checker for bid bonds (tender guarantees). Upload a bond as a PDF, enter the tender details, and get a seven-point compliance checklist with a colour-coded status for each criterion.

> **Assistive tool, not a decision-maker.** The output comes from a vision-language model and can be wrong. A human reviewer must verify the original document before accepting or rejecting a bond.

## The problem

In oil & gas tendering, every bid must come with a bank guarantee that meets strict conditions: the right beneficiary, currency and amount, an acceptable issuing bank, enough validity, and an unconditional payment clause. Checking these by hand for every bidder is slow and error-prone, especially near the closing date.

## What it does

1. Renders the first pages of the uploaded PDF as images.
2. Sends them to a vision-language model together with the tender requirements.
3. Returns a table with seven checks, each marked GREEN, YELLOW or RED, plus an overall result (PASS / CONDITIONAL / FAIL) and a one-line legal risk note.

| # | Check |
|---|-------|
| 1 | Tender name and number match |
| 2 | Beneficiary name is exact |
| 3 | Currency |
| 4 | Amount is at least the required amount |
| 5 | Issuing bank status (approved / international / blacklisted) |
| 6 | Validity period against the required minimum expiry date |
| 7 | Form compliance, including On First Demand wording |

The minimum expiry date is calculated in code (closing date + required days) and passed to the model, so the model does not have to do date arithmetic.

## Configuration

All policy is in `rules.json`, not in the code. The file in this repository contains **sample values only**. Keep your real rules outside version control: copy the file to `rules.local.json` (already git-ignored) and point to it with `RULES_FILE`.

| Variable | Purpose |
|----------|---------|
| `NVIDIA_API_KEY` | API key for the model endpoint (required) |
| `APP_PASSWORD` | Password for HTTP Basic auth (required unless `ALLOW_NO_AUTH=1`) |
| `APP_USER` | Basic auth user name (default `admin`) |
| `RULES_FILE` | Path to the rules file (default `rules.json`) |
| `MODEL` | Model name (default `meta/llama-3.2-11b-vision-instruct`) |
| `ALLOW_NO_AUTH` | Set to `1` to disable auth for local development only |

## Run locally

```bash
pip install -r requirements.txt
export NVIDIA_API_KEY=your_key
export ALLOW_NO_AUTH=1
python main.py
```

Open http://localhost:8080.

## Deploy

The included `Procfile` works on Railway and similar platforms. Set `NVIDIA_API_KEY` and `APP_PASSWORD` as environment variables in the platform, never in the code.

## Safety notes

- The server refuses to run without a password unless you opt out explicitly.
- Uploads are limited to 10 MB and the first 6 pages.
- Model output is escaped; only a small whitelist of table tags is rendered.
- Do not upload real bonds to a third-party API unless your organisation's data policy allows it. Use sample documents for demos.

## Limitations

- A single model call reads the document; there is no second-pass review yet.
- A small vision model can misread stamps, handwriting or low-quality scans.
- Bank lists and thresholds must be kept up to date by the owner of the rules file.

## Ideas for next steps

- Extract text with PyMuPDF first and compare it with the image reading.
- Add a second reviewer pass that challenges the first result.
- Export the checklist as a PDF for the tender file.
