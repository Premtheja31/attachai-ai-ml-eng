# Kindred Concierge — Assessment Starter

A small FastAPI + PostgreSQL/pgvector service simulating Kindred's member-matching and concierge backend. Used for the Round 3 take-home — see the assignment brief you were given for what to build.

## Setup

1. `docker compose up -d db` — starts Postgres with the `pgvector` extension on `localhost:5432`. If you already have something on 5432 locally, change the port mapping in `docker-compose.yml` and update `DATABASE_URL` to match.
2. `python3 -m venv .venv && source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. `cp .env.example .env` (defaults match the docker-compose service)
5. `python -m scripts.seed` — creates tables, wipes and re-seeds two clubs (`riverside`, `oakhurst`) with members, conversation history, extracted attributes, a knowledge base, and a couple of bookings. Prints every member's auth token when done.
6. `uvicorn app.main:app --reload` — runs the API on `http://localhost:8000`.

Run the tests with `pytest` — they run against `kindred_test` (created automatically by `init-scripts/init-test-db.sql` the first time the `db` container starts fresh; if you'd already started the container before pulling this, run `docker compose down -v` once to force a clean init).

## Auth

There's no login flow — every member has a fixed bearer-style token, sent as `X-Member-Token`. The seed script prints all of them; the two you'll use most:

- `riverside-admin`, `oakhurst-admin` — the admin/service role for each club
- `riverside-member-1` through `riverside-member-6`, and the same for `oakhurst`

## What's already built

- `GET /clubs/{club_id}/knowledge/query?q=...` — RAG-style lookup over a club's knowledge base
- `GET /members/{member_id}/candidates?reason=...` — ranks other members in the same club as introduction candidates
- `GET /introductions/{member_a_id}/{member_b_id}?reason=...` — generates a human-readable reason for an introduction
- `POST /bookings/{booking_id}/confirm-payment` — charges a booking via a mock payment client
- `POST /sessions`, `GET /sessions/{id}`, `POST /sessions/{id}/turn` — a minimal multi-turn booking flow (book → confirm → charge), plus a `refund_dispute` intent that always escalates the session instead of being auto-resolved
- `GET /sessions/debug/payment-charge-log` (admin only) — every charge attempted against the mock payment client, in order

All of it runs with zero external API keys — `app/embeddings.py` is a deterministic, local, hash-based stand-in for a real embeddings API, used everywhere in the pre-existing code. **Part 3 of the assignment (which you're building, not reviewing) is the one place a real LLM API is required** — see `app/llm_client.py` for the interface you're implementing against.

## Notes for your own testing

- The mock payment client (`app/services/payment_mock.py`) always succeeds for a normal amount, always fails for `amount_cents <= 0`, and raises a simulated timeout (`PaymentTimeoutError`, surfaced as a 504) for `amount_cents: 999999`.
- A session's `turn` payload accepts an optional `"simulate_crash": true` field, for your own testing.
- `eval/golden_set.json` is a small labeled set of raw messages for the extraction pipeline's eval requirement (see the assignment brief, Part 3).

## Submission

- **Screen recording (entire session, narrated):** https://drive.google.com/file/d/1ZaVGyleK10LQY2hNguq-PT7rl1hZ73Qd/view?usp=sharing
- Code review: [REVIEW.md](REVIEW.md) · Terminal evidence: [TERMINAL_LOG.md](TERMINAL_LOG.md) · Design reasoning: [DESIGN_NOTES.md](DESIGN_NOTES.md) · AI tool dialogue: [.ai-conversations/](.ai-conversations/)
- Part 3 extraction pipeline: `POST /clubs/{club_id}/extract-attributes` (`app/custom_model/`, `app/services/extraction_service.py`, `app/routers/extraction.py`); eval: `python -m eval.run_eval --threshold 0.75`
- Run the tests with `pytest` — 25 tests (7 starter + 7 Part 2 + 11 Part 3). Part 3's pipeline requires `OPENAI_API_KEY` in `.env`; tests do not (they use the provided test double).
