// RFC-style CSV state machine: quoted commas, doubled quotes, CRLF, embedded newlines and BOM.
function parseCSV(input) {
  input=input.replace(/^\uFEFF/,'');
  const rows=[]; let row=[], field='', quoted=false, closed=false, atStart=true;
  const pushField=()=>{row.push(field);field='';closed=false;atStart=true;};
  const pushRow=()=>{pushField(); if(row.some(v=>v.trim())) rows.push(row); row=[];};
  for(let i=0;i<input.length;i++) {
    const c=input[i];
    if(quoted) { if(c==='"') { if(input[i+1]==='"') {field+='"';i++;} else {quoted=false;closed=true;} } else field+=c; continue; }
    if(c===',') {pushField();continue;}
    if(c==='\r'||c==='\n') {if(c==='\r'&&input[i+1]==='\n')i++;pushRow();continue;}
    if(closed) throw new Error('Unexpected character after a closing CSV quote.');
    if(c==='"') {if(!atStart)throw new Error('Unexpected quote inside a CSV field.');quoted=true;atStart=false;}
    else {field+=c;atStart=false;}
  }
  if(quoted)throw new Error('Unclosed quote in CSV.');
  if(field||row.length||closed)pushRow();
  if(!rows.length)throw new Error('CSV is empty.');
  const headers=rows.shift().map(h=>h.trim().toLowerCase());
  if(new Set(headers).size!==headers.length)throw new Error('CSV headers must be unique.');
  if(!headers.includes('sku')||!headers.includes('raw_title'))throw new Error('CSV needs sku and raw_title columns.');
  if(!rows.length)throw new Error('CSV has no product rows.');
  if(rows.length>10000)throw new Error('Use at most 10,000 products in one upload.');
  return rows.map((r,i)=>{
    if(r.length!==headers.length)throw new Error(`Row ${i+2} has ${r.length} columns; expected ${headers.length}.`);
    const p={}; headers.forEach((h,j)=>{if(['sku','raw_title','raw_description'].includes(h))p[h]=r[j];});
    if(!p.sku.trim()||!p.raw_title.trim())throw new Error(`Row ${i+2} needs a SKU and title.`);
    return p;
  });
}

if (typeof module !== 'undefined') module.exports = {parseCSV};
