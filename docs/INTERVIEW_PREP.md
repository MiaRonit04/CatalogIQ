# CatalogIQ interview preparation

Use these as answer outlines, not a script to memorize. Read the named code and explain it in your own words. If you cannot explain a line, run a tiny example and observe it. The interview explicitly includes a new dataset and a small change without AI.

## Your 60-second explanation

“CatalogIQ accepts messy product listings and enriches them in the background. The HTTP handler validates and saves a job in SQLite, then responds without waiting for the LLM. A fixed pool of N worker threads processes durable queue rows across all jobs. Before calling the model, a worker checks a persistent content cache. Simultaneous duplicates share a Future so only one owner does the work. Every provider response is validated; failures get three retries after 200, 400 and 800 milliseconds. Product writes and job counters commit together. The plain JavaScript UI uploads CSV, polls progress, supports search and lets a human approve results. I optimized for a small, testable local implementation and documented the distributed-system changes needed at scale.”

Do not say “production-ready,” “exactly once” or “AI guarantees accurate categories.” None is true.

## Read the code in this order

1. `providers.py`: `Config`, `Provider`, mock, real adapter, `validate_output`.
2. `store.py`: `content_key`, `submit`, `claim`, `complete`.
3. `pipeline.py`: `_worker`, `_enrich`, `_attempts`.
4. `server.py`: `validate_products`, `route`, `validate_patch`.
5. `static/csv.js` and `static/app.js`: CSV parsing, job polling, search debounce, review.
6. Tests: identify the behavior each assertion proves, especially the independently measured concurrency peak.

Trace one row with a pencil: POST → validation → jobs/items → claim → cache/Future → provider → schema validation → cache commit → product/progress transaction → GET.

## Pipeline questions

### 1. Why use threads instead of asyncio or multiprocessing?

The expensive operation is blocking network I/O, not CPU computation. Threads can wait concurrently because I/O releases the GIL. A fixed pool makes the global bound easy to see. Asyncio is a good alternative with async HTTP and storage; processes add serialization and coordination overhead with little benefit here. Threads are a pragmatic choice, not universally superior.

### 2. Exactly where is concurrency limited?

`Pipeline.start()` creates exactly N enrichment threads. Each worker synchronously invokes at most one provider call at a time; all jobs feed the same durable queue. No job creates its own pool. The metric lock measures calls; it is not the limiter. `ProcessLock` prevents a second server from opening the same database with another N workers.

Follow-up: “Could I start two copies with different databases?” Yes. That is two separate services; no account-wide distributed limit exists. A shared external limiter is required for production.

### 3. Why is a fast POST important? What does it actually wait for?

It waits for input validation and one durable SQLite transaction, not enrichment. That makes acceptance meaningful after a restart. It is not constant-time: parsing/inserting 10,000 rows costs more than ten. Measure admission latency at the permitted maximum, and introduce bulk ingestion/backpressure at larger scales.

### 4. What is the difference between a queue and a thread pool?

The SQLite queue stores pending work durably. The fixed pool determines how many workers can execute it. An in-memory executor queue alone would lose pending tasks on a process crash.

### 5. Why are there four attempts rather than three?

The brief says “retry up to 3 more times”: one initial attempt plus three retries. Delays before attempts 2–4 are 0.2, 0.4 and 0.8 seconds. Total backoff is 1.4 seconds, excluding provider latency.

### 6. Do invalid JSON and wrong categories count as provider failures?

Yes. `validate_output` executes inside the attempt's try block. A transport error, malformed JSON or schema error increments `llm_errors_total` and retries. A final failure is stored on the product; other queue items continue.

### 7. What do you retry in a production provider?

Transient timeouts, 429s, selected 5xx responses and validation failures may be retryable. Invalid credentials should normally fail fast. This assessment retries all provider/validation exceptions four times for a simple uniform contract. Production should classify errors, honor Retry-After and add jitter to avoid synchronized retry bursts.

### 8. Why not keep retrying forever?

An unhealthy provider would waste quota, delay unrelated work and never finish jobs. A bounded attempt budget produces an explicit failure a human can inspect. Failed content is not cached, so resubmitting after recovery is allowed.

### 9. What happens during backoff?

The worker waits on a stoppable event without holding database, metrics or in-flight locks. It still occupies one pool slot. Therefore “N workers” is an upper bound, not a promise that N LLM calls are always running. A timer queue could improve utilization.

### 10. Explain the counters precisely.

Every attempt increments `llm_calls_total`, including retries. Every failed attempt increments `llm_errors_total`, including invalid output. An in-memory active count increments before an attempt and decrements in `finally`; the durable metric stores the largest observed active count. Historical metrics persist after restart.

## Caching and race conditions

### 11. How do you decide whether two products are duplicates?

Normalize exactly `raw_title + " " + raw_description` by lowercasing and collapsing whitespace. SKU does not participate. “AMUL  BUTTER” and “amul butter” match, but “500g” and “500 g” do not. The normalized text itself is the SQLite key, avoiding hash collisions. A production hash could reduce index size while retaining text for collision checks.

### 12. Why isn't a dictionary cache enough?

It disappears after restart, and a plain check-then-call sequence races: two threads can both see a miss before either writes. The solution combines durable cached results with a locked in-flight registry.

### 13. Explain the Future code line by line.

Under `flight_lock`, check SQLite's cache. If absent, look for a Future for that content. If one exists, this worker becomes a waiter. If absent, create it and become the owner. Release the lock. Waiters block on `future.result()`; the owner performs retries, validates, saves the successful result to SQLite and resolves the Future. Finally, remove the registry entry under the lock. No slow provider call occurs inside `flight_lock`.

### 14. Why write the cache before resolving the Future?

A new worker arriving after the owner finishes must find a durable result even if the in-flight entry has already been removed. It also reduces repeat work if the process crashes before product completion. Resolving first would create a durability gap for waiters.

### 15. What if the owner fails all attempts?

The Future receives the exception; existing waiters fail with the same outcome without extra parallel calls. Their cache-hit count is false. The registry entry is removed. A later queued item or new submission may attempt again because failure is not a successful cached enrichment.

### 16. Can waiting duplicates deadlock the pool?

Not here: a waiter only waits on a Future created by an already-running owner, and that owner performs its own synchronous call rather than scheduling a child task into the occupied pool. Deadlock would become possible if all workers waited for new tasks submitted to the same pool.

### 17. Why not hold the global lock while calling the LLM?

That would serialize independent contents and erase the benefit of N workers. The lock protects state transitions only. Network calls and Future waits happen outside it.

### 18. What if a SKU is uploaded twice with different content?

Each input row gets an increasing ID. `latest_skus` records the newest accepted ID. Completion updates the catalogue only if the completing row still owns that revision. The older job's counters still advance, so neither job hangs. Latest submission wins, not whichever call happens to finish last.

### 19. Does approving one product change all duplicates?

No. Approval changes the SKU's catalogue record, while the shared cache remains the original validated enrichment. Reviewer edits may be specific to one product. If a later upload replaces that SKU, the new result can replace its approval; this behavior is documented.

### 20. Why use a separate database for real-model demos?

The assignment says already-enriched content must not be called again. Therefore a provider switch intentionally does not invalidate the cache. With one database the real demo might only reuse mock results. Separate databases demonstrate both honestly. Production should deliberately version caches by prompt/model/taxonomy and define migration policy.

## Persistence, API and frontend

### 21. What if the server dies halfway through 10,000 items?

Committed `done` rows stay done. Startup resets only `running` rows to `pending`; untouched pending rows remain queued. Cached results can complete recovered items without another call. A single completion transaction writes the product, terminal item state and job counters, so a crash cannot commit only half of that logical operation.

### 22. Do you guarantee exactly-once LLM calls?

No. A response received immediately before a crash may be lost before its cache write. The next process may call again. This is at-least-once external execution with idempotent durable completion. Exactly-once external effects require provider idempotency or a stronger protocol. Retry budgets also restart for unfinished items.

### 23. Why SQLite, and why a lock?

SQLite is durable, transactional and dependency-free. One shared connection has `check_same_thread=False`, which permits access but does not make multi-statement operations logically atomic. An RLock and short transactions serialize these operations. RLock permits `approve()` to call `product()` while already holding the lock. WAL supports durable recovery, but the application lock still serializes its own reads and writes.

### 24. Is “done” the number of successful products?

No. Here it means terminal products, including failed ones. Successes equal `done - failed`. A job can be completed with failures; completed means nothing remains to process. This interpretation is stated in README.

### 25. How does search work, and what does SQL injection protection look like?

All query values are bound SQL parameters, not concatenated input. Only allowlisted PATCH column names enter SQL structure. Search matches literal substrings on both titles; `%` and `_` are escaped. It uses Unicode-aware lowercase through a registered SQLite function. This scan is suitable for the demo but slow for millions of rows.

### 26. Why debounce search but poll jobs once a second?

Debounce waits 300 ms after the latest keystroke, reducing requests while the user types. Polling checks ongoing server progress. A timeout scheduled after each completed poll avoids overlapping polls and stays at or below one per second. Sequence IDs prevent older search responses or switched-job polls overwriting newer UI state.

### 27. Why not parse CSV with split(',')?

CSV fields can contain commas, escaped quotes, CRLF and quoted newlines. The state machine tracks quoted fields and checks malformed rows. Tests include these cases. A mature parser would be preferable for broader encodings/dialects, but this keeps the plain frontend dependency-free.

### 28. How did you handle accessibility and untrusted text?

Native buttons, labels, selects, progress and a modal dialog provide keyboard behavior; visible focus styles help navigation. At 360 px the layout stacks and the review dialog scrolls. Seller/model text is inserted with `textContent`, not HTML, and the server sends a restrictive CSP. Neither the prompt nor JSON mode is a complete prompt-injection defense; validation still runs.

### 29. Why does changing page size to 500 return 100?

The contract caps page size at 100. Non-numeric, zero or negative values return 400. Page defaults to 1 and page size to 20. Stable SKU sorting makes pagination predictable, although updates between requests can still shift offset pages.

## System-design discussion

### 30. How would you process one million listings/day with a limited RPM budget?

First estimate: one million/day is 11.57 listings/second. With 50% cache hits and batches of ten, baseline demand is about 34.7 requests/minute, plus retries. Enforce shared request/token buckets, buffer bursts durably, batch within token limits, and monitor queue age/cost. More machines increase processing capacity but do not increase an account's quota.

### 31. Concurrency versus rate limiting: what's the difference?

Concurrency limits simultaneous work; rate limiting limits starts over a time window. Five calls that each last 100 ms can generate roughly 3,000 requests/minute with no other overhead. A five-call concurrency limit alone cannot enforce a 60 RPM contract.

### 32. How would you scale deduplication across machines?

Use a shared result store and distributed leases or a unique database row for each content key. Lease ownership must expire after a crash and use fencing tokens so stale workers cannot overwrite newer owners. The current in-process dictionary and process lock cannot coordinate distributed workers.

### 33. What would make search slow with five million products?

Leading-wildcard title scans, exact counts, deep offsets and a serialized connection. Use token FTS or trigram indexes depending on required semantics, cursor pagination, concurrent read connections/PostgreSQL and carefully invalidated caches. Do not claim that adding an ordinary title index fixes `%term%`.

### 34. What if the LLM invents a brand but returns valid JSON?

Schema validation passes. Compare the brand with seller evidence and a verified alias dictionary, flag unsupported claims, preserve quantities and use a labeled evaluation set. Route uncertain/conflicting outputs for human review. “JSON mode” guarantees neither truth nor taxonomy accuracy.

### 35. What would you improve first with another day?

Measure workload bottlenecks, improve error classification and retry jitter, then add stronger semantic checks and end-to-end UI regression tests. For production priorities change: authentication, bounded admission, distributed rate limits, backups and operations. Pick priorities based on demonstrated needs rather than adding tools for appearance.

### 36. What did AI do, and what did you do?

Be honest: “I used Codex to help implement, test and document the project. I reviewed the architecture and can explain the code paths. Here is a change I can make myself and the test I would add.” Only say the review/understanding part after you have actually done it. This assignment explicitly values that understanding.

## Numbers you should be able to derive

- No failures, 200 distinct contents, latency 200 ms, N=5: ideal provider time is approximately `ceil(200/5) × 0.2 = 8 seconds`, plus storage/scheduling/UI overhead. This is an estimate, not an SLA.
- Fresh sample upload: 240 rows, 200 unique normalized contents, 40 cache hits, 200 calls if failure rate is zero.
- Repeat upload after all succeeded: 240 hits, zero additional calls.
- One distinct product with failure rate 1: four calls, four errors, one failed item, at least 1.4 seconds of backoff.
- Independent failure probability p: expected attempts with four-attempt cap = `1 + p + p² + p³`; exhaustion probability = `p⁴`. For p=0.1, expected attempts = 1.111 and exhaustion probability = 0.0001. Real failures may be correlated, so this model is only illustrative.

## No-AI practice exercises

Spend 10–20 minutes on each. Run tests before and after. Explain your change out loud.

1. **Add retry jitter.** Change only the delay calculation. Preserve four attempts and a base delay near 200 ms. Explain why synchronized clients otherwise retry together. Update timing assertions so they are not flaky.
2. **Add a status filter.** Accept `status=enriched|failed|approved` in GET /api/products, parameterize the query, expose a select in the UI, reset page to 1, and test invalid input plus pagination.
3. **Add a brand filter.** Decide exact versus case-insensitive semantics; document and test it. Explain whether an index helps.
4. **Add one category.** Identify every affected place: categories constant, prompt, mock rules, frontend options and tests. Explain why duplicated frontend/backend category lists are a maintenance risk.
5. **Change maximum tags from 5 to 3.** Update provider validation, PATCH validation, prompt, UI helper text and frontend validation. Test both two and four tags.
6. **Diagnose 0 LLM calls in real mode.** Inspect health, database path and cache hits before blaming the provider. Run a new content or separate database.
7. **Explain and test a race.** Make an older SKU result finish after a newer result; prove the catalogue still contains the newer input.
8. **Graceful shutdown.** Start a slow job, stop the process, restart the same database and confirm committed items do not run again. Explain the response-before-cache crash gap.

## 60-minute mock interview

- 0–5 min: give the project pitch and show the code map.
- 5–15 min: upload unseen data; inspect errors, metrics and one approval.
- 15–30 min: draw the owner/waiter race and explain retries/recovery without notes.
- 30–45 min: implement one practice exercise without AI.
- 45–55 min: discuss one-million/day throughput and five-million-product search.
- 55–60 min: state honest limitations and what you would improve next.

Self-score each answer: 0 = cannot explain, 1 = memorized summary, 2 = explains with code, 3 = explains code plus a failure case and tradeoff. Prioritize questions 2, 6, 13, 18, 21, 22 and 31 until each scores at least 2.
