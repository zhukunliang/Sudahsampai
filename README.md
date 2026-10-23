# Sudahsampai

**Sudahsampai** ("Has it arrived?") is a purchase follow-up web app for Malaysian micro and small businesses: it tracks supplier orders, reminds the owner before delivery dates, drafts polite supplier chase messages when an order is overdue, reads mixed-language supplier replies with AI, checks delivered quantities, and keeps every action in an activity log.

## Team Information

| Item | Details |
|------|---------|
| Team Name | Sudahsampai |
| Team Domain | Domain 2 - Orbyte Smart SME Challenge |
| SDG | SDG 8 - Decent Work and Economic Growth |
| Team Leader | Zhu Kunliang |
| University | Xiamen University Malaysia |
| Members | 4 |

## Links

| Item | Link |
|------|------|
| Live Demo | https://sudahsampai.replit.app |
| GitHub | https://github.com/zhukunliang/Sudahsampai |
| Slides | TBD |
| Video | TBD |

## Problem

Many micro and small businesses in Malaysia order stock from several suppliers and follow up through WhatsApp, paper notes and memory. Delivery dates are easy to forget, late orders are noticed only when the shelf is empty, someone has to remember to chase the supplier, and short or extra deliveries are often not checked or recorded. There is no single place that shows which orders are due, late or wrong, or who did what.

Measured impact on SMEs (time spent on follow-ups, frequency of late or short deliveries): TBD.

## Solution

Sudahsampai keeps every supplier order in one place and runs a small, explainable rule engine on top of it. Each simulated day, it checks every open order, reminds the owner when a delivery is near, marks late orders as overdue and drafts a polite English + Malay chase message for the supplier. When a supplier replies on WhatsApp in mixed Malay, Chinese and English, staff paste the reply and AI turns it into structured facts (delay, partial delivery, price change, confirmation); the owner checks and confirms before any order is changed. When goods arrive, staff enter the received quantity; any mismatch is flagged for the owner, who decides what to do. Every step is written to an activity log, so the owner stays in control and can see exactly what happened.

## Key Features

The core flow follows the five steps **trigger → data → decision → approval → log**:

| Step | What happens in Sudahsampai |
|------|-----------------------------|
| 1. Trigger | A new simulated day starts ("Fast-forward 1 day"), staff confirm that goods have arrived, or a supplier reply is pasted in. |
| 2. Data | Supplier orders: supplier name and WhatsApp number, item, category, quantity and unit, delivery date, expiry date, reminder rule and auto-chase setting; plus the supplier's reply text. |
| 3. Decision | Deterministic rules: inside the reminder window → reminder; past the delivery date → overdue, plus a bilingual chase message if auto-chase is on; received quantity ≠ ordered quantity → discrepancy. Each order page explains in one sentence why it has its colour. AI is used in one place only: reading a supplier reply into a fixed JSON result (see below). |
| 4. Approval | Only the owner can change reminder rules, resolve a discrepancy (accept received quantity / request restock from supplier / mark for negotiation), and confirm an order update suggested from a supplier reply. Staff see these controls disabled, and the server rejects their requests. |
| 5. Log | Every action is recorded with simulated date, real time (Malaysia time), role, action and detail, shown on the activity log page and at the bottom of each order. |

### The AI step: reading supplier replies

Supplier replies are informal and mix languages, e.g. *"Bos, sorry ya 青菜 hari ni tak dapat hantar, lori rosak. Jumaat baru sampai, ok?"*. Keyword rules break as soon as the wording changes, so this one step uses a large language model:

- The reply is turned into a fixed JSON result: `intent` (delay / partial / price_change / confirm / unclear), `new_delivery_date`, `quantity`, `unit_price`, `confidence`, `summary_en`. Relative dates such as "Jumaat" (Friday) are converted using the simulated date.
- The server validates every field. If validation fails, `confidence` is below 0.6, or the intent is unclear, the result is marked **needs human check** and no update is offered.
- The system then gives a rule-based suggestion (for example, how many days later the new date is) and **only the owner** can confirm the update. The log records the original text, the AI result and who confirmed it.
- If the AI service is not configured, takes longer than 15 seconds, or returns an error, the app switches to a **rule mode** (keywords + regular expressions) and labels it on the page, so the demo never stops.
- All AI code is in `src/ai_parser.py`. The model is reached through any OpenAI-compatible API set by environment variables; the live demo uses the DeepSeek API (`deepseek-chat`).

Other features:

- Owner / staff roles (simulated login) with a single `@owner_required` permission check on the server; denied attempts are logged as `permission_denied`.
- On-time tracking: each delivery records whether it arrived on or before the delivery date.
- Same order is not reminded or chased twice on the same day.
- Summary of triggered events on the home page after each fast-forward.
- Owner-only "Reset demo data" button that reloads the synthetic data and sets the date back to 2026-10-21.
- Orbyte interface layer (`src/orbyte_adapter.py`): current user, supplier contacts, messaging, approvals, credit wallet and audit log all go through six functions. **All of them are simulated** today, based on our own assumptions about Orbyte's APIs; see [docs/api.md](docs/api.md).
- Simulated Orbyte credits: 1 credit per automatic chase and per delivery check, shown in the page corner.
- `POST /api/delivery-check`: the delivery check as a JSON API for other modules ([docs/api.md](docs/api.md)).
- Mobile-friendly pages.

Not yet built (planned): actually sending WhatsApp messages, and connecting to real Orbyte APIs.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.12 |
| Web framework | Flask 3 |
| Database | SQLite (Python built-in `sqlite3`, no ORM) |
| Templates / UI | Jinja2 templates, plain CSS |
| AI | DeepSeek API (`deepseek-chat`) through the `openai` Python library (OpenAI-compatible API, configured by environment variables) |
| Tests | Python `unittest` + Flask `test_client` |
| Hosting | Replit (Reserved VM deployment) |

## Project Structure

```
.
├── src/
│   ├── app.py              Flask routes, business rules, permission check
│   ├── db.py               Database schema, logging, simulated date, seed loading
│   ├── ai_parser.py        AI reading of supplier replies, validation, rule-mode fallback
│   ├── orbyte_adapter.py   Simulated Orbyte interface layer (6 functions)
│   ├── templates/          Jinja2 HTML templates
│   └── static/style.css    Styles (mobile-friendly)
├── data/
│   ├── seed_orders.json    Synthetic orders (fictional suppliers)
│   └── sample_replies.json Synthetic supplier replies (one per intent, mixed languages)
├── tests/
│   ├── test_app.py         App tests (orders, receiving, permissions, fast-forward)
│   └── test_ai_parser.py   AI parser tests (rule mode, validation, fallback)
├── scripts/
│   └── compare_models.py   Compare AI models on the sample supplier replies
├── docs/
│   └── api.md              Module API and assumed Orbyte capabilities (all simulated)
├── submission/             Proposal, declaration, slides, proof of Replit use, video link
├── requirements.txt
├── .replit                 Replit run and deployment config
└── LICENSE                 Apache License 2.0
```

`data/sdc.db` (database) and `data/sim_state.json` (current simulated date) are created at runtime and are not committed.

## How to Run

### Locally

Windows (PowerShell):

```bash
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe src/app.py
```

macOS / Linux:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python src/app.py
```

Then open http://localhost:5000. Optional environment variables:

| Variable | Purpose | If not set |
|----------|---------|------------|
| `SECRET_KEY` | Signs the session cookie | A random key is generated at each start |
| `AI_BASE_URL` | OpenAI-compatible API address, e.g. `https://api.deepseek.com` | Rule mode |
| `AI_API_KEY` | API key for that service | Rule mode |
| `AI_MODEL` | Model name, e.g. `deepseek-chat` or `glm-5.3` | Rule mode |
| `AI_MAX_TOKENS` | Maximum tokens the model may return; raise it for models that reason before answering | 400 |
| `AI_REASONING_EFFORT` | e.g. `low`; only sent to the model when set | Not sent |

Never put these values in the code or commit them.

### On Replit

1. **Create App → Import from GitHub**, and choose this repository.
2. Press **Run**; the app starts with `python src/app.py` on port 5000 (configured in `.replit`).
3. Add `SECRET_KEY`, `AI_BASE_URL`, `AI_API_KEY` and `AI_MODEL` under **Secrets** (for Run / Preview) **and** under **Deployment secrets** (for the published app); the two lists are separate.
4. **Publish** as a **Reserved VM**. SQLite is a local file, so Autoscale instances would lose data.
5. After every publish, open the live site as the owner and press **Reset demo data**.

## How to Run Tests

From the project root:

```bash
python -m unittest discover tests
```

There are 36 tests in two files. They never call a real AI service and need no API key.

- `tests/test_app.py` uses a temporary database and simulated-date file (via the `SDC_DB_PATH` and `SDC_SIM_PATH` environment variables) and deletes them afterwards, so `data/sdc.db` is never touched. It covers: rejecting a zero quantity, matching and mismatched deliveries, staff being blocked from owner-only actions, and auto-chase on overdue orders without duplicates on the same day.
- `tests/test_ai_parser.py` covers the rule mode on every sample reply and on mixed messages such as *"Jumaat baru boleh hantar, 12 kotak je dulu"* (delay + partial: 12 boxes, next Friday), the rule that "confirm" is only chosen when no delay, partial or price signal is present, server-side validation of bad AI results, and falling back to rule mode when the AI times out or fails.

## How We Use Replit

- **Development:** the GitHub repository is imported into Replit; we run and check the app with Run / Preview and use the Replit Shell and Git pane to pull new commits.
- **Deployment:** the live demo at https://sudahsampai.replit.app is a Replit Reserved VM deployment; `SECRET_KEY` and the `AI_*` settings (including the DeepSeek API key) are stored in Replit Secrets, never in the code.
- Proof of use: `submission/REPLIT_PROOF_OF_USE.pdf` (TBD).

## How We Use RunPod

We tested RunPod as a candidate AI provider but did not choose it for the live demo.

**What we tested.** The AI step reaches any OpenAI-compatible API through environment variables (`AI_BASE_URL`, `AI_API_KEY`, `AI_MODEL`, plus the optional `AI_MAX_TOKENS` and `AI_REASONING_EFFORT`), so no code is specific to one provider. We pointed it at RunPod's public endpoint for **GLM-5.3** (`glm-5.3`, max tokens 2000, reasoning effort `low`), paid from the RunPod credits provided by the competition, and ran `scripts/compare_models.py` against both providers on the same 6 synthetic supplier replies (simulated date 2026-10-21):

| | DeepSeek (`deepseek-chat`) | RunPod (`glm-5.3`) |
|---|---|---|
| Answered by AI (no fallback to rules) | 6 / 6 | 6 / 6 |
| Average time per reply | 1.12 s | 4.39 s |
| Flagged for human check | 1 (the unclear reply) | 1 (the unclear reply) |
| "Lori rosak. Jumaat baru boleh hantar, 12 kotak je dulu" (15 boxes ordered) | `partial`, 12 boxes, Friday | `delay`, 12 boxes, Friday |

**Why we chose DeepSeek.** Both models handled the first five replies sensibly. On the last one, the supplier is both late *and* short; DeepSeek marked it as a partial delivery, while GLM-5.3 marked it only as a delay, which in our app would update the date but not warn the owner about the 3 missing boxes. DeepSeek was also about 4 times faster. This was a small test (6 replies, one run each), so it is not a general ranking of the two models. Switching to RunPod later only needs the Secrets changed; screenshots of the test are in `submission/`.

## Data & Privacy

- **Synthetic data only.** All suppliers, phone numbers, orders and supplier replies in `data/seed_orders.json` and `data/sample_replies.json` are fictional; phone numbers use the placeholder pattern `+60 12-000 10xx`. No real customer or supplier data is collected or stored.
- **What the AI service receives.** When a reply is analysed, the reply text and basic order details (item, quantity and unit, delivery date, supplier name) are sent to the DeepSeek API, whose servers are outside Malaysia. Supplier phone numbers are not sent. In the demo all of this is synthetic. The RunPod comparison test sent only the synthetic sample replies and one fictional order.
- **For a real deployment**, supplier names and WhatsApp numbers can be personal data under Malaysia's Personal Data Protection Act 2010 (PDPA, as amended in 2024). A real deployment would need to:
  - tell suppliers what data is stored and why, and use it only for order follow-up (notice and choice, purpose limitation);
  - limit access by role and keep the activity log as an audit trail (security);
  - store secrets outside the code, use HTTPS, and keep the database access-controlled;
  - delete order and contact data that is no longer needed (retention);
  - let suppliers ask to see or correct their data (access and data integrity);
  - have a process for reporting data breaches;
  - follow the PDPA rules on transferring personal data outside Malaysia before sending supplier messages to an overseas AI service, tell suppliers about it, or switch to an AI service hosted in a suitable location.
- Exact legal obligations for a production deployment: TBD, to be confirmed against official PDPA guidance.

## Team

| Name | Role | GitHub |
|------|------|--------|
| Zhu Kunliang | Team Leader, TBD | TBD |
| Yan Feifan | TBD | TBD |
| Huang Jiamin | TBD | TBD |
| Lou Jingxing | TBD | TBD |

## Submission Checklist

Deadline: Sunday 25 October 2026, 7:59:59 AM MYT (planned submission: evening of 24 October).

- [ ] Proposal PDF: `SDC2026_Sudahsampai_Preliminary.pdf` (word limits checked)
- [ ] Pitch deck (matches the video)
- [ ] Live prototype link works in a private browser window: https://sudahsampai.replit.app
- [ ] Technology & Tools Declaration: `SDC2026_Sudahsampai_TechDeclaration.pdf`
- [ ] Proof of Replit use: `REPLIT_PROOF_OF_USE.pdf`
- [ ] GitHub repository is **public**, has the Apache License 2.0, and judges are added as collaborators
- [ ] No secrets in the repository
- [ ] Pitch video on YouTube, 5-7 minutes, includes the live demo
- [ ] Demo data reset before judging

## License

Copyright 2026 Team Sudahsampai

This project is licensed under the [Apache License 2.0](LICENSE).
