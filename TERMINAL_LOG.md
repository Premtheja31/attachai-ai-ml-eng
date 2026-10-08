# TERMINAL_LOG.md

Literal terminal output at each stage. (Sections for the Part 2 fix traces, Part 3 extraction/eval runs, and the final test run are appended as those parts are completed.)

## 1. Setup

```
$ docker compose ps
NAME                      IMAGE                    COMMAND                  SERVICE   CREATED          STATUS                    PORTS
attachai-ai-ml-eng-db-1   pgvector/pgvector:pg16   "docker-entrypoint.s…"   db        25 minutes ago   Up 25 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp

$ curl -s http://localhost:8000/health
{"status":"ok"}
```

Seed state (after `python -m scripts.seed`):

```
$ docker exec attachai-ai-ml-eng-db-1 psql -U kindred -d kindred -c "SELECT id, club_id, token, role FROM members ORDER BY id;"
 id |  club_id  |       token        |  role
----+-----------+--------------------+--------
  1 | riverside | riverside-admin    | admin
  2 | oakhurst  | oakhurst-admin     | admin
  3 | riverside | riverside-member-1 | member
  4 | riverside | riverside-member-2 | member
  5 | riverside | riverside-member-3 | member
  6 | riverside | riverside-member-4 | member
  7 | riverside | riverside-member-5 | member
  8 | riverside | riverside-member-6 | member
  9 | oakhurst  | oakhurst-member-1  | member
 10 | oakhurst  | oakhurst-member-2  | member
 11 | oakhurst  | oakhurst-member-3  | member
 12 | oakhurst  | oakhurst-member-4  | member
 13 | oakhurst  | oakhurst-member-5  | member
 14 | oakhurst  | oakhurst-member-6  | member
(14 rows)
```

## 2. Initial test run (before any fixes)

```
$ python -m pytest -q
7 passed, 1 warning in 3.74s
```

(One pre-existing wrinkle: adding `OPENAI_API_KEY` to `.env` initially broke `Settings` import — pydantic-settings forbids extra keys — fixed by declaring `openai_api_key` in `app/config.py`, which Part 3 needs anyway.)

## 3. Part 1 — bug traces (literal request/response from the running instance)

### Trace 1: cross-club restricted-data leak (REVIEW.md Issue 1)

```
$ curl -s -i "http://localhost:8000/introductions/5/3?reason=business" -H "X-Member-Token: oakhurst-member-1"
HTTP/1.1 200 OK
content-type: application/json

{"reason_text":"Because manages anxiety, prefers quiet low-key venues and raising a seed round, looking for angel investors; might be raising again next year — a good business match."}

$ curl -s "http://localhost:8000/introductions/999/998?reason=business" -H "X-Member-Token: oakhurst-member-1"
{"reason_text":"Because  and  — a good business match."}
```

Caller is an oakhurst member; members 5 and 3 are riverside. Member 5's only attribute is seeded `restricted=true`.

### Trace 2: session-flow double charge (REVIEW.md Issue 2)

```
$ curl -s "http://localhost:8000/sessions/debug/payment-charge-log" -H "X-Member-Token: riverside-admin"
{"charges":[4000,4000]}          <-- ledger before this scenario (earlier run)

$ curl -s -X POST "http://localhost:8000/sessions" -H "X-Member-Token: riverside-member-3"
{"session_id":2,"status":"active"}

$ curl -s -X POST "http://localhost:8000/sessions/2/turn" -H "X-Member-Token: riverside-member-3" \
    -H "Content-Type: application/json" -d '{"intent":"book","party_size":2}'
{"status":"awaiting_confirmation","party_size":2}

$ curl -s -i -X POST "http://localhost:8000/sessions/2/turn" -H "X-Member-Token: riverside-member-3" \
    -H "Content-Type: application/json" -d '{"intent":"affirm","amount_cents":4000,"simulate_crash":true}'
HTTP/1.1 500 Internal Server Error
{"detail":"simulated crash after charge, before persistence"}

$ curl -s "http://localhost:8000/sessions/2" -H "X-Member-Token: riverside-member-3"
{"session_id":2,"status":"active","party_size":2,"awaiting_confirmation":true,"booking_id":null}

$ curl -s -X POST "http://localhost:8000/sessions/2/turn" -H "X-Member-Token: riverside-member-3" \
    -H "Content-Type: application/json" -d '{"intent":"affirm","amount_cents":4000}'
{"status":"confirmed","booking_id":4}

$ curl -s "http://localhost:8000/sessions/debug/payment-charge-log" -H "X-Member-Token: riverside-admin"
{"charges":[4000,4000,4000,4000]}    <-- TWO new 4000¢ charges, ONE booking
```

### Trace 3: client-controlled amount (REVIEW.md Issue 3)

```
$ curl -s -i -X POST "http://localhost:8000/bookings/1/confirm-payment" \
    -H "X-Member-Token: riverside-member-1" -H "Content-Type: application/json" -d '{"amount_cents":1}'
HTTP/1.1 200 OK
{"status":"succeeded","attempt_id":3,"booking_status":"confirmed"}

$ docker exec attachai-ai-ml-eng-db-1 psql -U kindred -d kindred -t -c "SELECT id, amount_cents, status FROM bookings WHERE id=1;"
  1 |         8000 | confirmed
```

Seeded booking 1 (8000¢, pending) confirmed for 1¢.

### Trace 4: cross-club knowledge leak (REVIEW.md Issue 6)

```
$ curl -s "http://localhost:8000/clubs/riverside/knowledge/query?q=collared%20shirts%20dining" \
    -H "X-Member-Token: riverside-member-1" | python3 -m json.tool
[
    {"chunk_id": 6, "club_id": "oakhurst", "title": "Dress code",
     "body": "Oakhurst requires collared shirts in all dining areas, no exceptions."},
    {"chunk_id": 4, "club_id": "riverside", "title": "Cancellation policy",
     "body": "Riverside bookings can be cancelled up to 24 hours ahead for a full refund."},
    {"chunk_id": 1, "club_id": "riverside", "title": "Guest fees",
     "body": "Riverside guest fees are $50 per visit, waived for members' immediate family."},
    {"chunk_id": 5, "club_id": "oakhurst", "title": "Guest fees",
     "body": "Oakhurst guest fees are $75 per visit, capped at two guests per member per month."},
    {"chunk_id": 7, "club_id": "oakhurst", "title": "Opening hours",
     "body": "Oakhurst is open 6am to midnight, the pool closes at 9pm."}
]
```

A riverside member querying riverside's own KB receives three oakhurst chunks.

## 4. Part 2 — fix traces

> Note: the DB was re-seeded between Parts 1 and 2 (and the seed wipe-list gained
> `ConversationSession`, which the starter forgot — re-running the seed after using the
> session flow hit a FK violation). Postgres sequences don't reset on DELETE, so IDs
> shifted: members are now 15–28 (riverside 17–22, oakhurst 23–28); member 19 is the
> riverside member whose only attribute is `restricted=true`.

### Fix 1: introductions endpoint (REVIEW.md Issue 1)

BEFORE (same bug as Part 1, current IDs — oakhurst caller, riverside targets):

```
$ curl -s -i "http://localhost:8000/introductions/19/17?reason=business" -H "X-Member-Token: oakhurst-member-1"
HTTP/1.1 200 OK
{"reason_text":"Because manages anxiety, prefers quiet low-key venues and raising a seed round, looking for angel investors; might be raising again next year — a good business match."}
```

AFTER:

```
$ curl -s -i "http://localhost:8000/introductions/19/17?reason=business" -H "X-Member-Token: oakhurst-member-1"
HTTP/1.1 403 Forbidden
{"detail":"not a member of this club"}

# same-club caller, but target 19 has ONLY a restricted attribute -> nothing leaks:
$ curl -s "http://localhost:8000/introductions/19/17?reason=business" -H "X-Member-Token: riverside-member-1"
{"reason_text":"insufficient basis for an introduction"}

# same-club happy path: restricted and low-confidence (0.3) attributes excluded:
$ curl -s "http://localhost:8000/introductions/17/18?reason=business" -H "X-Member-Token: riverside-member-1"
{"reason_text":"Because raising a seed round, looking for angel investors and angel investor, has backed a dozen seed-stage startups — a good business match."}

# nonexistent members no longer produce empty reasons:
$ curl -s -i "http://localhost:8000/introductions/999/998?reason=business" -H "X-Member-Token: riverside-member-1"
HTTP/1.1 404 Not Found
{"detail":"member not found"}
```

### Fix 2: session-flow double charge (REVIEW.md Issue 2)

BEFORE (fresh charge log after server restart):

```
$ curl -s "http://localhost:8000/sessions/debug/payment-charge-log" -H "X-Member-Token: riverside-admin"
{"charges":[]}
# create session (riverside-member-2) -> session_id=3; book party_size=2
$ curl ... -d '{"intent":"affirm","amount_cents":4000,"simulate_crash":true}'
{"detail":"simulated crash after charge, before persistence"}
$ curl ... -d '{"intent":"affirm","amount_cents":4000}'
{"status":"confirmed","booking_id":7}
$ curl -s "http://localhost:8000/sessions/debug/payment-charge-log" -H "X-Member-Token: riverside-admin"
{"charges":[4000,4000]}            <-- TWO charges, ONE booking
```

AFTER (fresh charge log after reload):

```
$ curl -s "http://localhost:8000/sessions/debug/payment-charge-log" -H "X-Member-Token: riverside-admin"
{"charges":[]}
# create session (riverside-member-4) -> session_id=4; book party_size=2
$ curl ... -d '{"intent":"affirm","amount_cents":4000,"simulate_crash":true}'
{"detail":"simulated crash after charge, before persistence"}
$ curl ... -d '{"intent":"affirm","amount_cents":4000}'
{"status":"confirmed","booking_id":8}
$ curl -s "http://localhost:8000/sessions/debug/payment-charge-log" -H "X-Member-Token: riverside-admin"
{"charges":[4000]}                 <-- crash + retry, but only ONE charge
```

### Test run after Part 2 fixes

```
$ python -m pytest -q
14 passed, 1 warning in 4.50s
```

(7 pre-existing tests + 7 new: 4 covering the introductions authorization/filtering, 3 covering charge idempotency and failed-charge state.)
