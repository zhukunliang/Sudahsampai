# Sudahsampai

**Sudahsampai** ("Has it arrived?") is a purchase follow-up web app for Malaysian micro and small businesses: it tracks supplier orders, reminds the owner before delivery dates, drafts polite supplier chase messages when an order is overdue, checks delivered quantities, and keeps every action in an activity log.

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
| GitHub | TBD |
| Slides | TBD |
| Video | TBD |

## Problem

Many micro and small businesses in Malaysia order stock from several suppliers and follow up through WhatsApp, paper notes and memory. Delivery dates are easy to forget, late orders are noticed only when the shelf is empty, someone has to remember to chase the supplier, and short or extra deliveries are often not checked or recorded. There is no single place that shows which orders are due, late or wrong, or who did what.

Measured impact on SMEs (time spent on follow-ups, frequency of late or short deliveries): TBD.

## Solution

Sudahsampai keeps every supplier order in one place and runs a small, explainable rule engine on top of it. Each simulated day, it checks every open order, reminds the owner when a delivery is near, marks late orders as overdue and drafts a polite English + Malay chase message for the supplier. When goods arrive, staff enter the received quantity; any mismatch is flagged for the owner, who decides what to do. Every step is written to an activity log, so the owner stays in control and can see exactly what happened.

## Key Features

The core flow follows the five steps **trigger → data → decision → approval → log**:

| Step | What happens in Sudahsampai |
|------|-----------------------------|
| 1. Trigger | A new simulated day starts ("Fast-forward 1 day"), or staff confirm that goods have arrived. |
| 2. Data | Supplier orders: supplier name and WhatsApp number, item, category, quantity and unit, delivery date, expiry date, reminder rule and auto-chase setting. |
| 3. Decision | Deterministic rules: inside the reminder window → reminder; past the delivery date → overdue, plus a bilingual chase message if auto-chase is on; received quantity ≠ ordered quantity → discrepancy. Each order page explains in one sentence why it has its colour. |
| 4. Approval | Only the owner can change reminder rules and resolve a discrepancy (accept received quantity / request restock from supplier / mark for negotiation). Staff see these controls disabled, and the server rejects their requests. |
| 5. Log | Every action is recorded with simulated date, real time (Malaysia time), role, action and detail, shown on the activity log page and at the bottom of each order. |

Other features:

- Owner / staff roles (simulated login) with a single `@owner_required` permission check on the server; denied attempts are logged as `permission_denied`.
- On-time tracking: each delivery records whether it arrived on or before the delivery date.
- Same order is not reminded or chased twice on the same day.
- Summary of triggered events on the home page after each fast-forward.
- Owner-only "Reset demo data" button that reloads the synthetic data and sets the date back to 2026-10-21.
- Mobile-friendly pages.

Not yet built (planned): actually sending WhatsApp messages, AI extraction of orders from pasted messages.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.12 |
| Web framework | Flask 3 |
| Database | SQLite (Python built-in `sqlite3`, no ORM) |
| Templates / UI | Jinja2 templates, plain CSS |
| Tests | Python `unittest` + Flask `test_client` |
| Hosting | Replit (Reserved VM deployment) |

## Project Structure

```
.
├── src/
│   ├── app.py              Flask routes, business rules, permission check
│   ├── db.py               Database schema, logging, simulated date, seed loading
│   ├── templates/          Jinja2 HTML templates
│   └── static/style.css    Styles (mobile-friendly)
├── data/
│   └── seed_orders.json    Synthetic orders (fictional suppliers)
├── tests/
│   └── test_app.py         Automated tests
├── docs/                   Architecture diagram and screenshots (TBD)
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

Then open http://localhost:5000. Optional: set the `SECRET_KEY` environment variable; if it is not set, a random key is generated at each start.

### On Replit

1. **Create App → Import from GitHub**, and choose this repository.
2. Press **Run**; the app starts with `python src/app.py` on port 5000 (configured in `.replit`).
3. Add `SECRET_KEY` under **Secrets** (deployment secrets).
4. **Publish** as a **Reserved VM**. SQLite is a local file, so Autoscale instances would lose data.
5. After every publish, open the live site as the owner and press **Reset demo data**.

## How to Run Tests

From the project root:

```bash
python -m unittest discover tests
```

The tests use a temporary database and simulated-date file (via the `SDC_DB_PATH` and `SDC_SIM_PATH` environment variables) and delete them afterwards, so `data/sdc.db` is never touched. They cover: rejecting a zero quantity, matching and mismatched deliveries, staff being blocked from owner-only actions, and auto-chase on overdue orders without duplicates on the same day.

## How We Use Replit

- **Development:** the GitHub repository is imported into Replit; we run and check the app with Run / Preview and use the Replit Shell and Git pane to pull new commits.
- **Deployment:** the live demo at https://sudahsampai.replit.app is a Replit Reserved VM deployment; `SECRET_KEY` is stored in Replit Secrets.
- Proof of use: `submission/REPLIT_PROOF_OF_USE.pdf` (TBD).

## How We Use RunPod

Not used: our project has no GPU workload.

## Data & Privacy

- **Synthetic data only.** All suppliers, phone numbers and orders in `data/seed_orders.json` are fictional; phone numbers use the placeholder pattern `+60 12-000 10xx`. No real customer or supplier data is collected or stored.
- **For a real deployment**, supplier names and WhatsApp numbers can be personal data under Malaysia's Personal Data Protection Act 2010 (PDPA, as amended in 2024). A real deployment would need to:
  - tell suppliers what data is stored and why, and use it only for order follow-up (notice and choice, purpose limitation);
  - limit access by role and keep the activity log as an audit trail (security);
  - store secrets outside the code, use HTTPS, and keep the database access-controlled;
  - delete order and contact data that is no longer needed (retention);
  - let suppliers ask to see or correct their data (access and data integrity);
  - have a process for reporting data breaches.
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
