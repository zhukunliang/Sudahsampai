# Sudahsampai

Sudahsampai reads supplier replies in Malay, Chinese and English, flags delays and short deliveries, and asks the owner to approve anything important — so micro SMEs stop chasing suppliers by hand.

## Team Information

| Item | Details |
|------|---------|
| Team Name | 200 OK FOUND |
| Domain | Domain 2 — Orbyte Smart SME Challenge: Less Busywork, Better Business (Alpha Red Solutions Sdn Bhd) |
| SDG Track | SDG 8 — Decent Work and Economic Growth (targets 8.2, 8.3) |
| Team Leader | Zhu Kunliang |
| University | Xiamen University Malaysia |
| Members | 4 |

## Links

| Resource | Link |
|---|---|
| GitHub Repository | https://github.com/zhukunliang/Sudahsampai |
| Live Demo | https://sudahsampai.replit.app |
| RunPod Endpoint | Not used by the live app. A RunPod public endpoint (GLM-5.3) was used only for a one-off model comparison (`scripts/compare_models.py`); see [Runpod proof of use](submission/SDC2026_200OKFOUND_Runpod_POU.pdf) |
| Slide Deck | [SDC2026_200OKFOUND_PitchDeck.pdf](submission/SDC2026_200OKFOUND_PitchDeck.pdf) |
| Demo Video | https://youtu.be/iuXjIcDRGIM |

## Overview

### Problem

Micro SME owners in Malaysia order stock from several suppliers and follow up by hand over WhatsApp, in a mix of Malay, Chinese and English. Delivery dates are easy to forget, late orders are noticed only when the shelf is empty, and short deliveries are often not checked or recorded. Each missed delay or missing box costs the shop money, and there is no single place that shows which orders need attention.

### Solution

Sudahsampai is a supplier-order follow-up module, designed as a third-party module for the Orbyte ecosystem. Every order goes through the same five steps:

1. **Trigger** — a new (simulated) day starts, goods arrive, or a supplier reply is pasted in.
2. **Read data** — the order (supplier, item, quantity, unit price, delivery date, reminder rule) and the supplier's reply.
3. **Identify exceptions (rules + AI)** — deterministic rules find orders that are due, overdue or delivered short; AI reads the supplier's free-text reply into a fixed JSON result (delay, partial delivery, price change, confirmation or unclear).
4. **Owner approval** — anything important (changing an order from a reply, resolving a short delivery, changing rules) needs the owner. Only clearly low-risk updates may apply automatically, and the owner can undo them.
5. **Outcome + activity log** — the order is updated and every step is written to the activity log.

AI only reads replies, flags exceptions and drafts messages; it never makes business decisions by itself.

### Key Features

- **Priority dashboard** — today's to-do in three groups: *Must handle*, *Good to know* and *Waiting for supplier*, plus a one-line summary (needs action, arriving soon, waiting, losses prevented in RM).
- **New purchase order entry** — supplier, WhatsApp number, item, category, quantity, unit, unit price, delivery and expiry dates, reminder rule.
- **Supplier reply analysis with DeepSeek (`deepseek-chat`)** — returns fixed JSON (`intent`, `new_delivery_date`, `quantity`, `unit_price`, `confidence`, `summary_en`), validated on the server. A result with confidence below 0.6, an unclear intent or an invalid field is marked *needs human check*.
- **Offline rule mode** — if the AI is not configured, takes longer than 15 s or returns an error, keyword and regular-expression rules take over and the page labels it, so the demo never stops.
- **Personal data redacted before any AI call** — phone numbers, email addresses, names after a title (e.g. "Encik Ali", "Mr Tan") and the supplier's name are replaced with `[REDACTED]`; the supplier name, phone number and order number are not sent.
- **Auto-apply low-risk updates** — per-order owner setting (on by default): if the supplier confirms, or delays by 1–2 days, with confidence ≥ 0.8, the update is applied and logged. Only the owner can undo it. Partial deliveries, price changes and unclear replies always wait for the owner.
- **Receiving check** — staff enter the received quantity; a short delivery is flagged with its RM value and sent to the owner, who can accept it, mark it for negotiation, or ask the supplier to restock. Choosing restock changes the status to *waiting for restock*, logs it, and sends a bilingual restock request to the supplier (simulated, shown on the order page).
- **AI-drafted supplier replies** — after each analysis, an English + Malay reply draft that can be edited before sending; sending is simulated. It never accepts a price change or a smaller quantity on the owner's behalf. Without AI, a template is used.
- **Roles: owner vs staff** — `@owner_required` protects owner-only actions on the server; a staff attempt returns 403 and is logged as `permission_denied`.
- **Activity log** — every action with simulated date, Malaysia time, role and detail; filterable by order number.
- **Orbyte credits (simulated)** — 1 credit per automatic chase message and per delivery check.
- **Simulated date** — "Fast-forward 1 day" runs reminders, marks overdue orders and sends automatic chase messages (simulated); "Reset demo data" (owner only) restores the synthetic data and date 2026-10-21.
- **UI in English / Bahasa Melayu / Chinese** — switchable in the top bar.
- **Feedback page** (`/feedback`) — an anonymous five-question survey for shop owners; answers are stored in the database. Nothing is pre-filled.

### Orbyte Fit

All platform calls go through one file, `src/orbyte_adapter.py`:

| Function | Orbyte capability (assumed) |
|---|---|
| `get_current_user()` | Current user and role (owner / staff) |
| `get_supplier_contact(order)` | Supplier contact |
| `send_message(contact, text)` | Messaging (WhatsApp via Orbyte Connect) |
| `create_approval(conn, order_id, title, detail)` | Approval task in the owner's to-do list |
| `charge_credit(conn, reason, order_id)` | Credit wallet |
| `write_audit(conn, action, detail, ...)` | Shared audit log |

All of these are **simulated** today, based on our own assumptions about Orbyte's APIs. This is the only file to change when connecting to real Orbyte. Sudahsampai does not duplicate Orbyte's HR or admin features: users, roles and contacts are expected to come from Orbyte. Details: [docs/api.md](docs/api.md).

### Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Jinja2 templates, plain CSS, Lucide icons (unpkg CDN), Plus Jakarta Sans (Google Fonts) |
| Backend | Python 3.12, Flask 3 |
| Database | SQLite (Python built-in `sqlite3`, no ORM) |
| AI | DeepSeek API (`deepseek-chat`) through the `openai` Python library (OpenAI-compatible, configured by environment variables) |
| Hosting / Development | Replit, Reserved VM deployment |
| Tests | Python `unittest` + Flask `test_client` |

## Project Structure

```
.
├── src/
│   ├── app.py                Flask routes, business rules, owner/staff permission check
│   ├── db.py                 SQLite schema, activity log, simulated date, seed loading
│   ├── ai_parser.py          AI reply reading, redaction, validation, rule mode, reply/restock drafts
│   ├── orbyte_adapter.py     Simulated Orbyte interface layer (the only file to replace for real Orbyte)
│   ├── i18n.py               Language switching (EN / BM / 中文)
│   ├── locales.json          All UI text in three languages
│   ├── templates/            Jinja2 HTML templates
│   └── static/               style.css
├── data/
│   ├── seed_orders.json      Synthetic orders (fictional suppliers)
│   └── sample_replies.json   Synthetic supplier replies (one per intent, mixed languages)
├── tests/
│   ├── test_app.py           App tests (24)
│   └── test_ai_parser.py     AI parser tests (51)
├── scripts/
│   └── compare_models.py     Compares AI models on the sample replies (used for the RunPod test)
├── docs/
│   └── api.md                Module API and assumed Orbyte capabilities (all simulated)
├── submission/
│   ├── README.md                                  List of submission files
│   ├── SDC2026_200OKFOUND_Replit_POU.pdf          Replit proof of use
│   ├── SDC2026_200OKFOUND_Runpod_POU.pdf          RunPod proof of use
│   └── SDC2026_200OKFOUND_PitchDeck.pdf           Slide deck
├── requirements.txt          Flask, openai
├── .replit                   Replit run and deployment config
└── LICENSE                   Apache License 2.0
```

`data/sdc.db` (the SQLite database) and `data/sim_state.json` (current simulated date) are created at runtime and are not committed.

The preliminary proposal (`SDC2026_200OKFOUND_Preliminary.pdf`) and the Technology & Tools Declaration (`SDC2026_200OKFOUND_TechDeclaration.pdf`) are submitted on sdchack.my and are not in this public repository, because they contain the team leader's contact details.

### Architecture

```
Browser
  │
  ▼
Flask app on Replit (src/app.py)
  ├── src/db.py              → SQLite (data/sdc.db)
  ├── src/ai_parser.py       → DeepSeek API (redacted text only; offline rules if unavailable)
  └── src/orbyte_adapter.py  → Orbyte platform (simulated)
```

Module interfaces and assumptions: [docs/api.md](docs/api.md).

## How to Run

### Environment variables

| Variable | Purpose | If not set |
|---|---|---|
| `SECRET_KEY` | Signs the session cookie | A random key is generated at each start |
| `AI_BASE_URL` | OpenAI-compatible API address, e.g. `https://api.deepseek.com` | Offline rule mode |
| `AI_API_KEY` | API key for that service | Offline rule mode |
| `AI_MODEL` | Model name, e.g. `deepseek-chat` | Offline rule mode |
| `AI_MAX_TOKENS` (optional) | Maximum tokens the model may return | 400 |
| `AI_REASONING_EFFORT` (optional) | e.g. `low`; only sent to the model when set | Not sent |

Without the three `AI_*` values the app still works: supplier replies are read by the offline rules, and reply drafts and restock requests use templates. Never put these values in the code or commit them.

### Option 1: Replit

1. **Create App → Import from GitHub** and choose this repository.
2. Add the variables above under **Secrets** (used by Run / Preview). For a published app, add them again under **Publish → Deployment secrets**; the two lists are separate.
3. Click **Run**. The command in `.replit` creates a virtual environment, installs `requirements.txt` and starts `src/app.py` on port 5000.
4. To publish, deploy as a **Reserved VM** (SQLite is a local file).

### Option 2: Local

```bash
git clone https://github.com/zhukunliang/Sudahsampai.git
```
```bash
cd Sudahsampai
```

macOS / Linux:

```bash
python3 -m venv .venv
```
```bash
.venv/bin/pip install -r requirements.txt
```
```bash
.venv/bin/python src/app.py
```

Windows (PowerShell):

```bash
py -m venv .venv
```
```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
```
```bash
.venv\Scripts\python.exe src/app.py
```

Then open http://localhost:5000. To use the AI, set the environment variables in the same terminal before starting (the app reads them from the environment; it does not load a `.env` file).

### Run tests

From the project root:

```bash
python -m unittest discover tests
```

75 tests (24 in `tests/test_app.py`, 51 in `tests/test_ai_parser.py`). They use a temporary database, never call a real AI service and need no API key.

### Demo tips for judges

- No login or password is needed: roles are simulated. Use the dark **Demo controls** bar at the top to switch between **Owner** and **Staff**; as staff, owner-only buttons are disabled and direct requests are refused and logged.
- **Fast-forward 1 day** moves the simulated date forward and shows what was triggered (reminders, overdue orders, chase messages).
- **Reset demo data** (owner only) restores the 7 synthetic orders and the date 2026-10-21.
- On an order page, choose a sample supplier reply and click **Analyse reply** to see the AI result, the suggested update and the reply draft.

## How We Use Replit (Required)

- The GitHub repository was imported into Replit, where we ran and tested the app with Run / Preview and pulled new commits through the Git pane.
- The live demo at https://sudahsampai.replit.app is a Replit **Reserved VM** deployment.
- API keys are stored in Replit Secrets (and separately in Deployment secrets for the published app), never in the code.
- Replit Core was redeemed with the SDC voucher.
- Proof: [SDC2026_200OKFOUND_Replit_POU.pdf](submission/SDC2026_200OKFOUND_Replit_POU.pdf)

## How We Use RunPod (Required)

Our project does not need a GPU. We used a RunPod public endpoint for **GLM-5.3**, paid with the competition credits, to compare it against DeepSeek on the same 6 synthetic supplier replies with `scripts/compare_models.py`:

| | DeepSeek (`deepseek-chat`) | RunPod (`glm-5.3`) |
|---|---|---|
| Answered by AI (no fallback to rules) | 6 / 6 | 6 / 6 |
| Average time per reply | 1.12 s | 4.39 s |
| Flagged for human check | 1 (the unclear reply) | 1 (the unclear reply) |
| "Lori rosak. Jumaat baru boleh hantar, 12 kotak je dulu" (15 boxes ordered) | `partial`, 12 boxes, Friday | `delay`, 12 boxes, Friday |

DeepSeek was more accurate on the partial-delivery-plus-delay case (GLM-5.3's answer would have updated the date without warning the owner about the 3 missing boxes) and about 4 times faster, so the live app uses DeepSeek. This was a small test (6 replies, one run each), not a general ranking of the two models. Switching models only needs the `AI_*` environment variables changed; no code is specific to one provider.

Proof: [SDC2026_200OKFOUND_Runpod_POU.pdf](submission/SDC2026_200OKFOUND_Runpod_POU.pdf)

## Data & Privacy

- **Synthetic data only.** All suppliers, phone numbers, orders and replies in `data/seed_orders.json` and `data/sample_replies.json` are fictional. The SQLite database file is created at runtime and is not committed. The anonymous `/feedback` form asks for no name or contact details.
- **Redaction before AI calls.** Only what is needed is sent (reply text, item, quantity and unit, delivery date, simulated date); phone numbers, emails, titled names and the supplier's name are replaced with `[REDACTED]`. Redaction is pattern-based, so names without a title or in Chinese characters are not detected.
- **Real deployment.** Supplier names and phone numbers can be personal data under Malaysia's Personal Data Protection Act 2010 (PDPA); a real deployment would follow the PDPA, including its rules on sending data to an AI service outside Malaysia.
- **Simulated in this prototype:** WhatsApp sending, login and roles, and the Orbyte platform.

## Changes After Mentoring

After mentoring on 23 Oct 2026 with mentor Fadli Khalid, we:

- added a collapsible **"How Sudahsampai works"** banner at the top of the dashboard;
- simplified the dashboard: today's to-do list comes first, the numbers are reduced to one line, and the colours use one main colour;
- redacted personal data before every AI call.

## Challenges & Learnings

- **Challenge — one reply often says two things.** Supplier messages mix Malay, Chinese and English, and a single line like *"Jumaat baru boleh hantar, 12 kotak je dulu"* means both "late" and "short"; our first offline rules read it as a confirmation. We fixed this by collecting every signal before choosing the intent, and by keeping the AI as a reader only: the server validates its answer and the owner approves anything risky.
- **Learning — privacy and deployment matter as much as features.** Mentor feedback showed that supplier names were being sent to an overseas AI service, so we added redaction before every AI call; and a Replit build broke when packages could no longer be installed into the system Python, which we fixed with a project virtual environment. Testing on the live site after every publish caught problems that local tests did not.

## Future Improvements (Phase 2)

- Send and receive messages through the real WhatsApp Business API.
- Connect to real Orbyte APIs by replacing `src/orbyte_adapter.py`.
- Run a pilot with 3–5 merchants to measure a baseline: weekly hours spent following up suppliers, and RM lost to short deliveries.
- Cover more languages and phrases in the offline rule mode.

## Team

| Name | Role | GitHub |
|---|---|---|
| Zhu Kunliang | Team Leader / Development (code) | [zhukunliang](https://github.com/zhukunliang) |
| Yan Feifan | Pitch deck (slides) | [Scarlett2026109](https://github.com/Scarlett2026109) |
| Huang Jiamin | Demo video | JasmineHuang111 |
| Lou Jingxing | Documents (proposal and declaration PDFs) | [201035](https://github.com/201035) |

## AI Tools Disclosure

Claude Code and Claude chat assisted with code and reviews under the team's direction. ChatGPT and Doubao assisted with slides and text. The full list is in the Technology & Tools Declaration (submitted on sdchack.my).

## Submission Checklist

- [x] Team name, Domain 2 and SDG 8 filled in
- [x] Slide deck and demo video links added
- [x] Replit used, proof in `submission/`
- [x] RunPod used (comparison test only), proof in `submission/`
- [x] Preliminary proposal and Tech Declaration submitted on sdchack.my (not in the public repo: they contain contact details)
- [x] "How to Run" steps verified
- [x] No API keys or secrets committed
- [x] Apache License 2.0 included
- [x] Repository public; committee accounts added as collaborators only if requested

## License

This project is licensed under the [Apache License 2.0](LICENSE).
