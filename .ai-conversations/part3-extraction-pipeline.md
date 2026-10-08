# AI conversation log — Part 3 (extraction pipeline)

Tool: Claude Code (CLI). Condensed transcript of the design-and-build dialogue for
Part 3; prompts are mine, responses are the tool's (abridged to the decision-relevant
parts — full session visible in the screen recording). The same workflow (discuss →
decide → implement) was used for Parts 1 and 2.

---

## Splitting the work and choosing the model

**Me:** lets understand what part 3 is expecting us to do and let us have a chitchat
on what is going to happen and what steps we are gonna take

**AI:** Proposed meeting the six hard requirements: OpenAI client behind the existing
`LLMClient` protocol, idempotency via `source_message_id`, exponential-backoff retries,
per-message failure isolation, admin+club auth, restricted enforcement at
`build_member_profile_text`. Suggested `gpt-4o-mini` as default and a 75% eval
threshold. Asked three questions: model, threshold, sub-part plan.

**Me:** 1. lets use gpt-4o for now, 2. lets discuss why are you suggesting 75% and
what cases it fails for the 100%, 3. lets split part 3 into sub parts and implement
each at a time after discussing.

→ Model decision overridden to gpt-4o. Work split into 3.1 client / 3.2 endpoint /
3.3 tests / 3.4 eval / 3.5 docs+demo.

## Challenging the retry design (3.1)

**Me:** for output formats use pydantic model, for retry strategy why are we taking
3 attempts is it necessary?

**AI:** Agreed Pydantic was the cleaner path (SDK `parse` API with a `Literal` kind).
On retries: retrying at all is a stated requirement; 3 attempts is the knee of the
curve (1 retry under-serves persistent 429s; >3 inflates a synchronous endpoint's
worst case). Conceded the exact count is not sacred — made it `llm_max_attempts`
config instead of a constant. Flagged that the OpenAI SDK silently retries twice on
its own, so it set `max_retries=0` to keep our loop the single, visible retry path.

**Me:** why is it not asynchronous endpoint?

**AI:** Separated async-job API (202 + poll) from async/await concurrency. Rejected
the job API here because job state needs a table ("no new tables/migrations") and the
assignment's demo flow expects per-message results in one response; rejected
`asyncio.gather` fan-out because the codebase is sync SQLAlchemy end-to-end (an async
endpoint doing sync DB calls blocks the event loop) and concurrent fan-out causes the
429s the retry loop then fights. FastAPI runs sync endpoints in a threadpool, so only
the caller waits. Async job queue became the top "one more day" item in DESIGN_NOTES.

## Correcting the AI: file placement (3.1)

The AI's first implementation put the prompt, Pydantic models, and `OpenAILLMClient`
all inside `app/llm_client.py`, under the starter's protocol.

**Me:** instead of app/llm_client.py lets make a custom folder for this openai llm,
where inside the custom_model folder we will have the pydantic models in one file,
prompts in another file, the llm setup in another file with a good structure.

**AI:** Restored `app/llm_client.py` to its pristine starter state and rebuilt as a
package: `app/custom_model/{schemas.py, prompts.py, openai_client.py, __init__.py}`.
Verified `git diff` on the starter file came back empty afterwards, and re-ran the
real-API smoke test through the new import path.

**Me (follow-up check):** did the accuracy go down? from before?
*(the new smoke test printed confidence 0.9 where the earlier one printed 0.3)*

**AI:** No — the two tests used different input messages (hedged "might try padel"
vs. concrete "I need a padel partner every week"); the restructure moved code
byte-for-byte. The 0.3 vs 0.9 gap is the confidence-calibration rule working, not
drift.

## Correcting the AI: code style (3.2, 3.3)

The AI wrote the extraction service as a module-level function.

**Me:** and keep the extraction_service.py in a class based format

**AI:** Rewrote as `ExtractionService` (constructor takes `llm`, `db`; one public
method; private `_club_messages/_process_one/_already_processed/_refresh_embeddings/
_summarize`).

**Me (after it also wrote tests as functions):** make this test_extraction.py in a
class based code … when i say class based, the code which we are implementing should
be class based not the existing code

**AI:** Rebuilt the test file as classes (`ExtractionTestBase` + four test classes +
the retry-classification class); kept starter code and FastAPI routers function-based
per framework convention, which I accepted.

## The eval threshold: settled by experiment (3.4)

**Me:** for part 2 [of the questions] lets test with both 100 and 75 percent and see
the results and will decide based on that

**AI:** Built the eval with `--threshold` as a flag, pointed out thresholds are
applied post-hoc so one set of runs answers both variants, and ran it 5 times.

Result: 5/5 runs at 4/4, zero restricted violations.

**Me:** how keeping 0.75 and 1 affect the assignment?

**AI:** The brief grades the justification, not the number. Asymmetry: at 0.75 a
single borderline wobble during the recorded demo still passes (with output showing
which record and why); at 1.0 the same wobble puts a FAIL in the submitted demo.
Restricted-zero-tolerance fails the run in both variants, so the privacy guarantee is
unaffected by the choice.

**Me:** keep it 0.75 then

→ Final: threshold 0.75, restricted mismatches fail regardless of score, measured
baseline 5/5 at 100% documented in DESIGN_NOTES.
