// Optional frontend parser tests: node --test tests/test_csv.js
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {parseCSV}=require('../catalogiq/static/csv.js');
test('quoted commas, newlines, escaped quotes, BOM and CRLF',()=>{
  const rows=parseCSV('\uFEFFsku,raw_title,raw_description\r\nA,"Tea, green","A ""nice""\nleaf"\r\n');
  assert.deepEqual(rows,[{sku:'A',raw_title:'Tea, green',raw_description:'A "nice"\nleaf'}]);
});
test('optional description and blank lines',()=>assert.deepEqual(parseCSV('sku,raw_title\n\nA,Butter\n'),[{sku:'A',raw_title:'Butter'}]));
test('reject malformed and invalid input before submission',()=>{
  for(const csv of ['', 'sku,raw_title\n', 'sku,name\nA,B','sku,sku,raw_title\nA,A,B','sku,raw_title\nA,"bad','sku,raw_title\nA,B,C','sku,raw_title\nA,"B"junk','sku,raw_title\n,Butter'])assert.throws(()=>parseCSV(csv));
});
test('sample contains 240 rows and 40 duplicates',()=>{
  const rows=parseCSV(fs.readFileSync('data/sample_products.csv','utf8'));
  assert.equal(rows.length,240);assert.equal(new Set(rows.map(p=>(p.raw_title+' '+p.raw_description).toLowerCase().trim().replace(/\s+/g,' '))).size,200);
});
