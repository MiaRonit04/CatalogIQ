'use strict';
const CATEGORIES = ['Groceries','Beverages','Personal Care','Household','Electronics','Fashion','Home & Kitchen','Other'];
const $ = id => document.getElementById(id);
let page = 1, currentSku = null, catalogueRequest = 0, jobGeneration = 0, pollTimer;
let savedJobs = [];
try { savedJobs = JSON.parse(localStorage.getItem('catalogiq-jobs') || '[]'); if (!Array.isArray(savedJobs)) savedJobs=[]; } catch (_) {}
const notice = (message, error=false) => { $('notice').textContent=message; $('notice').className=error?'error':''; $('notice').hidden=false; };
async function api(path, options) {
  const response = await fetch(path, options);
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'Request failed');
  return value;
}
function option(select, text, value=text) { const el=document.createElement('option'); el.value=value; el.textContent=text; select.append(el); }
CATEGORIES.forEach(c => { option($('category'),c); option($('edit-category'),c); });

function rememberJob(job) {
  savedJobs=[job.id,...savedJobs.filter(id=>id!==job.id)].slice(0,20);
  try{localStorage.setItem('catalogiq-jobs',JSON.stringify(savedJobs));}catch(_){}
  renderJobChoices();
  $('job-select').value=job.id;
}
function renderJobChoices() {
  $('job-select').replaceChildren();
  if(!savedJobs.length)option($('job-select'),'No jobs yet','');
  savedJobs.forEach(id=>option($('job-select'),id,id));
}
$('upload-form').addEventListener('submit', async event=>{
  event.preventDefault(); const file=$('csv-file').files[0]; if(!file)return;
  $('upload-button').disabled=true;
  try {
    if(file.size>20000000)throw new Error('File must be 20 MB or smaller.');
    const products=parseCSV(await file.text());
    const job=await api('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({products})});
    rememberJob(job); notice(`Accepted ${job.total} listings. Processing in the background.`); watchJob(job.id);
  } catch(error){notice(error.message,true);} finally{$('upload-button').disabled=false;}
});
async function metrics() {
  const m=await api('/api/metrics');
  $('metric-calls').textContent=m.llm_calls_total; $('metric-errors').textContent=m.llm_errors_total; $('metric-peak').textContent=m.max_concurrent_llm_calls;
}
function watchJob(id) {
  clearTimeout(pollTimer); const generation=++jobGeneration;
  async function poll() {
    if(generation!==jobGeneration)return;
    let completed=false;
    try {
      const j=await api('/api/jobs/'+encodeURIComponent(id));
      if(generation!==jobGeneration)return;
      $('job-status').textContent=j.status;
      $('job-description').textContent=j.status==='completed'?'Job complete. Your catalogue is ready for review.':`Processing ${j.total} listings · ${j.id.slice(0,10)}`;
      $('job-progress').max=j.total; $('job-progress').value=j.done;
      $('job-done').textContent=`${j.done} / ${j.total}`; $('job-failed').textContent=j.failed; $('job-hits').textContent=j.cache_hits;
      await metrics(); completed=j.status==='completed';
      if(completed)await loadProducts();
    }catch(error){notice(error.message,true);}
    // Schedule after completion: never poll faster than once a second, never overlap.
    if(!completed&&generation===jobGeneration)pollTimer=setTimeout(poll,1000);
  }
  poll();
}
$('job-select').addEventListener('change',()=>{if($('job-select').value)watchJob($('job-select').value);});
function element(tag,text,className) {const el=document.createElement(tag);el.textContent=text;if(className)el.className=className;return el;}
async function loadProducts() {
  const request=++catalogueRequest;
  const params=new URLSearchParams({page,page_size:$('page-size').value,q:$('search').value,category:$('category').value});
  try {
    const data=await api('/api/products?'+params);
    if(request!==catalogueRequest)return;
    if(page>1&&!data.items.length){page=1;return loadProducts();}
    $('product-count').textContent=data.total;
    $('products').replaceChildren();
    if(!data.items.length){const empty=element('div','','empty');empty.append(element('span','◇'),element('h3','No products to show'),element('p','Upload listings or try a different search.'));$('products').append(empty);}
    data.items.forEach(p=>{
      const row=element('article','','product-row');const main=element('div','','product-main');
      const title=element('div','');title.append(element('h3',p.clean_title||p.raw_title),element('p',`${p.sku} · ${p.brand||'Unknown brand'}`));
      main.append(element('span','◇','product-glyph'),title);
      const review=element('button','Review →','secondary review-button');review.setAttribute('aria-label',`Review ${p.sku}`);review.addEventListener('click',()=>openReview(p.sku));
      row.append(main,element('span',p.category||'Uncategorized','product-category'),element('span',p.status,`badge ${p.status}`),review);$('products').append(row);
    });
    const pages=Math.max(1,Math.ceil(data.total/data.page_size));
    $('page-info').textContent=`Page ${page} of ${pages} · ${data.total} products`;
    $('previous').disabled=page===1;$('next').disabled=page>=pages;
  }catch(error){if(request===catalogueRequest)notice(error.message,true);}
}
let debounce;
$('search').addEventListener('input',()=>{clearTimeout(debounce);catalogueRequest++;debounce=setTimeout(()=>{page=1;loadProducts();},300);});
['category','page-size'].forEach(id=>$(id).addEventListener('change',()=>{page=1;loadProducts();}));
$('previous').addEventListener('click',()=>{page--;loadProducts();});$('next').addEventListener('click',()=>{page++;loadProducts();});
$('refresh').addEventListener('click',()=>{loadProducts();metrics().catch(e=>notice(e.message,true));});
async function openReview(sku) {
  try {
    const p=await api('/api/products/'+encodeURIComponent(sku));currentSku=sku;
    $('raw-sku').textContent=p.sku;$('raw-title').textContent=p.raw_title;$('raw-description').textContent=p.raw_description||'No description';$('raw-brand').textContent=p.brand||'Unknown';$('raw-status').textContent=p.status;
    $('edit-title').value=p.clean_title||p.raw_title;$('edit-category').value=p.category||'Other';$('edit-tags').value=p.tags.join(', ');
    $('review-error').textContent=p.error||'';$('review-error').hidden=!p.error;
    $('review-dialog').showModal();$('edit-title').focus();
  }catch(error){notice(error.message,true);}
}
$('close-dialog').addEventListener('click',()=>$('review-dialog').close());
$('review-form').addEventListener('submit',async event=>{
  event.preventDefault();$('approve-button').disabled=true;
  try {
    const tags=$('edit-tags').value.split(',').map(t=>t.trim().toLowerCase()).filter(Boolean);
    if(tags.length>5)throw new Error('Use at most 5 tags.');
    await api('/api/products/'+encodeURIComponent(currentSku),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({clean_title:$('edit-title').value,category:$('edit-category').value,tags})});
    $('review-dialog').close();notice(`${currentSku} approved.`);await loadProducts();
  }catch(error){$('review-error').hidden=false;$('review-error').textContent=error.message;}finally{$('approve-button').disabled=false;}
});
async function init() {
  renderJobChoices();
  try {const h=await api('/api/health');$('connection').textContent='Service online';$('connection').classList.add('online');$('provider-info').textContent=`${h.llm_provider.toUpperCase()} PROVIDER · ${h.llm_concurrency} WORKERS`;await metrics();}catch(error){$('connection').textContent='Service unavailable';notice(error.message,true);}
  await loadProducts();if(savedJobs.length)watchJob(savedJobs[0]);
}
init();
