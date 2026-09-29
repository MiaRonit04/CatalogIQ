"""Submit any CSV via HTTP; display final progress and metrics. No dependencies."""
import argparse
import csv
import json
import time
import urllib.request


def request(base, path, body=None):
    data=json.dumps(body).encode() if body is not None else None
    req=urllib.request.Request(base+path,data=data,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv',nargs='?',default='data/sample_products.csv')
    parser.add_argument('--url',default='http://localhost:8000')
    args=parser.parse_args()
    with open(args.csv,newline='',encoding='utf-8-sig') as f:products=list(csv.DictReader(f))
    start=time.monotonic();job=request(args.url,'/api/jobs',{'products':products})
    print(f'Accepted {job["id"]} in {time.monotonic()-start:.3f}s',flush=True)
    while job['status']!='completed':
        time.sleep(1);job=request(args.url,'/api/jobs/'+job['id'])
        print(f'{job["status"]}: {job["done"]}/{job["total"]}, failed={job["failed"]}, cache_hits={job["cache_hits"]}',flush=True)
    print(json.dumps(request(args.url,'/api/metrics'),indent=2))

if __name__=='__main__':main()
