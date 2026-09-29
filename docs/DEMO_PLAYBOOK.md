# Interview-day playbook

## Before the call

1. Read README and DESIGN. Rehearse the owner/waiter and restart explanations in INTERVIEW_PREP.
2. Run `python3 -m unittest discover -s tests -v` and optionally `node --test tests/test_csv.js`.
3. Keep one terminal for the server, one for commands, and your editor open to `pipeline.py`.
4. Have Ollama installed and your model already downloaded. Internet should not be needed for local inference.
5. Start mock mode on port 8000. Keep real mode ready in another terminal on port 8001, with a different database.

```bash
# Terminal 1: deterministic mock demonstration
MOCK_FAILURE_RATE=0 DATABASE_PATH=data/demo.sqlite3 python3 -m catalogiq.server

# Terminal 2: only if Ollama is not already running
ollama serve

# Terminal 3: real model (pull it once beforehand)
LLM_PROVIDER=ollama OLLAMA_MODEL=llama3.2:3b LLM_CONCURRENCY=2 PORT=8001 DATABASE_PATH=data/llama-demo.sqlite3 python3 -m catalogiq.server
```

Use the model actually installed on your laptop. To run the real provider at the assignment's port 8000, stop mock mode and omit `PORT=8001`.

## Five-minute demonstration

1. Open localhost:8000 and `/api/health`. Point out mock provider and concurrency 5.
2. Upload the supplied CSV (or the interviewer's CSV with `sku`, `raw_title`, optional `raw_description`). Watch the progress without reloading. “Done” includes failures.
3. On a fresh database with failure rate zero, the sample ends at **240 done, 0 failed, 40 hits**. Metrics show **200 calls, 0 errors, peak 5**. A repeat run shows 240 hits and no new calls. If your database was used before, explain why metrics differ.
4. Search for butter, select Groceries, move between pages, then review one listing. Edit title/tags and approve. Explain that this is a human change to one SKU, not a cache update.
5. Open localhost:8001 and submit the tiny `data/real_demo.csv`. Show genuine generated titles and explain that schema validity is not semantic correctness.

## Failure demonstration

Stop the mock server with Ctrl+C and start with a new path:

```bash
MOCK_FAILURE_RATE=1 MOCK_LATENCY_MS=10 DATABASE_PATH=data/failure-demo.sqlite3 python3 -m catalogiq.server
python3 scripts/demo.py data/real_demo.csv
```

Every unique item takes four attempts; duplicates already waiting share the error. Open a failed product to show its error. Setting failure rate 1 does not invalidate successful cache entries, which is why this uses a separate database.

## Recovery demonstration

Use a fresh database and slow mock calls:

```bash
MOCK_FAILURE_RATE=0 MOCK_LATENCY_MS=1000 DATABASE_PATH=data/recovery-demo.sqlite3 python3 -m catalogiq.server
```

Upload the sample, wait for partial progress, Ctrl+C, then restart the same command. The saved job resumes. A graceful stop is not the same as a forced crash: active calls may complete before the process exits. The recovery unit tests separately simulate interrupted running items and cache-before-completion gaps. Never claim this demo proves exactly-once external requests.

## Troubleshooting you should explain

- **Address already in use:** another service is on that port; stop the CatalogIQ process you started or choose another PORT. Do not terminate an unknown process.
- **Another server is using this database:** use a separate database for the second service, or stop the first. Deleting the lock file is not the fix.
- **Real model fails:** check `ollama list`, the model name, and whether `ollama serve` is running. Read the failed product error and server log. Download models before interview day.
- **No new calls after uploading:** normalized content is already cached. Show cache_hits; use a separate database if demonstrating real inference.
- **Job ID not found after switching databases:** the browser remembers recent IDs, but each database has its own jobs. Submit a new job in this database.
- **CSV rejected:** check required headers, missing SKUs/titles, balanced quotes, row column counts, and the 10,000-row/20 MB limits. The entire batch is rejected before insertion.
- **Unexpected category:** the mock uses simple keyword rules; real models can also make mistakes. Demonstrate review and discuss validation versus quality.

## Submission checklist

- Verify tests and both provider modes on your machine.
- Read the assumptions and limitations; be able to defend them.
- Check `git status` and `git log --oneline`.
- Create a repository in your own Git hosting account and push the local history. If private, give the evaluator access through the hosting service.
- Review the remote repository to confirm source, sample CSV, tests, README and DESIGN are present, but no database, model download or secrets are committed.
- Send the repository link and the model you actually demonstrated to the hiring contact provided separately. The assignment's `[hiring email]` is a placeholder.

Suggested email text to adapt and send yourself:

> Hi,
>
> Here is my CatalogIQ internship assignment: [repository link].
> The project includes the built-in mock provider and a real Ollama provider using [model actually used]. Setup, tests, design tradeoffs and AI-assistance disclosure are in the repository.
>
> Thank you,
> [Your name]
