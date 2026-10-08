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

## 5. Part 3 — extraction pipeline demo (real gpt-4o API calls)

Four fresh messages were inserted for riverside members (new rows only — seeded data
untouched): a two-part investment message, a hedged statement, a health-related
message, and small talk. Message ids 19–22.

### Batch run 1 — real API extraction

```
$ curl -s -X POST "http://localhost:8000/clubs/riverside/extract-attributes" \
    -H "X-Member-Token: riverside-admin" -H "Content-Type: application/json" \
    -d '{"message_ids":[19,20,21,22]}'
{
  "results": [
    {"message_id": 19, "status": "processed", "attributes_created": 2},
    {"message_id": 20, "status": "processed", "attributes_created": 1},
    {"message_id": 21, "status": "processed", "attributes_created": 2},
    {"message_id": 22, "status": "processed", "attributes_created": 0}
  ],
  "summary": {"processed": 4, "already_processed": 0, "failed": 0, "not_found": 0}
}

$ docker exec attachai-ai-ml-eng-db-1 psql -U kindred -d kindred -c \
    "SELECT source_message_id AS msg, member_id, kind, confidence, restricted, text
     FROM member_attributes WHERE source_message_id IN (19,20,21,22) ORDER BY source_message_id;"
 msg | member_id |   kind   | confidence | restricted |                        text
-----+-----------+----------+------------+------------+----------------------------------------------------
  19 |        20 | need     |        0.9 | f          | looking for introductions to climate-tech founders
  19 |        20 | offer    |       0.95 | f          | actively investing in climate-tech this quarter
  20 |        21 | interest |        0.3 | f          | might get into sailing next summer
  21 |        22 | context  |       0.95 | t          | seeing a physiotherapist for a back injury
  21 |        22 | interest |        0.9 | f          | unable to play tennis this season due to injury
(5 rows)
```

Notable: message 21 was split — the health fact is flagged `restricted=t`, the
non-clinical tennis note is not. Message 20's hedge ("might... not sure yet")
got confidence 0.3. Message 22 (small talk) correctly produced zero attributes.

### Batch run 2 — identical request, idempotency proof

```
$ curl -s -X POST "http://localhost:8000/clubs/riverside/extract-attributes" \
    -H "X-Member-Token: riverside-admin" -H "Content-Type: application/json" \
    -d '{"message_ids":[19,20,21,22]}'
{"results":[
  {"message_id":19,"status":"already_processed","attributes_created":0},
  {"message_id":20,"status":"already_processed","attributes_created":0},
  {"message_id":21,"status":"already_processed","attributes_created":0},
  {"message_id":22,"status":"processed","attributes_created":0}],
 "summary":{"processed":1,"already_processed":3,"failed":0,"not_found":0}}

$ docker exec ... "SELECT count(*) FROM member_attributes WHERE source_message_id IN (19,20,21,22);"
     5        <-- unchanged: no duplicate rows
```

(Message 22 yielded zero attributes, so no row marks it processed — it is
re-extracted but still writes nothing: idempotent in effect. See DESIGN_NOTES.)

### Authorization

```
$ curl -s -i -X POST ".../clubs/riverside/extract-attributes" -H "X-Member-Token: riverside-member-1" ...
HTTP/1.1 403 Forbidden
{"detail":"admin role required"}

$ curl -s -i -X POST ".../clubs/riverside/extract-attributes" -H "X-Member-Token: oakhurst-admin" ...
HTTP/1.1 403 Forbidden
{"detail":"not an admin of this club"}

# a riverside message id submitted by the oakhurst admin against their own club:
$ curl -s -X POST ".../clubs/oakhurst/extract-attributes" -H "X-Member-Token: oakhurst-admin" \
    -d '{"message_ids":[21]}'
{"results":[{"message_id":21,"status":"not_found","attributes_created":0}], ...}
     <-- indistinguishable from a nonexistent id: no cross-club existence oracle
```

### Restricted enforcement at the matching boundary

```
>>> build_member_profile_text(member_22, db)
'unable to play tennis this season due to injury'
     <-- the restricted physiotherapist attribute is excluded from the text that
         feeds profile_embedding and therefore candidate ranking
```

### Failure isolation (test double — real 5xx cannot be triggered on demand)

```
$ python -m pytest "tests/test_extraction.py::TestExtractionFailureIsolation" -v
tests/test_extraction.py::TestExtractionFailureIsolation::test_one_permanent_failure_does_not_fail_batch PASSED
```

(Batch of 3 where the middle message fails permanently: response statuses are
processed/failed/processed, and the two successes' rows are committed.)

## 6. Part 3 — eval run (real API, gpt-4o)

```
$ python -m eval.run_eval --threshold 0.75
[1/4] "I'm trying to raise a Series A in the next few months, would love i..."
      expected: kind=need restricted=False keywords=['series a', 'vc']
      extracted: kind=need confidence=0.9 restricted=False text='raising a Series A, looking for VC introductions'
      kind=PASS keywords=PASS restricted=PASS -> record PASS

[2/4] "I've led fundraising for three Series A rounds as an operator, happ..."
      expected: kind=offer restricted=False keywords=['series a', 'fundrais']
      extracted: kind=offer confidence=0.95 restricted=False text='experienced in leading fundraising for Series A rounds, willing to help others'
      extracted: kind=context confidence=0.95 restricted=False text='has led fundraising for three Series A rounds as an operator'
      kind=PASS keywords=PASS restricted=PASS -> record PASS

[3/4] "I've been in therapy for the last year and it's helped a lot, just ..."
      expected: kind=context restricted=True keywords=['therapy']
      extracted: kind=context confidence=0.9 restricted=True text='has been in therapy for the last year, finds it helpful'
      kind=PASS keywords=PASS restricted=PASS -> record PASS

[4/4] 'Does anyone want to join a weekly running club on Tuesday mornings?'
      expected: kind=interest restricted=False keywords=['running']
      extracted: kind=interest confidence=0.9 restricted=False text='looking for members to join a weekly running club on Tuesday mornings'
      kind=PASS keywords=PASS restricted=PASS -> record PASS

Overall: 4/4 records passed (score 1.00, threshold 0.75)
Restricted violations: 0 (zero tolerance)
RESULT: PASS
exit code: 0
```

Threshold calibration: the eval was run 5 times consecutively; all 5 runs scored
4/4 with 0 restricted violations. Threshold rationale in DESIGN_NOTES.

## 7. Final test run

```
$ python -m pytest -q
25 passed, 1 warning in 14.37s
```

(7 starter tests + 7 Part 2 tests + 11 Part 3 tests.)
