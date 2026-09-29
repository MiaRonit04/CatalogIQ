"""Run with python3 -m catalogiq.server. Standard-library HTTP server for local demo."""
import json
import logging
import os
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from .pipeline import Pipeline
from .providers import CATEGORIES, Config, make_provider
from .store import Store

ROOT = Path(__file__).resolve().parent

class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def validate_products(body):
    if not isinstance(body, dict) or not isinstance(body.get('products'), list) or not body['products']:
        raise APIError(400, 'products must be a non-empty list')
    if len(body['products']) > 10000:
        raise APIError(400, 'Maximum 10000 products per job')
    result = []
    for i, p in enumerate(body['products']):
        if not isinstance(p, dict):
            raise APIError(400, f'Product {i+1} must be an object')
        for field in ('sku', 'raw_title'):
            if not isinstance(p.get(field), str) or not p[field].strip():
                raise APIError(400, f'Product {i+1}: {field} is required and must be a non-empty string')
        description = p.get('raw_description', '')
        if not isinstance(description, str):
            raise APIError(400, f'Product {i+1}: raw_description must be a string')
        if len(p['sku']) > 200 or len(p['raw_title']) > 2000 or len(description) > 10000:
            raise APIError(400, f'Product {i+1} exceeds field length limits')
        result.append(dict(sku=p['sku'], raw_title=p['raw_title'], raw_description=description))
    return result


def validate_patch(body):
    if not isinstance(body, dict) or not body or set(body) - {'clean_title', 'category', 'tags'}:
        raise APIError(400, 'Supply any of clean_title, category or tags')
    if 'clean_title' in body:
        if not isinstance(body['clean_title'], str) or not body['clean_title'].strip():
            raise APIError(400, 'clean_title must be a non-empty string')
        body['clean_title'] = body['clean_title'].strip()
    if 'category' in body and body['category'] not in CATEGORIES:
        raise APIError(400, 'Invalid category')
    if 'tags' in body and (not isinstance(body['tags'], list) or len(body['tags']) > 5 or any(
            not isinstance(t, str) or not t.strip() or t != t.lower() for t in body['tags'])):
        raise APIError(400, 'tags must contain at most 5 non-empty lowercase strings')
    return body


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, config, store, pipeline):
        self.config, self.store, self.pipeline = config, store, pipeline
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, fmt, *args):
        logging.info('%s %s', self.address_string(), fmt % args)

    def send_json(self, status, value):
        self.send_bytes(status, json.dumps(value, ensure_ascii=False).encode(), 'application/json; charset=utf-8')

    def send_bytes(self, status, body, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def send_error(self, code, message=None, explain=None):
        self.send_json(code, {'error': message or self.responses.get(code, ('Request failed',))[0]})

    def body(self):
        if self.headers.get('Transfer-Encoding'):
            raise APIError(400, 'Chunked request bodies are not supported')
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise APIError(400, 'Invalid Content-Length')
        if length <= 0 or length > 20_000_000:
            raise APIError(400, 'JSON body must be between 1 byte and 20 MB')
        try:
            return json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError):
            raise APIError(400, 'Invalid JSON body')

    def handle_request(self):
        try:
            self.route()
        except APIError as exc:
            self.send_json(exc.status, {'error': exc.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except TimeoutError:
            self.send_json(408, {'error': 'Request timed out'})
        except Exception:
            logging.exception('Request failed')
            self.send_json(500, {'error': 'Internal server error'})

    do_GET = do_POST = do_PATCH = do_DELETE = do_PUT = do_HEAD = do_OPTIONS = handle_request

    def route(self):
        url = urlsplit(self.path)
        path, method = url.path, self.command
        store = self.server.store
        if method == 'GET':
            files = {'/': ('static/index.html', 'text/html; charset=utf-8'),
                     '/csv.js': ('static/csv.js', 'text/javascript; charset=utf-8'),
                     '/app.js': ('static/app.js', 'text/javascript; charset=utf-8'),
                     '/style.css': ('static/style.css', 'text/css; charset=utf-8')}
            if path in files:
                name, mime = files[path]
                return self.send_bytes(200, (ROOT / name).read_bytes(), mime)
            if path == '/sample.csv':
                return self.send_bytes(200, (ROOT.parent / 'data/sample_products.csv').read_bytes(), 'text/csv; charset=utf-8')
            if path == '/api/health':
                return self.send_json(200, dict(status='ok', llm_provider=self.server.config.provider,
                                              llm_concurrency=self.server.config.concurrency))
            if path == '/api/metrics':
                return self.send_json(200, store.metrics())
            if path.startswith('/api/jobs/'):
                job = store.job(unquote(path[len('/api/jobs/'):]))
                if job is None:
                    raise APIError(404, 'Unknown job')
                return self.send_json(200, job)
            if path == '/api/products':
                query = parse_qs(url.query, keep_blank_values=True)
                try:
                    page = int(query.get('page', ['1'])[0])
                    size = int(query.get('page_size', ['20'])[0])
                    if not 1 <= page <= 1_000_000_000 or size < 1:
                        raise ValueError()
                except ValueError:
                    raise APIError(400, 'page and page_size must be positive integers (page <= 1000000000)')
                return self.send_json(200, store.products(page, min(size, 100),
                    query.get('category', [''])[0], query.get('q', [''])[0]))
            if path.startswith('/api/products/'):
                product = store.product(unquote(path[len('/api/products/'):]))
                if product is None:
                    raise APIError(404, 'Unknown SKU')
                return self.send_json(200, product)
        if method == 'POST' and path == '/api/jobs':
            return self.send_json(202, self.server.pipeline.submit(validate_products(self.body())))
        if method == 'PATCH' and path.startswith('/api/products/'):
            sku = unquote(path[len('/api/products/'):])
            if store.product(sku) is None:
                raise APIError(404, 'Unknown SKU')
            product = store.approve(sku, validate_patch(self.body()))
            return self.send_json(200, product)
        raise APIError(404, 'Unknown endpoint')


class ProcessLock:
    """One process per database: otherwise each process would create N workers."""
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.file = open(path + '.lock', 'a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.file.write(b'0')
                self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError('Another server is already using this database')

    def close(self):
        self.file.close()


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    config = Config.from_env()
    lock = ProcessLock(config.database)
    store = Store(config.database)
    pipeline = Pipeline(store, make_provider(config), config.concurrency)
    server = Server(('127.0.0.1', int(os.getenv('PORT', '8000'))), config, store, pipeline)
    pipeline.start()
    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    logging.info('CatalogIQ http://localhost:%s | provider=%s | concurrency=%s',
                 server.server_port, config.provider, config.concurrency)
    try:
        server.serve_forever()
    finally:
        pipeline.stop()
        server.server_close()
        store.close()
        lock.close()

if __name__ == '__main__':
    main()
