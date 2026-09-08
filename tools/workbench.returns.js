// Technical observations are a snapshot, not creative approval or provider provenance.
function returnChecks(request,candidate) {
 request=normalizeRequest(request);candidate=candidateValue(candidate);
 const expected=request.duration_s===null?null:request.duration_s*1000,actual=candidate.duration_ms;
 const checks=[{id:'duration_ms',state:expected===null?'not_requested':actual===null?'unknown':Math.abs(actual-expected)<=100?'match':'mismatch',expected,actual}];
 const match=/^([1-9][0-9]{0,3}):([1-9][0-9]{0,3})$/.exec(request.aspect_ratio||''),w=candidate.width,h=candidate.height;
 let verdict;
 if(!request.aspect_ratio||request.aspect_ratio==='adaptive')verdict='not_requested';
 else if(!match||!w||!h)verdict='unknown';
 else{const a=Number(match[1]),b=Number(match[2]);verdict=Math.abs(w*b-h*a)*100<=h*a*2?'match':'mismatch';}
 checks.push({id:'dimension_ratio',state:verdict,expected:request.aspect_ratio,actual:w&&h?`${w}:${h}`:null});
 const pixels=/^(360|480|540|720|768|1080|1440|2160)[pP]$/.exec(request.resolution||''),edge=pixels?Number(pixels[1]):null,actualEdge=w&&h?Math.min(w,h):null;
 verdict=!request.resolution?'not_requested':edge===null||actualEdge===null?'unknown':actualEdge>=edge?'match':'mismatch';
 checks.push({id:'minimum_short_edge_px',state:verdict,expected:edge,actual:actualEdge});return checks;
}
async function returnReport(request,candidates) {
 request=normalizeRequest(request);candidates=candidates.map(candidateValue);
 require(candidates.length>=1&&candidates.length<=12&&new Set(candidates.map(c=>c.sha256)).size===candidates.length,'请核对 1 至 12 个不同内容的视频');
 const items=candidates.map(candidate=>{const checks=returnChecks(request,candidate),states=new Set(checks.map(c=>c.state));return {candidate,checks,status:states.has('mismatch')||states.has('unknown')?'needs_attention':states.size===1&&states.has('not_requested')?'not_requested':'metadata_matches_requested_checks',full_video_decode:'not_checked'};});
 const body={schema_id:'manju.return-preflight/v1',request,request_sha256:await hash(request),policy:{duration_tolerance_ms:100,aspect_tolerance_percent:2,resolution_policy:'known_p_labels_minimum_short_edge'},items,automatic_approval:false,automatic_selection:false,picture_lock_authorized:false,provider_origin_verified:false,creative_quality_evaluated:false,audio_evaluated:false,geometry_note:'Dimension checks use reported pixel dimensions; SAR, rotation and crop are not certified.'};
 return {...body,report_sha256:await hash(body)};
}
async function rasterReport(request,candidates,pixels){
 require(Array.isArray(pixels)&&pixels.length===2&&pixels.every(x=>Number.isSafeInteger(x)&&x>=1&&x<=32768),'目标宽高必须是 1 至 32768 的整数');
 const body=await returnReport(request,candidates);delete body.report_sha256;body.schema_id='manju.return-preflight/v2';body.policy.resolution_policy='explicit_exact_pixel_dimensions';body.policy.expected_pixels=pixels;body.policy.vendor_origin_inferred=false;
 for(const item of body.items){const c=item.candidate,actual=c.width&&c.height?[c.width,c.height]:null;item.checks[item.checks.length-1]={id:'exact_pixel_dimensions',expected:pixels,actual,state:actual===null?'unknown':canonical(actual)===canonical(pixels)?'match':'mismatch'};
  item.status=item.checks.some(c=>['mismatch','unknown'].includes(c.state))?'needs_attention':'metadata_matches_requested_checks';}
 return {...body,report_sha256:await hash(body)};
}
function expectedReturnPixels(){const w=$('return-width').value,h=$('return-height').value;require(Boolean(w)===Boolean(h),'像素宽高必须同时填写');if(!w)return null;return [integer(Number(w),1,32768,'像素宽'),integer(Number(h),1,32768,'像素高')];}
const RUNWAY_45_RASTERS={'16:9':[1280,720],'9:16':[720,1280],'1:1':[960,960],'4:3':[1104,832],'3:4':[832,1104],'21:9':[1584,672]};
$('return-runway-raster').addEventListener('click',handled(()=>{const ratio=getRequest().aspect_ratio,pixels=RUNWAY_45_RASTERS[ratio];require(pixels,'当前画幅没有核验过的 Gen-4.5 像素模板');$('return-width').value=pixels[0];$('return-height').value=pixels[1];$('return-raster-note').textContent='已按 Runway Gen-4.5 官方说明（核验 2026-09-08）填入 '+pixels.join(' × ')+'。部分画幅仅适用于图生视频。此操作不推断视频来源。';}));
$('return-clear-raster').addEventListener('click',()=>{$('return-width').value='';$('return-height').value='';$('return-raster-note').textContent='使用旧通用最低短边检查；它不是供应商的尺寸保证。';});
async function inspectBoundReturns() {
 require(!state.busy&&!reviewState.busy,'素材仍在处理中');
 const request=getRequest(),pixels=expectedReturnPixels(),requestIdentity=await hash(request),revision=reviewState.revision,cs=reviewState.document?reviewState.document.session.candidates:reviewState.pending;
 require(cs.length,'先添加实际候选视频');reviewState.busy=true;
 try{
  const candidates=[];
  for(const c of cs){const found=reviewState.files.get(c.sha256);require(found,'请重新绑定原视频：'+c.filename);status('正在核对返回规格：'+c.filename);
   const entry=await inspectFile(found.file);require(entry.sha256===c.sha256&&entry.file.size===c.bytes,'候选内容已经改变');candidates.push(await measuredCandidate(found.file,entry));}
  require(revision===reviewState.revision&&await hash(getRequest())===requestIdentity&&canonical(expectedReturnPixels())===canonical(pixels),'任务或候选在检查期间改变，旧结果没有导出');
  return pixels===null?await returnReport(request,candidates):await rasterReport(request,candidates,pixels);
 }finally{reviewState.busy=false;}
}
function showReturnReport(report) {
 const root=$('return-results');root.replaceChildren();
 root.append(element('p',`只读检查快照 · 镜头 ${report.request.shot_id} · 任务 ${report.request_sha256.slice(0,12)}`,'hint'));
 const labels={duration_ms:'时长（毫秒）',dimension_ratio:'像素画幅',minimum_short_edge_px:'最低短边（像素）',exact_pixel_dimensions:'精确像素宽高'},states={match:'符合本地规格检查',mismatch:'需要处理',unknown:'尚不能判断',not_requested:'未指定 / 不适用'};
 report.items.forEach((item,i)=>{const box=element('div',undefined,'option');box.append(element('h3','返回候选 '+(i+1)),element('code',item.candidate.sha256));
  item.checks.forEach(c=>box.append(element('p',`${labels[c.id]}：${states[c.state]}。目标 ${c.expected??'未指定'}，实测 ${c.actual??'未知'}`,c.state==='mismatch'?'reason':c.state==='unknown'?'notice':'hint')));
  root.append(box);});
 root.append(element('p','没有评价内容、声音或供应商来源，没有自动批准或选片。导出时会重新读取实际文件。','hint'));
}
$('inspect-returns').addEventListener('click',handled(async()=>{const r=await inspectBoundReturns();showReturnReport(r);status('返回规格检查完成。这是技术观察，不代表内容合格。');}));
$('export-returns').addEventListener('click',handled(async()=>{const r=await inspectBoundReturns();showReturnReport(r);download(new Blob([jsonBytes(r)],{type:'application/json'}),'MANJU_RETURN_'+r.report_sha256.slice(0,12)+'.json');status('已触发返回规格报告下载。请自行确认保存；原视频和人工决定未被改动。');}));
window.ManjuReturns={returnChecks,returnReport,rasterReport,inspectBoundReturns};
