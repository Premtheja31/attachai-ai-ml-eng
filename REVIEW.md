# REVIEW.md — Code Review (Part 1)

Issues found in the starter codebase, **prioritized by business impact**. Kindred's product is discretion and trust for private-club members, so issues that leak what members confided, charge their cards incorrectly, or break the per-club isolation promise rank above everything else.

All traces below are literal output captured from my running local instance (seeded via `python -m scripts.seed`, API on `http://localhost:8000`).

## Summary (ranked)

| # | Issue | Location | Category | Severity |
|---|-------|----------|----------|----------|
| 1 | Introductions endpoint leaks restricted health data, cross-club, to any valid token | `app/routers/introductions.py:19-25` | Security / Data Isolation | **Critical** |
| 2 | Charge-before-persist in session flow: crash + retry double-charges the card | `app/services/session_flow.py:36-39` | Data Integrity | **Critical** |
| 3 | Client controls the payment amount: 1¢ confirms an $80 booking | `app/routers/bookings.py:29` | Security / Data Integrity | **Critical** |
| 4 | Confirm-payment is replayable; provider timeout leaves no record | `app/routers/bookings.py:13-43` | Data Integrity | High |
| 5 | Session marked "confirmed" even when the charge failed | `app/services/session_flow.py:46,52` | Data Integrity | High |
| 6 | Knowledge similarity search not filtered by club — cross-club KB leak | `app/routers/knowledge.py:28-33` | Data Isolation | High |
| 7 | Restricted attributes baked into matching profile text / embeddings | `app/services/matching_service.py:10-12` | Security / Data Integrity | High |

Additional lower-severity observations are listed at the end.

---

## Issue 1 — Introductions endpoint leaks restricted attributes to anyone, across clubs

- **File/line:** `app/routers/introductions.py:19-25` (queries at 19-20, verbatim join at 23-24)
- **Category:** Security / Data Isolation
- **Severity:** Critical

The endpoint's only gate is `get_current_member` — i.e. "possesses *some* valid token". There is no check that the caller is either member, that the caller shares a club with them, or even that `member_a` and `member_b` are in the same club as each other. Every `MemberAttribute` of both targets — including rows with `restricted=True` (health/clinical data) — is concatenated verbatim into `reason_text`, and low-confidence speculation (0.3) is stated as fact. Any member of any club can enumerate integer member IDs and dump every member's confided information; the victim never knows.

Why the nearby check doesn't catch it: the `member: Member = Depends(get_current_member)` parameter authenticates the caller but the resolved member is never used — no comparison against `member_a_id`/`member_b_id` or their `club_id` ever happens.

**Recommended fix:** require the caller's `club_id` to match both members' `club_id` (404 on cross-club or nonexistent IDs, to avoid existence oracles); filter attributes to `restricted == False` and `confidence >= threshold`; return "insufficient basis" when nothing qualifies (this also sets up Part 4a).

**Trace (literal):** an *oakhurst* member reading two *riverside* members' attributes — including a restricted mental-health attribute:

```
GET /introductions/5/3?reason=business
X-Member-Token: oakhurst-member-1

HTTP/1.1 200 OK
content-type: application/json

{"reason_text":"Because manages anxiety, prefers quiet low-key venues and raising a seed round, looking for angel investors; might be raising again next year — a good business match."}
```

Member 5 is Riverside Member 3, whose only attribute is seeded `restricted=true` ("manages anxiety, prefers quiet low-key venues"). The caller is from a different club. Nonexistent IDs don't even 404:

```
GET /introductions/999/998?reason=business
X-Member-Token: oakhurst-member-1

{"reason_text":"Because  and  — a good business match."}
```

---

## Issue 2 — Session flow double-charges on crash + retry

- **File/line:** `app/services/session_flow.py:36` (charge), `:38-55` (persistence only after)
- **Category:** Data Integrity
- **Severity:** Critical

In the `affirm` turn, `payment_mock_client.charge()` runs **before anything is persisted** — no payment attempt row, no idempotency key, no "has this session already charged?" guard. If the process dies between the charge and the commit (deterministically reproducible with `"simulate_crash": true`), the DB retains no evidence a charge happened: the session is still `awaiting_confirmation` with no booking. The client's natural response to a 500 is to retry — which charges the card a second time. Every network blip or deploy during a payment turn is a potential duplicate charge and chargeback.

**Recommended fix:** persist a pending `PaymentAttempt` with a deterministic idempotency key (e.g. derived from the session ID) *before* calling the provider, pass the key to the provider, and short-circuit `affirm` if a succeeded/pending attempt already exists for the session. Reconcile ambiguous outcomes (timeout/crash) instead of blindly re-charging.

**Trace (literal):** provider ledger before this scenario contained two charges from an earlier run; watch it gain **two** 4000¢ charges for **one** booking:

```
GET /sessions/debug/payment-charge-log   (admin)
{"charges":[4000,4000]}                  <-- before this scenario

POST /sessions                      -> {"session_id": 2}
POST /sessions/2/turn  {"intent":"book","party_size":2}
                                    -> {"status":"awaiting_confirmation","party_size":2}

POST /sessions/2/turn  {"intent":"affirm","amount_cents":4000,"simulate_crash":true}
HTTP/1.1 500 Internal Server Error
{"detail":"simulated crash after charge, before persistence"}

GET /sessions/2                     -> {"session_id":2,"status":"active","party_size":2,
                                        "awaiting_confirmation":true,"booking_id":null}

POST /sessions/2/turn  {"intent":"affirm","amount_cents":4000}   <-- the natural retry
                                    -> {"status":"confirmed","booking_id":4}

GET /sessions/debug/payment-charge-log   (admin)
{"charges":[4000,4000,4000,4000]}        <-- TWO new charges, ONE booking
```

---

## Issue 3 — Client-controlled payment amount confirms bookings for 1¢

- **File/line:** `app/routers/bookings.py:29` (charges `payload.amount_cents`, never reads `booking.amount_cents`)
- **Category:** Security / Data Integrity
- **Severity:** Critical

`confirm-payment` charges whatever `amount_cents` the client sends. The booking's own `amount_cents` is never consulted. A member can confirm any of their bookings by paying one cent — the club silently loses the difference on every booking paid this way.

Why the nearby check doesn't catch it: the ownership filter (`Booking.member_id == member.id`, line 22) correctly stops members paying *other people's* bookings, but says nothing about the *amount* — validation of the charge against the booking simply doesn't exist.

**Recommended fix:** ignore the client-supplied amount entirely — charge `booking.amount_cents` (and reject if the booking isn't in a payable state, see Issue 4).

**Trace (literal):** seeded booking 1 is 8000¢ (pending), owner `riverside-member-1`:

```
POST /bookings/1/confirm-payment
X-Member-Token: riverside-member-1
{"amount_cents": 1}

HTTP/1.1 200 OK
{"status":"succeeded","attempt_id":3,"booking_status":"confirmed"}
```

DB afterwards: `SELECT id, amount_cents, status FROM bookings WHERE id=1;` → `1 | 8000 | confirmed` — an $80 booking confirmed for one cent.

---

## Issue 4 — Confirm-payment is replayable; timeouts leave no record

- **File/line:** `app/routers/bookings.py:13-43` (no status guard; timeout handler at 30-31 persists nothing)
- **Category:** Data Integrity
- **Severity:** High

Two gaps in the same handler. (a) There is no check of `booking.status` and no idempotency: re-POSTing against an already-`confirmed` booking charges the provider again (the `payment_attempts.idempotency_key` column exists in the schema but is never written by any code path). (b) On `PaymentTimeoutError` the handler raises a 504 **without persisting any `PaymentAttempt`** — but a provider timeout is an *ambiguous* outcome: the charge may have succeeded. The system keeps no evidence the attempt happened, so the inevitable retry is blind (same double-charge family as Issue 2).

**Recommended fix:** reject confirm-payment unless `booking.status == "pending"`; persist the attempt (with idempotency key) before charging; record timeouts as `status="unknown"` for reconciliation.

---

## Issue 5 — Session reports "confirmed" even when the charge failed

- **File/line:** `app/services/session_flow.py:46` (booking status reflects outcome) vs `:52` (session status unconditionally `"confirmed"`)
- **Category:** Data Integrity
- **Severity:** High

In `affirm`, `amount_cents` defaults to `0` (and is client-controlled — session-flow sibling of Issue 3). A 0-amount charge *fails*, the booking is correctly left `"pending"` — but `session.status` is set to `"confirmed"` unconditionally and the member is told `{"status": "confirmed"}`. The member believes they're booked; the club has an unpaid pending booking.

**Recommended fix:** derive the session state and response from the charge result; require a positive amount sourced from the booking context, not the payload.

---

## Issue 6 — Knowledge similarity search is not filtered by club

- **File/line:** `app/routers/knowledge.py:28-33` (membership check at 19-20 doesn't constrain the query)
- **Category:** Data Isolation
- **Severity:** High

The route correctly 403s callers asking about a club they don't belong to — but the nearest-neighbour search itself runs over **every club's** `knowledge_chunks` with no `club_id` filter. Whatever is most similar wins, regardless of club. Any club's internal policies/documents leak to every other club's members, violating the core isolation promise ("Each club's members, knowledge, and data are isolated from every other club's").

Why the nearby check doesn't catch it: the 403 validates the *path parameter* against the caller; the *query* at line 28-33 never uses `club_id` at all.

**Recommended fix:** add `.filter(KnowledgeChunk.club_id == club_id)` to the query.

**Trace (literal):** a riverside member querying riverside's own KB receives three **oakhurst** chunks:

```
GET /clubs/riverside/knowledge/query?q=collared%20shirts%20dining
X-Member-Token: riverside-member-1

HTTP/1.1 200 OK
[
  {"chunk_id": 6, "club_id": "oakhurst", "title": "Dress code",
   "body": "Oakhurst requires collared shirts in all dining areas, no exceptions."},
  {"chunk_id": 4, "club_id": "riverside", "title": "Cancellation policy", ...},
  {"chunk_id": 1, "club_id": "riverside", "title": "Guest fees", ...},
  {"chunk_id": 5, "club_id": "oakhurst", "title": "Guest fees",
   "body": "Oakhurst guest fees are $75 per visit, capped at two guests per member per month."},
  {"chunk_id": 7, "club_id": "oakhurst", "title": "Opening hours", ...}
]
```

---

## Issue 7 — Restricted attributes feed the matching pipeline

- **File/line:** `app/services/matching_service.py:10-12` (`build_member_profile_text` joins all attributes; no `restricted` or confidence filter)
- **Category:** Security / Data Integrity
- **Severity:** High

The profile text used to build `profile_embedding` — and therefore candidate ranking — includes `restricted=True` attributes (the seed then bakes them into every member's stored embedding). Restricted health/clinical data silently influences who gets introduced to whom; combined with Issue 1, the raw text is also directly exposed. This is the inverse of the Part 3 hard requirement that restricted attributes never reach matching, and the same helper is reused by `embedding_pipeline.refresh_member_embedding`, so the leak is systemic, not one call site.

**Recommended fix:** filter to `restricted == False` (and a confidence floor) inside `build_member_profile_text` itself, so every consumer — matching, embedding refresh, future extraction re-embeds — inherits the enforcement at a single choke point; re-embed existing members after the fix.

---

## Additional observations (lower severity)

| Issue | Location | Category | Severity |
|-------|----------|----------|----------|
| Requester's embedding and attribute query recomputed inside the candidate loop (O(N) embeds + O(N) queries); ranking done in Python over all members instead of a pgvector query | `app/services/matching_service.py:33-35` | Performance | Medium |
| `refresh_member_embedding` is never called by any code path — profile embeddings go permanently stale once attributes change after seeding | `app/services/embedding_pipeline.py:8` | Data Integrity | Medium |
| Payment charge-log debug endpoint shows **all clubs'** charges to any club's admin; log is per-process in-memory | `app/routers/sessions.py:13-18` | Data Isolation | Medium |
| `turn` accepts a raw unvalidated `dict` (no Pydantic schema; `intent` optional, `party_size` accepts 0/negatives and still advances to `awaiting_confirmation`) | `app/routers/sessions.py:51`, `app/services/session_flow.py:20-24` | Data Integrity | Low |
| `conftest.py` uses `os.environ.setdefault(...)` — if the shell already exports a `DATABASE_URL`, the test suite will wipe *that* database table-by-table | `tests/conftest.py:3-5` | Data Integrity | Low |
| No DB uniqueness on `member_attributes.source_message_id` — extraction idempotency must be enforced at the application level | `app/models.py:53` | Data Integrity | Low (note) |
