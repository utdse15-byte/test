// Capability imports are staged, inspected, then explicitly applied.
// Only dates/declarations are checked here; no source URL is fetched.
const catalogRevision={pending:null,previous:null,report:null,baseline:null,form:null,serial:0};
function changedFields(a,b,prefix){const out=[];for(const k of [...new Set([...Object.keys(a),...Object.keys(b)])].sort()){const x=a[k]??null,y=b[k]??null;if(canonical(x)!==canonical(y))out.push({path:prefix+k,before:x,after:y});}return out;}
async function catalogReview(before,after,request=null,today=new Date().toISOString().slice(0,10)){
 const a=normalizeCatalog(structuredClone(before)),b=normalizeCatalog(structuredClone(after));require(validDate(today),'比较日期无效');
 const old=new Map(a.profiles.map(p=>[p.id,p])),next=new Map(b.profiles.map(p=>[p.id,p])),changes=[];
 for(const id of [...new Set([...old.keys(),...next.keys()])].sort()){
  const x=old.get(id)??null,y=next.get(id)??null,prefix=`profiles[${id}]`;
  if(!x||!y){changes.push({path:prefix,before:x,after:y});continue;}
  changes.push(...changedFields(Object.fromEntries(Object.entries(x).filter(([k])=>k!=='modes')),Object.fromEntries(Object.entries(y).filter(([k])=>k!=='modes')),prefix+'.'));
  const xm=new Map(x.modes.map(m=>[m.id,m])),ym=new Map(y.modes.map(m=>[m.id,m]));
  for(const mid of [...new Set([...xm.keys(),...ym.keys()])].sort()){
   const v=xm.get(mid)??null,w=ym.get(mid)??null,k=prefix+`.modes[${mid}]`;
   if(!v||!w)changes.push({path:k,before:v,after:w});else changes.push(...changedFields(v,w,k+'.'));
  }
 }
 let impact=null,r=null;
 if(request!==null){r=normalizeRequest(request);const rows=[a,b].map(c=>new Map(options(r,c,today).map(o=>[`${o.profile_id}\0${o.mode_id}`,o])));impact=[];
  for(const key of [...new Set([...rows[0].keys(),...rows[1].keys()])].sort()){const x=rows[0].get(key)??null,y=rows[1].get(key)??null;if(canonical(x)!==canonical(y)){const [pid,mid]=key.split('\0');impact.push({profile_id:pid,mode_id:mid,before:x,after:y});}}
 }
 const freshness=[...b.profiles].sort((x,y)=>x.id<y.id?-1:x.id>y.id?1:0).map(p=>({profile_id:p.id,checked_on:p.checked_on,review_after:p.review_after,state:p.checked_on>today?'future_dated':today>p.review_after?'review_overdue':'within_review_window'}));
 return {schema_id:'manju.catalog-review/v1',evaluated_on:today,before_revision:a.revision,after_revision:b.revision,before_sha256:await hash(a),after_sha256:await hash(b),request_sha256:r?await hash(r):null,field_changes:changes,task_impact:impact,evidence_freshness:freshness,remote_sources_verified:false,automatic_apply:false,automatic_approval:false,automatic_model_selection:false};
}
async function previewCatalog(value){
 const token=++catalogRevision.serial,next=normalizeCatalog(structuredClone(value)),base=canonical(catalog),form=canonical(workspaceView());
 let request=null;try{request=getRequest();}catch(_){/* Incomplete drafts can still inspect a catalog, but no task impact is asserted. */}
 const report=await catalogReview(catalog,next,request);
 if(token!==catalogRevision.serial||base!==canonical(catalog)||form!==canonical(workspaceView())){status('能力档比较期间工作现场改变，请重新预览。',true);return;}
 catalogRevision.pending=next;catalogRevision.report=report;catalogRevision.baseline=base;catalogRevision.form=form;
 $('catalog-confirmed').checked=false;$('apply-catalog').disabled=false;$('catalog-preview').hidden=false;
 $('catalog-summary').textContent=`${report.before_revision} → ${report.after_revision}；${report.field_changes.length} 处字段变化；${report.task_impact===null?'当前草稿未完整，未评估任务影响':report.task_impact.length+' 个模式的任务结果变化'}。日期窗口不代表网页已经重新核验。`;
 $('catalog-diff').textContent=JSON.stringify(report,null,2);$('catalog-preview').scrollIntoView({block:'nearest'});
 status('已预览，尚未替换能力档。请检查具体变化，再勾选并应用。');
}
async function applyCatalog(){
 require(catalogRevision.pending&&catalogRevision.report,'请先预览新能力档');
 require($('catalog-confirmed').checked,'请明确确认已查看能力变化');
 require(catalogRevision.baseline===canonical(catalog)&&catalogRevision.form===canonical(workspaceView()),'工作现场已改变，请重新预览能力档；尚未替换。');
 require(catalogRevision.report.evaluated_on===new Date().toISOString().slice(0,10),'比较日期已改变，请重新预览');
 catalogRevision.previous=structuredClone(catalog);catalog=structuredClone(catalogRevision.pending);catalogRevision.pending=null;catalogRevision.serial++;
 invalidate();$('catalog-confirmed').checked=false;$('apply-catalog').disabled=true;$('catalog-revert').disabled=false;
 status('已替换能力档，当前型号选择与确认已清除。任务文字、素材和审片历史未改；可预览恢复上一能力档。');
}
$('apply-catalog').addEventListener('click',handled(applyCatalog));
$('catalog-refresh').addEventListener('click',handled(async()=>{require(catalogRevision.pending,'没有待比较能力档');await previewCatalog(catalogRevision.pending);}));
$('catalog-revert').addEventListener('click',handled(async()=>{require(catalogRevision.previous,'没有上一能力档');await previewCatalog(catalogRevision.previous);}));
$('catalog-save-review').addEventListener('click',handled(async()=>{require(catalogRevision.report,'没有比较报告');download(new Blob([jsonBytes(catalogRevision.report)],{type:'application/json'}),'MANJU_CATALOG_REVIEW.json');}));
window.ManjuCatalogReview={review:catalogReview,preview:previewCatalog,state:catalogRevision,strictJSON};
