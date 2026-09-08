// Scoped retakes are independent documents, never edits to Request/v1 or approval history.
const REPAIR_KEYS=['schema_id','request','request_sha256','source','start_ms','end_ms','context_before_ms','context_after_ms','preserve','change','audio_policy','requested_by','scope_confirmed','execution_authorized','automatic_selection','outside_interval_unchanged_verified','plan_sha256'];
const REPAIR_FORM=['repair-start','repair-end','repair-before','repair-after','repair-preserve','repair-change','repair-audio','repair-human'];
const repairState={entry:null,source:null,request:null,busy:false,revision:0,dirty:false,url:null,previewEnd:null};
function repairNotice(text,error=false){$('repair-status').textContent=text;$('repair-status').classList.toggle('reason',error);}
function repairChanged(){repairState.revision++;repairState.dirty=true;$('repair-confirmed').checked=false;}
function repairWindow(p){const start=Math.max(0,p.start_ms-p.context_before_ms),end=Math.min(p.source.duration_ms,p.end_ms+p.context_after_ms);return {start_ms:start,end_ms:end,repair_start_relative_ms:p.start_ms-start,repair_end_relative_ms:p.end_ms-start,interval_convention:'half_open_presentation_milliseconds'};}
function repairSourceName(p){return 'source/'+p.source.sha256+extension(p.source.filename);}
async function normalizeRepair(value){
 exactKeys(value,REPAIR_KEYS,'返工单');require(value.schema_id==='manju.repair-plan/v1','不支持的返工版本');
 const request=normalizeRequest(value.request),source=candidateValue(value.source);
 require(canonical(request)===canonical(value.request)&&canonical(source)===canonical(value.source),'返工上下文或原片元数据不是完整规范格式');
 require(source.media_check!=='hash_only'&&source.duration_ms!==null&&source.bytes<=MAX_FILE,'返工需要已测量的实际原片，最多 128 MiB');
 integer(value.start_ms,0,86400000,'开始毫秒');integer(value.end_ms,1,86400000,'结束毫秒');
 integer(value.context_before_ms,0,10000,'前方参考');integer(value.context_after_ms,0,10000,'后方参考');
 require(value.start_ms<value.end_ms&&value.end_ms<=source.duration_ms,'返工范围必须满足 0 ≤ 开始 < 结束 ≤ 原片时长');
 for(const key of ['preserve','change']){require(Array.isArray(value[key])&&value[key].length>=1&&value[key].length<=30,'保留与修改各需 1 至 30 条');for(const x of value[key])require(typeof x==='string'&&x===x.trim()&&x.length>=1&&x.length<=2000,'每条要求必须是 1 至 2000 字的去首尾空白文本');}
 const kept=new Set(value.preserve.map(casefold));require(!value.change.some(s=>kept.has(casefold(s))),'同一要求不能同时保留和修改');
 require(['preserve_original','needs_manual_review'].includes(value.audio_policy),'声音策略无效');
 require(string(value.requested_by,120,'请求人',1)===value.requested_by,'请求人格式不规范');
 require(value.scope_confirmed===true&&value.execution_authorized===false&&value.automatic_selection===false&&value.outside_interval_unchanged_verified===false,'返工边界或确认无效');
 require(await hash(request)===value.request_sha256,'原任务上下文已经改变');
 const body={...value};delete body.plan_sha256;require(await hash(body)===value.plan_sha256,'返工单内容校验失败');return clone(value);
}
function repairBrief(p){const w=repairWindow(p);return ['# 局部返工交接 / Scoped revision','',
 `镜头：${p.request.shot_id}`,`原任务 SHA-256：${p.request_sha256}`,`原片：${repairSourceName(p)}`,`原片 SHA-256：${p.source.sha256}`,`请求人：${p.requested_by}`,'',
 `返工范围（毫秒）：[${p.start_ms}, ${p.end_ms})`,`供参考的上下文（毫秒）：[${w.start_ms}, ${w.end_ms})`,
 '计时从所附原片开始，使用半开区间。这不是帧精确剪辑或供应商参数。','','## 保留',...p.preserve.map(s=>'- '+s),'','## 修改',...p.change.map(s=>'- '+s),'',`声音策略：${p.audio_policy}`,'',
 '本包保留完整原片，没有剪切、上传、生成、自动拼接或批准返回视频。',
 '原任务仅作上下文，引用的其他素材未随此返工包携带。',
 '保留项是创作要求，不是模型一定保持不变的保证。',
 '外部返回后必须核对接缝、范围外画面和声音，并重新人工审片。',
 'SHA-256 只证明内容一致，不证明发布者身份或版权许可。',''].join('\n');}
function repairView(){return {form:Object.fromEntries(REPAIR_FORM.map(id=>[id,$(id).value])),request:repairState.request||getRequest(),source:repairState.source,confirmed:$('repair-confirmed').checked};}
function repairMs(id){const value=$(id).value;require(/^\d+(?:\.\d{1,3})?$/.test(value),'时间请输入非负秒数，最多三位小数');return Math.round(Number(value)*1000);}
function repairLines(id){return $(id).value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);}
async function repairPlan(){
 require(repairState.source&&repairState.entry,'先选择实际原视频');require($('repair-confirmed').checked,'请先明确确认返工范围');
 const request=repairState.request||getRequest();const body={schema_id:'manju.repair-plan/v1',request,request_sha256:await hash(request),source:repairState.source,start_ms:repairMs('repair-start'),end_ms:repairMs('repair-end'),context_before_ms:repairMs('repair-before'),context_after_ms:repairMs('repair-after'),preserve:repairLines('repair-preserve'),change:repairLines('repair-change'),audio_policy:$('repair-audio').value,requested_by:$('repair-human').value.trim(),scope_confirmed:true,execution_authorized:false,automatic_selection:false,outside_interval_unchanged_verified:false};body.plan_sha256=await hash(body);return normalizeRepair(body);
}
function showRepairSource(){const video=$('repair-video');video.pause();video.removeAttribute('src');if(repairState.url)URL.revokeObjectURL(repairState.url);repairState.url=null;repairState.previewEnd=null;video.hidden=!repairState.entry;if(repairState.entry){repairState.url=URL.createObjectURL(repairState.entry.file);video.src=repairState.url;video.load();}}
async function repairBundle(){
 const before=canonical(repairView()),rev=repairState.revision,plan=await repairPlan(),entry=await inspectFile(repairState.entry.file);
 require(entry.sha256===plan.source.sha256&&entry.file.size===plan.source.bytes,'实际原片与返工单不符');
 const body=jsonBytes(plan),brief=encoder.encode(repairBrief(plan)),entries=[{name:'PLAN.json',data:body,crc:crc32(body)},{name:'BRIEF.md',data:brief,crc:crc32(brief)},{name:repairSourceName(plan),data:entry.file,crc:entry.crc}];
 const files={'PLAN.json':await hash(body),'BRIEF.md':await hash(brief),[repairSourceName(plan)]:entry.sha256};
 const manifest=jsonBytes({schema_id:'manju.repair-manifest/v1',files});entries.push({name:'MANIFEST.json',data:manifest,crc:crc32(manifest)});
 require(rev===repairState.revision&&before===canonical(repairView()),'返工或上方任务在导出期间变化，请重新核对');
 return {plan,blob:zipStore(entries)};
}
async function readRepair(blob){
 const entries=await storedZipMembers(blob,'repair');require(entries.size===4&&entries.has('PLAN.json')&&entries.has('BRIEF.md')&&entries.has('MANIFEST.json'),'返工包必须包含完整四个条目');
 const parse=async name=>{const entry=entries.get(name);require(entry.size<=2*1024*1024,'返工元数据超过 2 MiB');const data=new Uint8Array(await entry.data.arrayBuffer());require(crc32(data)===entry.crc,'返工元数据 CRC 不符');return strictJSON(new TextDecoder('utf-8',{fatal:true}).decode(data));};
 const plan=await normalizeRepair(await parse('PLAN.json')),manifest=await parse('MANIFEST.json'),name=repairSourceName(plan);
 exactKeys(manifest,['schema_id','files'],'返工清单');require(manifest.schema_id==='manju.repair-manifest/v1','返工清单版本无效');exactKeys(manifest.files,['PLAN.json','BRIEF.md',name],'返工文件清单');
 require(entries.has(name),'返工原片路径不符');let sourceEntry;
 for(const [key,e]of entries){if(key==='MANIFEST.json')continue;const file=new File([e.data],key===name?plan.source.filename:key,{type:key===name?({'.mp4':'video/mp4','.mov':'video/quicktime','.webm':'video/webm','.mkv':'video/x-matroska'}[extension(plan.source.filename)]):'application/octet-stream'}),checked=await inspectFile(file);
 require(checked.crc===e.crc&&checked.sha256===manifest.files[key],'返工文件 CRC 或 SHA-256 不符：'+key);if(key===name)sourceEntry=checked;}
 require(sourceEntry.sha256===plan.source.sha256&&sourceEntry.file.size===plan.source.bytes,'实际原片身份与返工单不同');
 require(entries.get('BRIEF.md').size<=2*1024*1024&&await entries.get('BRIEF.md').data.text()===repairBrief(plan),'返工说明与计划不一致');return {plan,entry:sourceEntry};
}
function applyRepair(result){const {plan,entry}=result;repairState.request=clone(plan.request);repairState.source=clone(plan.source);repairState.entry=entry;
 for(const[id,key]of [['repair-start','start_ms'],['repair-end','end_ms'],['repair-before','context_before_ms'],['repair-after','context_after_ms']])$(id).value=String(plan[key]/1000);
 $('repair-preserve').value=plan.preserve.join('\n');$('repair-change').value=plan.change.join('\n');$('repair-audio').value=plan.audio_policy;$('repair-human').value=plan.requested_by;
 repairChanged();repairState.dirty=false;showRepairSource();$('repair-context').textContent=repairBrief(plan);repairNotice('已恢复返工原片与范围；当前确认已清除，上方任务和审片记录未改变。');}
$('repair-source').addEventListener('change',handled(async e=>{const file=e.target.files[0];if(!file)return;require(!repairState.busy,'返工文件操作仍在进行');repairState.busy=true;const revision=repairState.revision;
 try{require(VIDEO_EXTS.includes(extension(file.name)),'请选择视频文件');const entry=await inspectFile(file),candidate=await measuredCandidate(file,entry);require(revision===repairState.revision,'读取期间返工表单已改变，请重新选择文件');repairState.entry=entry;repairState.source=candidate;repairChanged();$('repair-end').value=String(candidate.duration_ms/1000);showRepairSource();repairNotice(`原片已绑定：${candidate.filename} · ${candidate.duration_ms/1000} 秒。请缩小需要返工的范围。`);}finally{repairState.busy=false;e.target.value='';}}));
for(const id of REPAIR_FORM)$(id).addEventListener('input',repairChanged);
for(const id of WORKSPACE_FORM)$(id).addEventListener('input',()=>{if(!repairState.request)repairChanged();});
$('repair-current-context').addEventListener('click',handled(()=>{const request=getRequest();repairState.request=clone(request);repairChanged();$('repair-context').textContent=JSON.stringify(request,null,2);repairNotice('已复制上方任务作为返工上下文，之后上方修改不会静默改变这份返工。');}));
for(const [button,target]of [['repair-mark-start','repair-start'],['repair-mark-end','repair-end']])$(button).addEventListener('click',handled(()=>{require(repairState.source,'先选择原片');$(target).value=$('repair-video').currentTime.toFixed(3);repairChanged();}));
$('repair-preview').addEventListener('click',handled(async()=>{require(repairState.source,'先选择原片');const start=repairMs('repair-start'),end=repairMs('repair-end'),before=repairMs('repair-before'),after=repairMs('repair-after');require(start<end&&end<=repairState.source.duration_ms&&before<=10000&&after<=10000,'请检查范围和参考长度');const video=$('repair-video');repairState.previewEnd=Math.min(repairState.source.duration_ms,end+after)/1000;video.currentTime=Math.max(0,start-before)/1000;await video.play();}));
$('repair-video').addEventListener('timeupdate',()=>{const video=$('repair-video');if(repairState.previewEnd!==null&&video.currentTime>=repairState.previewEnd){video.pause();repairState.previewEnd=null;}});
$('repair-export').addEventListener('click',handled(async()=>{require(!repairState.busy,'返工操作仍在进行');repairState.busy=true;try{const {plan,blob}=await repairBundle();download(blob,'MANJU_REPAIR_'+plan.plan_sha256.slice(0,12)+'.zip');$('repair-context').textContent=repairBrief(plan);repairState.dirty=false;repairNotice('已触发返工 ZIP 下载，包含实际原片。请到下载列表确认保存；没有修改或生成视频。');}finally{repairState.busy=false;}}));
$('repair-import').addEventListener('change',handled(async e=>{const file=e.target.files[0];if(!file)return;require(!repairState.busy,'返工操作仍在进行');repairState.busy=true;const rev=repairState.revision;
 try{const result=await readRepair(file);require(rev===repairState.revision,'核验期间返工区发生变化，未替换');require(confirm('返工包已核验。替换当前返工区？请先保存当前返工。上方任务和磁盘工程不会改变。'),'保留当前返工区');applyRepair(result);}finally{repairState.busy=false;e.target.value='';}}));
window.addEventListener('beforeunload',event=>{if(repairState.dirty){event.preventDefault();event.returnValue='';}});
window.ManjuRepair={normalizeRepair,contextWindow:repairWindow,brief:repairBrief,build:repairBundle,read:readRepair,apply:applyRepair,state:repairState};
