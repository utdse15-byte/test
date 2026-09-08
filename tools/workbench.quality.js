// Quality preference is a separate, immutable evidence overlay. It never changes
// Request/v1, old catalogs, or a historical approval on disk.
const qualityPolicy=JSON.parse($('builtin-quality').textContent);
async function qualityPlan(request,cat,day=new Date().toISOString().slice(0,10)){
 const entries=new Map(qualityPolicy.entries.map(e=>[e.profile_id,e]));
 const eligible=[],excluded=[];
 for(const option of options(request,cat,day)){
  const entry=entries.get(option.profile_id),profile=cat.profiles.find(p=>p.id===option.profile_id);let reason=null;
  if(!entry)reason='not_in_quality_shortlist';
  else if(!entry.tasks.includes(request.task))reason='task_not_in_quality_shortlist';
  else if(await hash(profile)!==entry.profile_sha256)reason='profile_changed_requires_quality_recheck';
  else if(day<qualityPolicy.checked_on)reason='quality_evidence_date_in_future';
  if(reason)excluded.push({profile_id:option.profile_id,mode_id:option.mode_id,reason});
  else{
   const warnings=[...option.warnings,'quality_shortlist_is_not_per_shot_proof'];
   if(day>qualityPolicy.review_after)warnings.push('quality_evidence_review_overdue');
   if(entry.basis==='official_successor_family_evidence')warnings.push('leaderboard_score_not_verified_for_exact_successor');
   eligible.push({...option,warnings:[...new Set(warnings)].sort(),quality_basis:entry.basis,quality_note:entry.note});
  }
 }
 return {schema_id:'manju.quality-plan/v1',request_sha256:await hash(request),catalog_sha256:await hash(cat),shortlist_sha256:await hash(qualityPolicy),evaluated_on:day,selected_profile:null,automatic_fallback:false,cost_ranked:false,ranked_by_quality:false,options:eligible,excluded,limits:qualityPolicy.limits};
}
$('quality-only').addEventListener('change',()=>{invalidate();status($('quality-only').checked?'已回到质量优先名单；请重新检查与选择。':'已进入历史兼容视图，不表示这些型号仍属顶尖。不会自动选择。');});
$('export-quality').addEventListener('click',handled(async()=>{
 const request=getRequest(),before=state.revision,report=await qualityPlan(request,catalog);require(before===state.revision,'任务变化，请重新保存依据');
 download(new Blob([jsonBytes({schema_id:'manju.quality-evidence-export/v1',shortlist:qualityPolicy,report})],{type:'application/json'}),'MANJU_QUALITY_'+report.request_sha256.slice(0,12)+'.json');
 status('已触发质量名单与筛选依据下载；未选型号、未调用生成、未修改任务。');
}));
window.ManjuQuality={qualityPlan,policy:()=>JSON.parse(JSON.stringify(qualityPolicy))};
