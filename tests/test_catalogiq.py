import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
from catalogiq.pipeline import Pipeline
from catalogiq.providers import Config, MockProvider, OllamaProvider, validate_output
from catalogiq.server import ProcessLock, Server
from catalogiq.store import Store, content_key


def products(n, prefix='SKU', same=False):
    return [dict(sku=f'{prefix}-{i:04}',raw_title='Amul butter 500G' if same else f'Amul butter {i}G',raw_description='') for i in range(n)]


def wait_job(store, job, timeout=15):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        result=store.job(job['id'])
        if result['status']=='completed':return result
        time.sleep(.01)
    raise AssertionError(f'Job did not complete: {store.job(job["id"])}')


class TrackingMock(MockProvider):
    def __init__(self, config):
        super().__init__(config);self.lock=threading.Lock();self.active=0;self.peak=0
    def enrich(self,*args):
        with self.lock:self.active+=1;self.peak=max(self.peak,self.active)
        try:return super().enrich(*args)
        finally:
            with self.lock:self.active-=1


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=str(Path(self.tmp.name)/'db.sqlite3');self.store=Store(self.path);self.pipeline=None
    def tearDown(self):
        if self.pipeline:self.pipeline.stop()
        self.store.close();self.tmp.cleanup()
    def start(self, concurrency=5, provider=None, latency=20, failure=0):
        provider=provider or MockProvider(Config(latency_ms=latency,failure_rate=failure))
        self.pipeline=Pipeline(self.store,provider,concurrency);self.pipeline.start();return provider

    def test_concurrency_bound_across_jobs_and_actual_parallelism(self):
        mock=TrackingMock(Config(latency_ms=30,failure_rate=0));self.start(3,mock)
        jobs=[self.pipeline.submit(products(15,prefix=str(i))) for i in range(3)]
        for j in jobs:wait_job(self.store,j)
        self.assertEqual(mock.peak,3)
        self.assertEqual(self.store.metrics()['max_concurrent_llm_calls'],mock.peak)
        self.assertEqual(self.store.metrics()['llm_calls_total'],15)

    def test_retry_exhaustion_is_four_attempts_and_does_not_stop_job(self):
        self.start(2,latency=0,failure=1)
        start=time.monotonic();job=wait_job(self.store,self.pipeline.submit(products(2)))
        self.assertGreaterEqual(time.monotonic()-start,1.38)
        self.assertEqual((job['done'],job['failed']),(2,2))
        self.assertEqual(self.store.metrics()['llm_calls_total'],8)
        self.assertEqual(self.store.metrics()['llm_errors_total'],8)
        self.assertIn('4 attempts',self.store.product('SKU-0000')['error'])

    def test_retry_recovers_after_transient_mock_errors(self):
        self.start(1,latency=0,failure=.5)
        with patch('catalogiq.providers.random.random',side_effect=[0,0,1]):
            job=wait_job(self.store,self.pipeline.submit(products(1)))
        self.assertEqual(job['failed'],0)
        self.assertEqual(self.store.metrics()['llm_calls_total'],3)
        self.assertEqual(self.store.metrics()['llm_errors_total'],2)

    def test_simultaneous_duplicates_single_flight(self):
        self.start(5,latency=80)
        p=products(30,same=True);p[1]['raw_title']='  AMUL  BUTTER   500g  '
        j1=self.pipeline.submit(p[:15]);j2=self.pipeline.submit(p[15:])
        a,b=wait_job(self.store,j1),wait_job(self.store,j2)
        self.assertEqual(a['cache_hits']+b['cache_hits'],29)
        self.assertEqual(self.store.metrics()['llm_calls_total'],1)
        self.assertEqual(self.store.products(1,100,'','')['total'],30)

    def test_concurrent_failure_shared_with_waiters(self):
        self.start(5,latency=30,failure=1)
        job=wait_job(self.store,self.pipeline.submit(products(5,same=True)))
        self.assertEqual(job['failed'],5)
        self.assertEqual(job['cache_hits'],0)
        self.assertEqual(self.store.metrics()['llm_calls_total'],4)

    def test_invalid_json_and_category_are_retried(self):
        mock=MockProvider(Config(latency_ms=0,failure_rate=0))
        good=mock.enrich('butter','');bad=json.loads(good);bad['category']='Invented'
        self.start(1,mock)
        with patch.object(mock,'enrich',side_effect=['not json',json.dumps(bad),good]):
            job=wait_job(self.store,self.pipeline.submit(products(1)))
        self.assertEqual(job['failed'],0);self.assertEqual(self.store.metrics()['llm_errors_total'],2)
        self.assertEqual(self.store.metrics()['llm_calls_total'],3)

    def test_persistent_cache_and_products_after_restart(self):
        self.start();wait_job(self.store,self.pipeline.submit(products(1)))
        self.pipeline.stop();self.store.close();self.store=Store(self.path);self.start()
        job=wait_job(self.store,self.pipeline.submit(products(1,prefix='NEW')))
        self.assertEqual(job['cache_hits'],1);self.assertEqual(self.store.metrics()['llm_calls_total'],1)
        self.assertIsNotNone(self.store.product('SKU-0000'))

    def test_recovery_reuses_cache_before_item_completion(self):
        j=self.store.submit(products(2));item=self.store.claim()
        result=validate_output(MockProvider(Config(latency_ms=0,failure_rate=0)).enrich(item['raw_title'],''))
        self.store.cache_put(item['content_key'],result)
        # Simulate process loss after the durable cache write, before completion.
        self.store.close();self.store=Store(self.path);self.start()
        job=wait_job(self.store,j)
        self.assertEqual((job['done'],job['cache_hits']),(2,1));self.assertEqual(self.store.metrics()['llm_calls_total'],1)

    def test_recovery_skips_already_completed_items(self):
        j=self.store.submit(products(3));item=self.store.claim()
        result=validate_output(MockProvider(Config(latency_ms=0,failure_rate=0)).enrich(item['raw_title'],''))
        self.store.complete(item,result,None,False);self.store.claim()
        self.store.close();self.store=Store(self.path);self.start()
        job=wait_job(self.store,j)
        self.assertEqual(job['done'],3);self.assertEqual(self.store.metrics()['llm_calls_total'],2)

    def test_latest_submission_wins_even_when_old_result_finishes_last(self):
        old=self.store.submit([dict(sku='same',raw_title='Old title',raw_description='')]);olditem=self.store.claim()
        new=self.store.submit([dict(sku='same',raw_title='New title',raw_description='')]);newitem=self.store.claim()
        mock=MockProvider(Config(latency_ms=0,failure_rate=0))
        for item in [newitem,olditem]:self.store.complete(item,validate_output(mock.enrich(item['raw_title'],'')),None,False)
        self.assertEqual(self.store.product('same')['raw_title'],'New title')
        self.assertEqual(self.store.job(old['id'])['done'],1);self.assertEqual(self.store.job(new['id'])['done'],1)

    def test_normalization_exact_contract(self):
        self.assertEqual(content_key(' A  b','C\n d '),'a b c d')
        self.assertEqual(content_key('a b','c'),content_key('a','b c'))
        self.assertNotEqual(content_key('500g',''),content_key('500 g',''))

    def test_process_lock_blocks_second_server(self):
        lock=ProcessLock(self.path)
        try:
            with self.assertRaises(RuntimeError):ProcessLock(self.path)
        finally:lock.close()


class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(str(Path(self.tmp.name)/'db.sqlite3'))
        self.config=Config(latency_ms=20,failure_rate=0,concurrency=2)
        self.pipeline=Pipeline(self.store,MockProvider(self.config),2);self.pipeline.start()
        self.server=Server(('127.0.0.1',0),self.config,self.store,self.pipeline)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):
        self.server.shutdown();self.thread.join();self.server.server_close();self.pipeline.stop();self.store.close();self.tmp.cleanup()
    def request(self,path,method='GET',body=None,raw=None):
        data=raw if raw is not None else json.dumps(body).encode() if body is not None else None
        request=urllib.request.Request(self.base+path,data=data,method=method,headers={'Content-Type':'application/json'})
        try:
            response=urllib.request.urlopen(request,timeout=5)
        except urllib.error.HTTPError as error:response=error
        with response:return response.status,json.load(response)
    def seed(self,n=3):
        status,j=self.request('/api/jobs','POST',{'products':products(n)})
        self.assertEqual(status,202);return wait_job(self.store,j)

    def test_health_metrics_and_fast_background_submission(self):
        self.assertEqual(self.request('/api/health'),(200,dict(status='ok',llm_provider='mock',llm_concurrency=2)))
        start=time.monotonic();status,j=self.request('/api/jobs','POST',{'products':products(10000)})
        self.assertEqual(status,202);self.assertLess(time.monotonic()-start,1)
        start=time.monotonic();self.assertEqual(self.request('/api/health')[0],200);self.assertLess(time.monotonic()-start,.5)
        self.assertIn(j['status'],('queued','running'));self.assertLess(j['done'],j['total'])
        self.assertEqual(self.request('/api/jobs/'+j['id'])[0],200)
        m=self.request('/api/metrics')[1]
        self.assertEqual(set(m),{'llm_calls_total','llm_errors_total','max_concurrent_llm_calls'})
        self.assertTrue(all(type(v) is int for v in m.values()))

    def test_validation_errors_use_error_envelope(self):
        cases=[('/api/jobs','POST',{'products':[]}),('/api/jobs','POST',{'products':[{'sku':'A'}]}),
            ('/api/jobs','POST',{'products':[{'sku':'','raw_title':'a'}]}),('/api/jobs','POST',{'products':[{'sku':'A','raw_title':'x','raw_description':None}]}),
            ('/api/products?page=no','GET',None),('/api/products?page_size=no','GET',None),('/api/products?page=0','GET',None),('/api/products?page_size=-1','GET',None)]
        for path,method,body in cases:
            with self.subTest(path=path,body=body):
                status,result=self.request(path,method,body);self.assertEqual(status,400);self.assertEqual(set(result),{'error'})
        self.assertEqual(self.request('/api/jobs','POST',raw=b'{broken')[0],400)
        for path in ['/api/jobs/missing','/api/products/missing','/missing']:
            status,result=self.request(path);self.assertEqual(status,404);self.assertEqual(set(result),{'error'})

    def test_pagination_search_filter_and_approval(self):
        self.seed(3)
        status,data=self.request('/api/products?page_size=2');self.assertEqual(status,200)
        self.assertEqual(data['total'],3);self.assertEqual(len(data['items']),2)
        self.assertEqual([x['sku'] for x in data['items']],['SKU-0000','SKU-0001'])
        self.assertEqual(len(self.request('/api/products?page_size=2&page=2')[1]['items']),1)
        self.assertEqual(self.request('/api/products?page_size=200')[1]['page_size'],100)
        self.assertEqual(self.request('/api/products?q=AMUL&category=Groceries')[1]['total'],3)
        self.assertEqual(self.request('/api/products?q=%25')[1]['total'],0)
        self.assertEqual(self.request('/api/products?category=Fashion')[1]['total'],0)
        status,p=self.request('/api/products/SKU-0000','PATCH',{'clean_title':'Reviewed product','category':'Other','tags':['reviewed']})
        self.assertEqual(status,200);self.assertEqual(p['status'],'approved');self.assertEqual(p['clean_title'],'Reviewed product')
        self.assertEqual(self.request('/api/products?q=REVIEWED')[1]['total'],1)
        self.assertEqual(self.request('/api/products/SKU-0000')[1]['tags'],['reviewed'])
        for body in [{'category':'Wrong'},{'clean_title':' '},{'tags':['UPPER']},{'tags':'bad'},{'brand':'no'},{'tags':['a']*6}]:
            self.assertEqual(self.request('/api/products/SKU-0000','PATCH',body)[0],400)
        self.assertEqual(self.request('/api/products/missing','PATCH',{'clean_title':'x'})[0],404)

    def test_unicode_case_insensitive_search(self):
        status,j=self.request('/api/jobs','POST',{'products':[{'sku':'UNICODE','raw_title':'CAFÉ crème'}]})
        wait_job(self.store,j)
        from urllib.parse import quote
        self.assertEqual(self.request('/api/products?q='+quote('café'))[1]['total'],1)

    def test_existing_sku_updates_and_description_optional(self):
        for title in ['first','second']:
            status,j=self.request('/api/jobs','POST',{'products':[{'sku':'A / 1','raw_title':title}]})
            wait_job(self.store,j)
        status,p=self.request('/api/products/A%20%2F%201');self.assertEqual(status,200)
        self.assertEqual(p['raw_title'],'second');self.assertEqual(p['raw_description'],'')
        self.assertEqual(self.request('/api/products')[1]['total'],1)


class ProviderTests(unittest.TestCase):
    def test_full_schema_rejects_wrong_types(self):
        valid=dict(clean_title='Title',category='Other',brand=None,tags=[])
        for changes in [dict(brand=3),dict(tags=['CAPS']),dict(tags=['']),dict(clean_title=''),dict(category=[]),dict(tags='tag')]:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):validate_output(json.dumps(valid|changes))
        for raw in ['[]','null','"hello"','{}']:
            with self.assertRaises(ValueError):validate_output(raw)

    def test_ollama_adapter_against_local_stub(self):
        # Verifies real HTTP request/response wiring, without claiming to test a real model.
        from http.server import BaseHTTPRequestHandler,HTTPServer
        seen={}
        class Stub(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.update(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                seen['path']=self.path
                payload=json.dumps({'response':json.dumps(dict(clean_title='Butter',category='Groceries',brand=None,tags=['butter']))}).encode()
                self.send_response(200);self.end_headers();self.wfile.write(payload)
            def log_message(self,*args):pass
        server=HTTPServer(('127.0.0.1',0),Stub);thread=threading.Thread(target=server.handle_request);thread.start()
        try:
            provider=OllamaProvider(Config(provider='ollama',ollama_url=f'http://127.0.0.1:{server.server_port}'))
            self.assertEqual(validate_output(provider.enrich('butter',''))['category'],'Groceries')
            self.assertEqual(seen['path'],'/api/generate');self.assertFalse(seen['stream']);self.assertEqual(seen['format'],'json')
            self.assertIn('untrusted',seen['prompt'])
        finally:thread.join();server.server_close()

if __name__=='__main__':unittest.main()
