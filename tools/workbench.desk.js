// Daily work is a portable envelope around the unchanged Studio/v1 archive.
// No browser persistence, remote calls, derived approvals or implicit writes.
const DESK_FORM=['flex-template-name','flex-template-notes','flex-branch-name'];
const DESK_KEYS=['schema_id','studio_archive_sha256','external_edit','personal_template','scratch_form','confirmations_restored','automatic_execution','project_modified'];
const deskState={busy:false,pending:null,verified:null,intake:null,intakeSerial:0};
function deskBuffers(){return {external_edit:clone(exchangeState.edit),personal_template:clone(flexState.template),scratch_form:studioRawForm(DESK_FORM)};}
function deskFingerprint(){return canonical({view:studioView(),buffers:deskBuffers()});}
function deskGuard(){return canonical({studio:studioGuard(),buffers:deskBuffers()});}
const deskInitialBuffers=canonical(deskBuffers());
function deskNotice(text,error=false){$('desk-save-status').textContent=text;$('desk-save-status').classList.toggle('reason',error);}
function intakeNotice(text,error=false){$('intake-status').textContent=text;$('intake-status').classList.toggle('reason',error);}
function deskNeedsSave(){return deskState.verified?deskFingerprint()!==deskState.verified:studioNeedsBackup()||canonical(deskBuffers())!==deskInitialBuffers;}
async function scanBlob(blob){
 const sha=new SHA256();let crc=0xffffffff;
 // Keep memory bounded even when the original Studio.zip exceeds 128 MiB.
 for(let offset=0;offset<blob.size;offset+=1024*1024){const data=new Uint8Array(await blob.slice(offset,offset+1024*1024).arrayBuffer());sha.update(data);for(const byte of data)crc=(crc>>>8)^CRC_TABLE[(crc^byte)&255];}
 return {sha256:sha.hex(),crc:(crc^0xffffffff)>>>0};
}
async function normalizeDesk(value){
 exactKeys(value,DESK_KEYS,'收工包');require(value.schema_id==='manju.desk-session/v1'&&hashPattern.test(value.studio_archive_sha256)&&value.confirmations_restored===false&&value.automatic_execution===false&&value.project_modified===false,'收工包版本、身份或人工边界无效');
 if(value.external_edit!==null)await normalizeExchange(value.external_edit);
 if(value.personal_template!==null)normalizeTemplate(value.personal_template);
 studioForm(value.scratch_form,DESK_FORM);
 require(jsonBytes(value).length<=2*1024*1024,'待处理文字合计超过 2 MiB，请分别另存外部改稿和模板');
 return clone(value);
}
function deskAssertForm(doc){
 for(const[id,text]of Object.entries(doc.scratch_form)){const test=$(id).cloneNode(true);test.value=text;require(test.value===text,'模板草稿无法原样写入：'+id+'；当前工作未改变。');}
}
async function buildDesk(){
 const guard=deskGuard(),buffers=deskBuffers(),studio=await buildStudio(),scan=await scanBlob(studio.blob);
 const document=await normalizeDesk({schema_id:'manju.desk-session/v1',studio_archive_sha256:scan.sha256,...buffers,confirmations_restored:false,automatic_execution:false,project_modified:false});
 const data=jsonBytes(document),manifest=jsonBytes({schema_id:'manju.desk-manifest/v1',files:{'DESK.json':await hash(data),'STUDIO.zip':scan.sha256}});
 require(activeUIOperations<=1&&guard===deskGuard(),'保存期间工作或待处理材料改变，未导出过时收工包');
 return {document,studio,guard,blob:zipStore([{name:'DESK.json',data,crc:crc32(data)},{name:'STUDIO.zip',data:studio.blob,crc:scan.crc},{name:'MANIFEST.json',data:manifest,crc:crc32(manifest)}])};
}
async function readDesk(blob){
 const entries=await storedZipMembers(blob,'desk');require(entries.size===3&&['DESK.json','STUDIO.zip','MANIFEST.json'].every(n=>entries.has(n)),'收工包文件集合不完整');
 const parse=async name=>{const entry=entries.get(name),raw=new Uint8Array(await entry.data.arrayBuffer());require(crc32(raw)===entry.crc,'收工包元数据 CRC 不符');return {raw,value:strictJSON(new TextDecoder('utf-8',{fatal:true}).decode(raw))};};
 const raw=await parse('DESK.json'),document=await normalizeDesk(raw.value),manifest=(await parse('MANIFEST.json')).value;
 exactKeys(manifest,['schema_id','files'],'收工包清单');require(manifest.schema_id==='manju.desk-manifest/v1','收工包清单版本不符');exactKeys(manifest.files,['DESK.json','STUDIO.zip'],'收工包文件清单');
 const entry=entries.get('STUDIO.zip'),scan=await scanBlob(entry.data);
 require(scan.crc===entry.crc&&scan.sha256===document.studio_archive_sha256&&scan.sha256===manifest.files['STUDIO.zip']&&await hash(raw.raw)===manifest.files['DESK.json'],'收工包内容校验不符，未恢复');
 const studio=await readStudio(entry.data);deskAssertForm(document);assertStudioRepresentable(studio.document);
 return {document,studio};
}
function deskClearRestore(){deskState.pending=null;$('desk-restore-preview').hidden=true;$('desk-restore-confirmed').checked=false;$('desk-restore-apply').disabled=true;}
function deskInstallBuffers(doc){
 exchangeClearPreview();exchangeState.edit=clone(doc.external_edit);exchangeState.undo=null;exchangeState.lastReport=null;exchangeState.serial++;
 $('exchange-preview').disabled=!exchangeState.edit;$('exchange-import-texts').disabled=!exchangeState.edit;$('exchange-report').disabled=true;
 $('exchange-import-status').textContent=exchangeState.edit?'已恢复待处理外部改稿。尚未应用，请重新预览三方比较。':'收工包内没有待处理外部改稿，旧缓冲已清除。';
 $('exchange-rows').replaceChildren();exchangeNotice('收工包中的改稿只是待处理材料，不是已接受的修改。');
 flexClearPreview();flexState.template=clone(doc.personal_template);flexState.donor=null;flexState.undo=null;flexState.serial++;
 $('flex-preview-template').disabled=!flexState.template;$('flex-preview-compose').disabled=true;$('flex-release-donor').disabled=true;
 $('flex-template-status').textContent=flexState.template?'已恢复待应用模板：'+flexState.template.name+'。请重新预览，尚未应用。':'收工包内没有待应用模板。';
 for(const[id,text]of Object.entries(doc.scratch_form))chooseValue(id,text);
 $('flex-template-mode').value='fill_empty';$('flex-confirmed').checked=false;
 exchangeChanged();flexChanged();
}
async function deskPreview(blob,{source="file"}={}){
 require(["file","local_recovery"].includes(source),"未知恢复来源，当前工作未改变");
 const guard=deskGuard(),result=await readDesk(blob);require(activeUIOperations<=1&&guard===deskGuard(),'核验期间工作或待处理材料改变，旧结果未应用，请重新打开');
 deskClearRestore();deskState.pending={result,guard,source};const d=result.document,s=result.studio.document;
 $('desk-restore-summary').textContent=`将替换三个工作区和待处理材料，不是自动合并。\n镜头：${s.workspace.draft.form['shot-id']}\n原素材：${s.media.length} 个，${s.media.reduce((n,m)=>n+m.bytes,0)} 字节\n待处理外部改稿：${d.external_edit?Object.keys(d.external_edit.values).length+' 个字段':'无，恢复会清除当前缓冲'}\n已加载模板：${d.personal_template?.name||'无，恢复会清除当前模板'}\n模板名称、说明和命名副本草稿：保留\n当前确认全部清除，质量优先开启；不自动接受外部修改或模板。`;
 $('desk-restore-preview').hidden=false;deskNotice('收工包核验完成。当前工作仍在，请查看恢复预览。');$('desk-restore-preview').scrollIntoView({block:'center'});
}
function deskChanged(){
 if(deskState.pending&&deskState.pending.guard!==deskGuard()){deskState.pending=null;$('desk-restore-confirmed').checked=false;$('desk-restore-apply').disabled=true;$('desk-restore-summary').textContent='预览后工作或待处理材料已变化，旧预览失效。请重新打开收工包；新内容没有被覆盖。';}
 if(deskState.intake&&deskState.intake.guard!==deskGuard()){deskState.intake=null;$('intake-open').disabled=true;$('intake-confirmed').checked=false;intakeNotice('识别后工作已变化，请重新选择文件；未导入或覆盖新内容。');}
 if(deskState.verified&&deskNeedsSave())deskNotice('有未核验的新修改或待处理材料。此前收工包仍在，请重新保存并选回文件核验。');
 $('desk-inventory').textContent=`当前：原素材 ${studioBindings().size} 个；待处理外部改稿 ${exchangeState.edit?'1 份':'无'}；已加载模板 ${flexState.template?'1 份':'无'}。`;
 $('desk-restore-apply').disabled=!deskState.pending||!$('desk-restore-confirmed').checked;
 const choice=deskState.intake?.routes[Number($('intake-destination').value)];$('intake-replace-note').hidden=!choice?.replace;$('intake-open').disabled=!choice||(choice.replace&&!$('intake-confirmed').checked);
}
function deskHandled(fn){return handled(async event=>{try{await fn(event);}catch(error){deskNotice('未完成：'+(error.message||error),true);throw error;}finally{deskChanged();}});}
$('desk-save').addEventListener('click',deskHandled(async()=>{
 require(!studioBusy(),'其他文件操作仍在进行');deskState.busy=true;
 try{const result=await buildDesk();require(result.guard===deskGuard(),'工作已变化，请重试');download(result.blob,'MANJU_DESK_'+(await hash(result.document)).slice(0,12)+'.zip');deskNotice('已发起收工包下载。包含原工作现场和待处理改稿 / 模板；请保存后选回文件核验，尚不能声称已落盘。');}finally{deskState.busy=false;}
}));
$('desk-verify-file').addEventListener('change',deskHandled(async event=>{
 const file=event.target.files[0];if(!file)return;require(!studioBusy(),'其他文件操作仍在进行');deskState.busy=true;const guard=deskGuard();
 try{const result=await readDesk(file);require(guard===deskGuard()&&activeUIOperations<=1,'核验期间内容改变，未标记备份');const buffers={external_edit:result.document.external_edit,personal_template:result.document.personal_template,scratch_form:result.document.scratch_form};
  const matches=canonical({view:studioDocumentView(result.studio.document),buffers})===deskFingerprint();
  if(matches){deskState.verified=deskFingerprint();studioState.verified=canonical(studioView());state.dirty=false;reviewState.dirty=false;repairState.dirty=false;directorState.dirty=false;deskNotice('已核验你选回的收工包，三个工作区和待处理材料均与当前一致。只核验，没有替换工作。');}
  else deskNotice('所选收工包有效，但与当前工作或待处理材料不同。没有替换，也没有把新修改标为已备份。');
 }finally{deskState.busy=false;event.target.value='';}
}));
$('desk-restore-confirmed').addEventListener('change',deskChanged);
$('desk-restore-cancel').addEventListener('click',()=>{deskClearRestore();deskNotice('取消恢复，当前工作和待处理材料不变。');});
$('desk-restore-apply').addEventListener('click',deskHandled(()=>{
 require(!studioBusy()&&deskState.pending&&$('desk-restore-confirmed').checked,'请核验预览并明确确认');require(deskState.pending.guard===deskGuard(),'预览已过时，不能覆盖新内容');
 const result=deskState.pending.result,localRecovery=deskState.pending.source==="local_recovery";deskAssertForm(result.document);assertStudioRepresentable(result.studio.document);
 studioApplyRaw(result.studio);deskInstallBuffers(result.document);studioClearPending();deskClearRestore();
 deskState.verified=localRecovery?null:deskFingerprint();studioState.verified=localRecovery?null:canonical(studioView());deskNotice(localRecovery?'已从本机恢复点取回现场。待处理意见和模板尚未应用；没有标记独立备份，请收工时另存 ZIP 并选回核验。':'收工包已恢复。原素材可直接使用；外部改稿和模板仍待预览，未接受任何修改或批准。');
}));
// Identification is read-only. Files only enter a real input after a user click.
function intakeRoute(label,input,section,replace=false,extra={}){return {label,input,section,replace,...extra};}
async function identifyIntake(files){
 require(files.length>0&&files.length<=32,'一次选择 1 至 32 个文件；JSON 或 ZIP 请一次一个');
 if(files.every(f=>extension(f.name)==='.txt')){require(files.every(f=>f.size<=120000),'单个 TXT 超过文字上限');return {kind:'texts',summary:'配套外部改稿 TXT。先加载对应 EDIT.json，再由第09区核对编号。',routes:[intakeRoute('送到外部改稿，核对原编号','exchange-import-texts','exchange-section')]};}
 const allowed=[...IMAGE_EXTS,...VIDEO_EXTS,...AUDIO_EXTS];
 if(files.every(f=>allowed.includes(extension(f.name)))){
  require(files.every(f=>f.size>0&&f.size<=MAX_FILE)&&files.reduce((n,f)=>n+f.size,0)<=MAX_TOTAL,'媒体超出单文件128 MiB / 合计512 MiB限制');
  const image=files.every(f=>IMAGE_EXTS.includes(extension(f.name))),video=files.every(f=>VIDEO_EXTS.includes(extension(f.name))),audio=files.every(f=>AUDIO_EXTS.includes(extension(f.name)));const routes=[];
  if(video){routes.push(intakeRoute('作为新候选，进入审片','review-files','review-section'));if(files.length===1){routes.push(intakeRoute('作为返工原片（替换当前底片）','repair-source','repair-section',true),intakeRoute('作为导演运动底片（替换当前底片）','director-source','director-section',true));}}
  const roles=image?['first_frame','last_frame','subject_reference']:video?['source_video','reference_video','performance_video']:audio?['reference_audio']:[];
  for(const role of roles)routes.push(intakeRoute('绑定为 '+ROLE_NAMES[role],'asset-files','workspace-section',false,{role}));
  require(routes.length,'请按同类媒体分批打开，不猜测混合文件的用途');return {kind:'media',summary:`${files.length} 个媒体文件。名称/扩展名仅用于提出入口，目标区还会检查实际文件；不会按同名自动覆盖旧候选。`,routes};
 }
 require(files.length===1,'不同类型材料请分批打开，未导入其中任何一项');const file=files[0];
 require(file.size>0&&file.size<=MAX_TOTAL+8*1024*1024,'文件为空或超出本地大小上限');
 const header=new Uint8Array(await file.slice(0,4).arrayBuffer());
 if(header[0]===0x50&&header[1]===0x4b){
  const entries=await storedZipMembers(file,'intake');const markers=['DESK.json','STUDIO.json','WORKSPACE.json','PLAN.json','EDIT.json','REQUEST.json'].filter(n=>entries.has(n));
  require(markers.length===1,'无法唯一识别此 ZIP。完整影片包或示例合集请先解压，再打开其中的材料文件。');
  const name=markers[0],entry=entries.get(name),bytes=new Uint8Array(await entry.data.arrayBuffer());require(crc32(bytes)===entry.crc,'材料元数据 CRC 不符');const doc=strictJSON(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
  if(name==='DESK.json'&&doc.schema_id==='manju.desk-session/v1')return {kind:'desk',summary:'收工包：三个工作区与待处理改稿 / 模板。下一步先完整核验并预览，不立即恢复。',routes:[intakeRoute('核验并预览恢复收工包',null,'desk-restore-preview',false,{desk:true})]};
  if(name==='STUDIO.json'&&doc.schema_id==='manju.studio-session/v1')return {kind:'studio',summary:'三个工作区的旧版总备份。可以完整恢复，也可以只取用其中一个工作区。',routes:[intakeRoute('核验并预览完整恢复','import-studio','studio-home'),intakeRoute('只取用部分工作区','flex-donor-file','flex-section')]};
  if(name==='WORKSPACE.json'&&doc.schema_id==='manju.authoring-workspace/v1')return {kind:'workspace',summary:'镜头与审片现场；不包含独立返工和导演区。',routes:[intakeRoute('核验并恢复镜头与审片','import-workspace','workspace-section')]};
  if(name==='PLAN.json'&&doc.schema_id==='manju.repair-plan/v1')return {kind:'repair',summary:'局部返工材料，包含原片。目标入口会重新核验并要求确认。',routes:[intakeRoute('打开返工材料','repair-import','repair-section')]};
  if(name==='PLAN.json'&&doc.schema_id==='manju.director-plan/v1')return {kind:'director',summary:'导演材料，包含底片、原帧和目标图。',routes:[intakeRoute('打开导演材料','director-import','director-section')]};
  if(name==='EDIT.json'&&doc.schema_id==='manju.external-edit/v1')throw new Error('这是外部编辑材料 ZIP，不是工作备份。请解压，修改后打开其中 EDIT.json 和配套 TXT；媒体按实际用途另行添加。');
  throw new Error('此 ZIP 不是已支持的可恢复材料；交接包和任意剪辑工程不会被假装还原。');
 }
 require(file.size<=2*1024*1024,'未知文件不作为 JSON 读取；JSON 最大 2 MiB');
 const doc=strictJSON(new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer()));require(doc&&typeof doc==='object'&&!Array.isArray(doc),'材料必须是带版本标记的对象');
 const routes={
  'manju.model-request/v1':['request','镜头任务，不含媒体。导入会替换上方任务，原工程不变。',intakeRoute('导入镜头任务','import-request','workspace-section',true)],
  'manju.project-authoring-import/v1':['request','主影片只读导出的镜头与上下文。此页修改不会自动写回原工程。',intakeRoute('导入工程镜头','import-request','workspace-section',true)],
  'manju.model-catalog/v1':['catalog','型号能力目录。先展示差异，确认后才替换。',intakeRoute('预览能力档变化','import-catalog','catalog-preview')],
  'manju.creative-template/v1':['template','纯文字个人模板，先核验再预览，不立即套用。',intakeRoute('载入待应用模板','flex-template-file','flex-section')],
  'manju.external-edit/v1':['external','外部文字修改。只加载到缓冲，再三方比较；不直接覆盖本地。',intakeRoute('载入外部改稿','exchange-import-json','exchange-section')],
  'manju.review-document/v1':['review','历史审片记录，不含视频字节；不是新的批准。',intakeRoute('导入历史审片记录','import-review','review-section',true)]
 };
 const selected=routes[doc.schema_id];require(selected,'未支持的 JSON 类型或版本；没有改写、执行或导入任何内容');return {kind:selected[0],summary:selected[1],routes:[selected[2]]};
}
async function prepareIntake(files){
 require(!studioBusy(),'其他文件操作仍在进行');deskState.busy=true;const serial=++deskState.intakeSerial,guard=deskGuard();deskState.intake=null;$('intake-open').disabled=true;$('intake-preview').hidden=true;
 try{const result=await identifyIntake(files);require(serial===deskState.intakeSerial&&guard===deskGuard()&&activeUIOperations<=1,'识别期间工作或材料已变化，请重新打开');deskState.intake={...result,files,guard};$('intake-summary').textContent=result.summary;$('intake-destination').replaceChildren(...result.routes.map((r,i)=>{const option=element('option',r.label);option.value=String(i);return option;}));$('intake-confirmed').checked=false;$('intake-preview').hidden=false;intakeNotice('已识别 '+files.map(f=>f.name).join('、')+'。当前工作尚未改变。');}
 finally{deskState.busy=false;deskChanged();}
}
$('intake-files').addEventListener('change',handled(async event=>{try{await prepareIntake([...event.target.files]);}catch(e){intakeNotice('未打开：'+e.message,true);throw e;}finally{event.target.value='';}}));
$('intake-destination').addEventListener('change',()=>{$('intake-confirmed').checked=false;deskChanged();});$('intake-confirmed').addEventListener('change',deskChanged);
$('intake-cancel').addEventListener('click',()=>{deskState.intakeSerial++;deskState.intake=null;$('intake-preview').hidden=true;intakeNotice('已取消，原文件和当前工作未改变。');});
function intakeReveal(id){const target=$(id);if(!target)return;for(let p=target;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;target.scrollIntoView({block:'start'});}
$('intake-open').addEventListener('click',()=>{
 try{
  require(!studioBusy()&&activeUIOperations===0&&deskState.intake,'请先识别文件，等待当前操作完成');const current=deskState.intake,route=current.routes[Number($('intake-destination').value)];require(current.guard===deskGuard()&&route,'工作已改变，请重新打开');require(!route.replace||$('intake-confirmed').checked,'请明确确认替换目标区');
  if(route.input==='exchange-import-texts')require(exchangeState.edit,'先打开本次配套的 EDIT.json，再取回 TXT；当前文字不变');
  deskState.intake=null;$('intake-preview').hidden=true;
  if(route.desk){deskHandled(async()=>{require(!studioBusy(),'其他文件操作仍在进行');deskState.busy=true;try{await deskPreview(current.files[0]);}finally{deskState.busy=false;}})();return;}
  const input=$(route.input);require(input&&!input.disabled,'该入口尚未就绪');const transfer=new DataTransfer();for(const file of current.files)transfer.items.add(file);input.files=transfer.files;if(route.role)$('asset-role').value=route.role;
  // Dispatch outside a handled operation: the original input owns its lifecycle.
  input.dispatchEvent(new Event('change',{bubbles:true}));intakeReveal(input.closest('details')?.id||route.section);
  intakeNotice('已送到“'+route.label+'”。请按目标区显示的核验结果继续，未自动接受任何批准。');
 }catch(e){intakeNotice('未打开：'+e.message,true);status(e.message,true);}finally{deskChanged();}
});
const intakeDrop=$('intake-drop');
for(const type of ['dragenter','dragover'])intakeDrop.addEventListener(type,event=>{if([...event.dataTransfer.types].includes('Files')){event.preventDefault();intakeDrop.classList.add('drag-over');}});
intakeDrop.addEventListener('dragleave',()=>intakeDrop.classList.remove('drag-over'));
intakeDrop.addEventListener('drop',handled(async event=>{event.preventDefault();intakeDrop.classList.remove('drag-over');const files=[...event.dataTransfer.files];if(!files.length){intakeNotice('这里只接收本地文件，不访问拖入的网址。',true);return;}try{await prepareIntake(files);}catch(e){intakeNotice('未打开：'+e.message,true);throw e;}}));
// Dropping a file outside the inbox must not navigate away and lose drafts.
document.addEventListener('dragover',event=>{if([...event.dataTransfer?.types||[]].includes('Files'))event.preventDefault();});
document.addEventListener('drop',event=>{
 if(![...event.dataTransfer?.types||[]].includes('Files'))return;event.preventDefault();
 if(intakeDrop.contains(event.target))return;
 const files=[...event.dataTransfer.files];handled(async()=>{try{await prepareIntake(files);intakeDrop.scrollIntoView({block:'center'});}catch(e){intakeNotice('未打开：'+e.message,true);throw e;}})();
});
intakeDrop.addEventListener('keydown',event=>{if(event.target===intakeDrop&&['Enter',' '].includes(event.key)){event.preventDefault();$('intake-files').click();}});
window.addEventListener('beforeunload',event=>{if(deskNeedsSave()){event.preventDefault();event.returnValue='';}});
for(const type of ['input','change','click'])document.addEventListener(type,()=>queueMicrotask(deskChanged));
window.ManjuDesk={restoreSourceVersion:1,state:deskState,isBusy:()=>deskState.busy,changed:deskChanged,needsSave:deskNeedsSave,view:deskBuffers,normalize:normalizeDesk,build:buildDesk,read:readDesk,preview:deskPreview,identify:identifyIntake,prepare:prepareIntake,fingerprint:deskFingerprint};
deskChanged();
