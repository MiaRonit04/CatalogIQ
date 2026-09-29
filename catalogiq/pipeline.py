"""Fixed worker pool + per-content futures = global bound and single-flight caching."""
import logging
import threading
from concurrent.futures import Future
from .providers import validate_output


class Pipeline:
    def __init__(self, store, provider, concurrency):
        self.store, self.provider = store, provider
        self.concurrency = concurrency
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.flight_lock = threading.Lock()
        self.flights = {}
        self.metric_lock = threading.Lock()
        self.active = 0
        self.threads = []

    def start(self):
        for i in range(self.concurrency):
            thread = threading.Thread(target=self._worker, name=f'enricher-{i}', daemon=True)
            self.threads.append(thread)
            thread.start()

    def submit(self, products):
        job = self.store.submit(products)
        self.wake.set()
        return job

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        for thread in self.threads:
            thread.join()

    def _worker(self):
        while not self.stop_event.is_set():
            item = self.store.claim()
            if item is None:
                self.wake.wait(.1)
                self.wake.clear()
                continue
            try:
                result, hit = self._enrich(item)
                self.store.complete(item, result, None, hit)
            except InterruptedError:
                self.store.release(item)
            except Exception as exc:
                self.store.complete(item, None, str(exc)[:1000], False)
                logging.info('Item %s failed: %s', item['id'], exc)

    def _enrich(self, item):
        key = item['content_key']
        with self.flight_lock:
            cached = self.store.cache_get(key)
            if cached is not None:
                return cached, True
            future = self.flights.get(key)
            owner = future is None
            if owner:
                future = Future()
                self.flights[key] = future
        if not owner:
            # A duplicate shares success OR failure; it never makes a parallel call.
            return future.result(), True
        try:
            result = self._attempts(item)
            self.store.cache_put(key, result)
            future.set_result(result)
            return result, False
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self.flight_lock:
                del self.flights[key]

    def _attempts(self, item):
        for attempt in range(4):
            if self.stop_event.is_set():
                raise InterruptedError('Server stopping; item will resume')
            with self.metric_lock:
                self.active += 1
                self.store.attempt_started(self.active)
            try:
                raw = self.provider.enrich(item['raw_title'], item['raw_description'])
                return validate_output(raw)
            except Exception as exc:
                self.store.attempt_failed()
                if attempt == 3:
                    raise RuntimeError(f'Failed after 4 attempts: {type(exc).__name__}: {exc}') from exc
            finally:
                with self.metric_lock:
                    self.active -= 1
            # No database/flight/metric lock is held during backoff.
            if self.stop_event.wait(.2 * 2 ** attempt):
                raise InterruptedError('Server stopping; item will resume')
