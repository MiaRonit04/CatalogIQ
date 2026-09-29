"""Short, serialized SQLite transactions. No network calls run under this lock."""
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def content_key(title, description):
    # Store the exact normalized string: no hash collision or ambiguous field delimiter.
    return ' '.join((title + ' ' + description).lower().split())


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, status TEXT NOT NULL, total INTEGER NOT NULL,
          done INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
          cache_hits INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
          started_at TEXT, finished_at TEXT);
        CREATE TABLE IF NOT EXISTS job_items (
          id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
          sku TEXT NOT NULL, raw_title TEXT NOT NULL, raw_description TEXT NOT NULL,
          content_key TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending');
        CREATE INDEX IF NOT EXISTS pending_items ON job_items(state, id);
        CREATE TABLE IF NOT EXISTS products (
          sku TEXT PRIMARY KEY, revision INTEGER NOT NULL, raw_title TEXT NOT NULL,
          raw_description TEXT NOT NULL, clean_title TEXT, category TEXT, brand TEXT,
          tags TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL, error TEXT);
        CREATE INDEX IF NOT EXISTS product_category_sku ON products(category, sku);
        CREATE TABLE IF NOT EXISTS latest_skus (sku TEXT PRIMARY KEY, revision INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS cache (content_key TEXT PRIMARY KEY, result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS metrics (
          id INTEGER PRIMARY KEY CHECK(id=1), llm_calls_total INTEGER NOT NULL DEFAULT 0,
          llm_errors_total INTEGER NOT NULL DEFAULT 0,
          max_concurrent_llm_calls INTEGER NOT NULL DEFAULT 0);
        INSERT OR IGNORE INTO metrics(id) VALUES(1);
        ''')
        with self.transaction() as db:
            db.execute("UPDATE job_items SET state='pending' WHERE state='running'")

    @contextmanager
    def transaction(self):
        with self.lock:
            with self.db:
                yield self.db

    def submit(self, products):
        job_id = 'j_' + uuid.uuid4().hex
        with self.transaction() as db:
            db.execute('INSERT INTO jobs(id,status,total,created_at) VALUES(?,?,?,?)',
                       (job_id, 'queued', len(products), now()))
            for p in products:
                cursor = db.execute('''INSERT INTO job_items(job_id,sku,raw_title,raw_description,content_key)
                    VALUES(?,?,?,?,?)''', (job_id, p['sku'], p['raw_title'], p['raw_description'],
                    content_key(p['raw_title'], p['raw_description'])))
                db.execute('INSERT INTO latest_skus VALUES(?,?) ON CONFLICT(sku) DO UPDATE SET revision=excluded.revision',
                           (p['sku'], cursor.lastrowid))
        return self.job(job_id)

    def claim(self):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM job_items WHERE state='pending' ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE job_items SET state='running' WHERE id=?", (row['id'],))
            db.execute("UPDATE jobs SET status='running', started_at=COALESCE(started_at,?) WHERE id=?",
                       (now(), row['job_id']))
            return dict(row)

    def cache_get(self, key):
        with self.lock:
            row = self.db.execute('SELECT result FROM cache WHERE content_key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def cache_put(self, key, result):
        with self.transaction() as db:
            db.execute('INSERT OR IGNORE INTO cache VALUES(?,?)', (key, json.dumps(result)))

    def complete(self, item, result, error, cache_hit):
        with self.transaction() as db:
            # Completion and progress are atomic; stale runs cannot overwrite a newer SKU.
            if db.execute('SELECT state FROM job_items WHERE id=?', (item['id'],)).fetchone()[0] != 'running':
                return
            latest = db.execute('SELECT revision FROM latest_skus WHERE sku=?', (item['sku'],)).fetchone()[0]
            if latest == item['id']:
                result = result or {}
                db.execute('''INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(sku) DO UPDATE SET revision=excluded.revision,
                    raw_title=excluded.raw_title, raw_description=excluded.raw_description,
                    clean_title=excluded.clean_title, category=excluded.category, brand=excluded.brand,
                    tags=excluded.tags, status=excluded.status, error=excluded.error''',
                    (item['sku'], item['id'], item['raw_title'], item['raw_description'],
                     result.get('clean_title'), result.get('category'), result.get('brand'),
                     json.dumps(result.get('tags', [])), 'failed' if error else 'enriched', error))
            db.execute("UPDATE job_items SET state='done' WHERE id=?", (item['id'],))
            db.execute('UPDATE jobs SET done=done+1, failed=failed+?, cache_hits=cache_hits+? WHERE id=?',
                       (int(error is not None), int(cache_hit), item['job_id']))
            db.execute("UPDATE jobs SET status='completed', finished_at=? WHERE id=? AND done=total",
                       (now(), item['job_id']))

    def release(self, item):
        with self.transaction() as db:
            db.execute("UPDATE job_items SET state='pending' WHERE id=? AND state='running'", (item['id'],))

    def job(self, job_id):
        with self.lock:
            row = self.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            return dict(row) if row else None

    @staticmethod
    def product_row(row):
        if row is None:
            return None
        value = dict(row)
        value.pop('revision')
        value['tags'] = json.loads(value['tags'])
        return value

    def product(self, sku):
        with self.lock:
            return self.product_row(self.db.execute('SELECT * FROM products WHERE sku=?', (sku,)).fetchone())

    def products(self, page, page_size, category, q):
        clauses, args = [], []
        if category:
            clauses.append('category=?')
            args.append(category)
        if q:
            # Escape LIKE metacharacters: user search is literal substring search.
            term = '%' + q.lower().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            clauses.append("(lower(clean_title) LIKE ? ESCAPE '\\' OR lower(raw_title) LIKE ? ESCAPE '\\')")
            args.extend([term, term])
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.lock:
            total = self.db.execute('SELECT count(*) FROM products' + where, args).fetchone()[0]
            rows = self.db.execute('SELECT * FROM products' + where + ' ORDER BY sku LIMIT ? OFFSET ?',
                                   args + [page_size, (page-1)*page_size]).fetchall()
        return dict(items=[self.product_row(r) for r in rows], page=page, page_size=page_size, total=total)

    def approve(self, sku, updates):
        with self.transaction() as db:
            if not db.execute('SELECT 1 FROM products WHERE sku=?', (sku,)).fetchone():
                return None
            for key, value in updates.items():
                if key not in ('clean_title', 'category', 'tags'):
                    raise ValueError('Invalid update field')
                db.execute(f'UPDATE products SET {key}=? WHERE sku=?',
                           (json.dumps(value) if key == 'tags' else value, sku))
            db.execute("UPDATE products SET status='approved', error=NULL WHERE sku=?", (sku,))
            return self.product(sku)

    def attempt_started(self, active):
        with self.transaction() as db:
            db.execute('''UPDATE metrics SET llm_calls_total=llm_calls_total+1,
                max_concurrent_llm_calls=max(max_concurrent_llm_calls,?) WHERE id=1''', (active,))

    def attempt_failed(self):
        with self.transaction() as db:
            db.execute('UPDATE metrics SET llm_errors_total=llm_errors_total+1 WHERE id=1')

    def metrics(self):
        with self.lock:
            row = dict(self.db.execute('SELECT * FROM metrics WHERE id=1').fetchone())
            row.pop('id')
            return row

    def close(self):
        self.db.close()
