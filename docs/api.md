# Sudahsampai Module Interfaces

> **Everything in this document is simulated.** Orbyte has not given us real APIs. The Orbyte capabilities below are our own **assumptions** about how an Orbyte module would connect to the platform, and our implementation in `src/orbyte_adapter.py` fakes them with a local SQLite database and the Flask session. Replacing that one file is all that is needed to connect to real Orbyte APIs.

## 1. Interfaces this module provides

### `POST /api/delivery-check`

Records a delivery for one order and checks the received quantity. Intended for other Orbyte modules, for example a warehouse scanner. It runs the same logic as the "Confirm receipt" form on the order page.

**Who can call it:** any logged-in Orbyte user (owner or staff). The user comes from the current Orbyte login, which is simulated by the session.

**Request**

```json
{ "order_id": 2, "received_quantity": 13 }
```

**What it does**

1. Records the received quantity and the received date (simulated date).
2. Marks the delivery on time if the received date is on or before the delivery date.
3. If the quantity matches: status becomes `delivered`.
4. If the quantity does not match: status becomes `discrepancy`, and an approval task is sent to the owner's Orbyte to-do list (Work).
5. Charges 1 Orbyte credit.
6. Writes every step to the audit log.

**Response `200`**

```json
{
  "order_id": 2,
  "status": "discrepancy",
  "received_quantity": 13,
  "received_at": "2026-10-21",
  "on_time": true,
  "discrepancy": "Short 2 boxes (ordered 15, received 13) ≈ RM 36.00",
  "approval": { "id": "approval-2-2026-10-21", "assignee": "Ah Seng", "title": "Resolve quantity mismatch" },
  "credits_used_this_month": 1
}
```

When the quantity matches, `discrepancy` and `approval` are `null`.

**Errors**

| Code | When |
|------|------|
| `400` | `order_id` is not an integer, or `received_quantity` is not an integer ≥ 0 |
| `404` | The order does not exist |
| `409` | The order has already been received |

**Example**

```bash
curl -X POST https://sudahsampai.replit.app/api/delivery-check -H "Content-Type: application/json" -d '{"order_id": 2, "received_quantity": 13}'
```

### Web pages

The web pages (order list, new order, order detail, activity log) are described in the main [README](../README.md). Owner-only actions (change reminder rules, resolve a discrepancy, reset demo data) are protected on the server by `@owner_required`.

## 2. Orbyte capabilities this module depends on

All calls go through `src/orbyte_adapter.py`. Each function is a **mock** today.

| Orbyte capability (assumed) | Adapter function | What it does here (simulated) | Planned real call (assumption) |
|-----------------------------|------------------|-------------------------------|--------------------------------|
| **People** - users and roles | `get_current_user()` | Reads the demo role from the session and returns a fictional user (Ah Seng, owner / Mei Ling, staff) | Current user and role from Orbyte single sign-on |
| **People** - contacts | `get_supplier_contact(order)` | Returns the supplier name and WhatsApp number stored on the order | Supplier record from the Orbyte contact directory |
| **Connect** - messaging | `send_message(contact, text)` | Does **not** send anything; returns a receipt with `status: "simulated"` | Send the message through Orbyte Connect's WhatsApp channel |
| **Work** - approvals / to-dos | `create_approval(conn, order_id, title, detail)` | Writes an `approval_created` audit entry as the "task sent to the owner" | Create an approval task in the owner's Orbyte Work to-do list |
| **Credit wallet** | `charge_credit(conn, reason, order_id)` | Writes a `credit_charged` audit entry; monthly usage = number of these entries in the simulated month. No real payment. | Deduct credits from the shop's Orbyte credit wallet |
| **Audit log** | `write_audit(conn, action, detail, order_id, actor)` | Writes to the local SQLite `logs` table | Write to Orbyte's shared audit log |

### When credits are charged

| Event | Credits |
|-------|---------|
| Each automatic chase message to a supplier | 1 |
| Each delivery check (web form or `POST /api/delivery-check`) | 1 |

Reminders, overdue notes, rule changes and discrepancy decisions are free.

## 3. AI model used to read supplier replies

The AI step (in `src/ai_parser.py`) calls any **OpenAI-compatible** chat API. Which service and model it uses is set only by environment variables (Replit Secrets), so switching between DeepSeek and RunPod needs no code change:

| Variable | DeepSeek | RunPod public endpoint (GLM-5.3) |
|----------|----------|----------------------------------|
| `AI_BASE_URL` | `https://api.deepseek.com` | `https://api.runpod.ai/v2/<ENDPOINT_ID>/openai/v1` |
| `AI_API_KEY` | DeepSeek API key | RunPod API key |
| `AI_MODEL` | `deepseek-chat` | `glm-5.3` |
| `AI_MAX_TOKENS` (optional) | not needed (default 400) | raise it if the model reasons before answering |
| `AI_REASONING_EFFORT` (optional) | leave unset | e.g. `low` |

- The page shows which model answered, e.g. "AI: deepseek-chat (DeepSeek)" or "AI: glm-5.3 (RunPod)". It never shows variable names, addresses or keys.
- The app first asks for strict JSON Schema output and, if the service does not support it, falls back to plain JSON mode. Answers wrapped in a ```json code block or surrounded by extra text are unwrapped first; if no JSON object can be found, the result is marked "needs human check".
- If the variables are missing, the request takes longer than 15 seconds, or the service returns an error, the app switches to offline keyword rules.
- `scripts/compare_models.py` runs all sample replies against the currently configured model and prints intent, extracted values, confidence, time and whether a human check is needed, for side-by-side comparison.

## 4. Assumptions and limits

- The function names, parameters and return values are our design; they are not taken from any published Orbyte documentation.
- No real WhatsApp messages are sent and no real money or credits are charged.
- The users Ah Seng and Mei Ling are fictional.
- `POST /api/delivery-check` has no API token yet; in a real deployment it would require an Orbyte access token and the same role checks as the web pages.
