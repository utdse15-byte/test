// Whole-studio checkpoints reuse Workspace/v1 and never manufacture approvals.
const STUDIO_KEYS=['schema_id','workspace','repair','director','auxiliary_form','media','quality_only_on_restore','confirmations_restored','automatic_execution','project_modified'];
const STUDIO_AUXILIARY_FORM=['return-width','return-height','promotion-resolution','asset-role'];
const studioState={busy:false,pending:null,baseline:null,verified:null,serial:0};
function studioRawForm(ids){return Object.fromEntries(ids.map(id=>[id,$(id).value]));}
function studioView(){return {workspace:workspaceView(),repair:{form:studioRawForm(REPAIR_FORM),request:clone(repairState.request),source:clone(repairState.source)},director:{form:studioRawForm(DIRECTOR_FORM),source:clone(directorState.source),anchors:clone(directorState.anchors)},auxiliary_form:studioRawForm(STUDIO_AUXILIARY_FORM)};}
function studioDocumentView(doc){const w=doc.workspace;return {workspace:{draft:w.draft,catalog:w.catalog,review:w.review,pending:w.pending,review_form:w.review_form,reveal:w.reveal},repair:doc.repair,director:doc.director,auxiliary_form:doc.auxiliary_form};}
function studioBusy(){return Boolean(window.ManjuFlex?.isBusy())||activeUIOperations>1||studioState.busy||workspaceBusy||state.busy||reviewState.busy||repairState.busy||directorState.busy;}
function studioGuard(){return canonical({view:studioView(),quality:$('quality-only').checked,revisions:[state.revision,state.generation,reviewState.revision,reviewState.formRevision,repairState.revision,directorState.revision,catalogRevision.serial]});}
function studioMedia(doc){
 const required=requiredWorkspaceMedia(doc.workspace),geometry=new Map();
 const videos=[...doc.workspace.pending,...(doc.workspace.review?.session.candidates||[]),doc.repair.source,doc.director.source].filter(Boolean);
 const rasters=doc.director.anchors.flatMap(a=>[a.frame,a.guide]).filter(Boolean);
 for(const rec of [...videos,...rasters]){
  require(!required.has(rec.sha256)||required.get(rec.sha256)===rec.bytes,'相同媒体哈希声明不同大小');required.set(rec.sha256,rec.bytes);
  const dimensions=[rec.width??null,rec.height??null,rec.duration_ms??null],old=geometry.get(rec.sha256);
  if(old){require(old.every((v,i)=>v===null||dimensions[i]===null||v===dimensions[i]),'同一媒体的尺寸或时长声明冲突');for(let i=0;i<3;i++)if(dimensions[i]===null)dimensions[i]=old[i];}
  geometry.set(rec.sha256,dimensions);
 }
 return required;
}
function studioForm(form,ids){exactKeys(form,ids,'总备份原始表单');for(const s of Object.values(form))require(typeof s==='string'&&s.length<=30000,'总备份表单超长或类型无效');}
function studioSource(source){if(source===null)return null;const c=candidateValue(source);require(canonical(c)===canonical(source)&&c.media_check!=='hash_only'&&c.bytes<=MAX_FILE,'返工与导演底片必须有实测元数据');return c;}
async function normalizeStudio(value){
 exactKeys(value,STUDIO_KEYS,'总备份');require(value.schema_id==='manju.studio-session/v1'&&value.quality_only_on_restore===true&&value.confirmations_restored===false&&value.automatic_execution===false&&value.project_modified===false,'总备份版本或安全边界不正确');
 const workspace=await normalizeWorkspace(clone(value.workspace)),r=value.repair,d=value.director;
 exactKeys(r,['form','request','source'],'返工草稿');studioForm(r.form,REPAIR_FORM);studioSource(r.source);
 if(r.request!==null)require(canonical(normalizeRequest(r.request))===canonical(r.request),'返工任务上下文格式不规范');
 exactKeys(d,['form','source','anchors'],'导演草稿');studioForm(d.form,DIRECTOR_FORM);studioSource(d.source);
 require(Array.isArray(d.anchors)&&d.anchors.length<=16&&(!d.anchors.length||d.source!==null),'导演草稿锚点或底片缺失');
 const ids=new Set(),times=new Set();for(const a of d.anchors){
  exactKeys(a,ANCHOR_KEYS,'导演草稿锚点');require(/^A[0-9]{2}$/.test(a.id)&&!ids.has(a.id),'导演锚点ID重复或无效');ids.add(a.id);
  integer(a.source_time_ms,0,86400000,'锚点毫秒');require(!times.has(a.source_time_ms)&&a.source_time_ms<d.source.duration_ms,'锚点时间重复或越界');times.add(a.source_time_ms);
  const frame=directorRaster(a.frame);require(frame.width===d.source.width&&frame.height===d.source.height,'原帧尺寸与底片不同');
  if(a.guide!==null){const guide=directorRaster(a.guide);require(guide.width*frame.height===frame.width*guide.height,'目标图与原帧画幅不同');}
  require(typeof a.target==='string'&&a.target.length<=4000,'目标文字过长');require(a.capture_method==='browser_canvas_sdr_reference'&&a.time_basis==='presentation_time_not_certified_frame_index','参考帧方法标记无效');
 }
 studioForm(value.auxiliary_form,STUDIO_AUXILIARY_FORM);
 require(Array.isArray(value.media)&&value.media.length<=134,'总备份素材数量超限');
 for(const m of value.media){exactKeys(m,['sha256','bytes','filename','mime_type'],'总素材绑定');require(hashPattern.test(m.sha256),'总素材哈希无效');integer(m.bytes,1,MAX_FILE,'素材字节');safePath(m.filename);require(typeof m.filename==='string'&&m.filename.length<=240&&!m.filename.includes('/'),'素材文件名无效');require(typeof m.mime_type==='string'&&m.mime_type.length<=100&&!/[\x00-\x1f]/.test(m.mime_type),'素材类型无效');}
 const doc={...clone(value),workspace},needed=studioMedia(doc),bound=new Map(doc.media.map(m=>[m.sha256,m.bytes]));
 require(bound.size===doc.media.length&&canonical([...needed].sort())===canonical([...bound].sort()),'总备份必须包含全部且仅被引用的实际素材');
 require([...needed.values()].reduce((n,x)=>n+x,0)<=MAX_TOTAL,'三个区去重后的总素材超过本地 512 MiB');
 require(jsonBytes(doc).length<=2*1024*1024&&canonical(doc)===canonical(value),'总备份元数据超限或包含隐式转换');return doc;
}
function studioBindings(){
 const result=new Map([...reviewState.files,...state.files]);
 for(const entry of [repairState.entry,directorState.entry,...directorState.files.values()])if(entry)result.set(entry.sha256,entry);
 return result;
}
async function buildStudio(){
 const snapshot=studioView(),guard=studioGuard(),all=studioBindings();
 const workspaceRequired=requiredWorkspaceMedia(snapshot.workspace),needed=studioMedia(snapshot),media=[],entries=[],files={};
 // First verify every media byte, without mutating the user's live state.
 for(const [h,size]of needed){const entry=all.get(h);require(entry,'总备份缺少原文件：'+h.slice(0,12)+'；未生成空壳备份');status('正在总备份并校验：'+entry.file.name);const checked=await inspectFile(entry.file);require(checked.sha256===h&&checked.file.size===size,'实际文件发生变化，拒绝保存');media.push({sha256:h,bytes:size,filename:entry.file.name,mime_type:entry.file.type});entries.push({name:'media/'+h,data:entry.file,crc:checked.crc});files['media/'+h]=h;}
 const workspaceMedia=[];const workspaceBindings=new Map([...state.files,...reviewState.files]);
 for(const [h,size]of workspaceRequired){const entry=workspaceBindings.get(h)||all.get(h);workspaceMedia.push({sha256:h,bytes:size,filename:entry.file.name,mime_type:entry.file.type});}
 const workspace={schema_id:'manju.authoring-workspace/v1',...snapshot.workspace,media:workspaceMedia,confirmations_restored:false,project_modified:false};
 const document=await normalizeStudio({schema_id:'manju.studio-session/v1',...snapshot,workspace,media,quality_only_on_restore:true,confirmations_restored:false,automatic_execution:false,project_modified:false});
 const data=jsonBytes(document);entries.push({name:'STUDIO.json',data,crc:crc32(data)});files['STUDIO.json']=await hash(data);
 const inventory=jsonBytes({schema_id:'manju.studio-manifest/v1',files});entries.push({name:'MANIFEST.json',data:inventory,crc:crc32(inventory)});
 require(activeUIOperations<=1&&guard===studioGuard(),'打包期间某个工作区已变化，没有导出过时现场');return {document,blob:zipStore(entries),guard};
}
function studioEntry(bindings,record,type){const e=bindings.get(record.sha256);require(e,'缺少已核验媒体');return {...e,file:new File([e.file],record.filename,{type:type||e.file.type})};}
async function readStudio(blob){
 const entries=await storedZipMembers(blob,'studio');require(entries.has('STUDIO.json')&&entries.has('MANIFEST.json'),'总备份缺少元数据');
 const parse=async name=>{const e=entries.get(name),data=new Uint8Array(await e.data.arrayBuffer());require(crc32(data)===e.crc,'总备份元数据CRC不符');return strictJSON(new TextDecoder('utf-8',{fatal:true}).decode(data));};
 const doc=await normalizeStudio(await parse('STUDIO.json')),manifest=await parse('MANIFEST.json');exactKeys(manifest,['schema_id','files'],'总备份清单');require(manifest.schema_id==='manju.studio-manifest/v1','总备份清单版本无效');
 const names=['STUDIO.json',...doc.media.map(m=>'media/'+m.sha256)].sort();exactKeys(manifest.files,names,'总备份哈希清单');require(canonical([...names,'MANIFEST.json'].sort())===canonical([...entries.keys()].sort()),'总备份存在缺少或多余文件');
 const bindings=new Map(),records=new Map(doc.media.map(m=>['media/'+m.sha256,m]));
 for(const name of names){const e=entries.get(name),m=records.get(name),file=new File([e.data],m?m.filename:name,{type:m?m.mime_type:'application/json'}),fresh=await inspectFile(file);require(fresh.crc===e.crc&&fresh.sha256===manifest.files[name],'总备份文件哈希或CRC不符：'+name);if(m){require(fresh.sha256===m.sha256&&file.size===m.bytes,'总备份素材身份不符');bindings.set(m.sha256,fresh);}}
 // Browser recovery independently measures referenced videos and decodes PNGs.
 const videos=[...doc.workspace.pending,...(doc.workspace.review?.session.candidates||[]),doc.repair.source,doc.director.source].filter(Boolean),measured=new Map();
 for(const c of videos){const entry=studioEntry(bindings,c);let actual=measured.get(c.sha256);if(!actual){actual=await measuredCandidate(entry.file,entry);measured.set(c.sha256,actual);}for(const k of ['width','height','duration_ms'])require(c[k]===null||c[k]===actual[k],'总备份视频实测元数据与记录不符：'+c.filename);}
 const rasters=new Map();for(const a of doc.director.anchors)for(const rec of [a.frame,a.guide])if(rec){let actual=rasters.get(rec.sha256);if(!actual){actual=(await directorPNG(studioEntry(bindings,rec,'image/png').file)).raster;rasters.set(rec.sha256,actual);}require(actual.width===rec.width&&actual.height===rec.height,'总备份PNG实测尺寸与记录不符');}
 return {document:doc,bindings};
}
function studioApplyRaw(result){
 const {document:doc,bindings}=result,workspaceBindings=new Map(doc.workspace.media.map(m=>[m.sha256,studioEntry(bindings,m,m.mime_type)]));
 // No awaits after validation. Media views are rebuilt from verified bindings.
 applyWorkspace({document:doc.workspace,bindings:workspaceBindings});
 for(const [id,text]of Object.entries(doc.repair.form))chooseValue(id,text);
 repairState.request=clone(doc.repair.request);repairState.source=clone(doc.repair.source);repairState.entry=doc.repair.source?studioEntry(bindings,doc.repair.source):null;repairState.previewEnd=null;repairChanged();showRepairSource();$('repair-context').textContent=doc.repair.request?JSON.stringify(doc.repair.request,null,2):'';
 for(const [id,text]of Object.entries(doc.director.form))chooseValue(id,text);
 directorState.source=clone(doc.director.source);directorState.entry=doc.director.source?studioEntry(bindings,doc.director.source):null;directorState.anchors=clone(doc.director.anchors);directorState.files=new Map();
 for(const a of doc.director.anchors)for(const [kind,rec]of [['frames',a.frame],['guides',a.guide]])if(rec)directorState.files.set(directorRasterName(rec,kind),studioEntry(bindings,rec,'image/png'));
 directorChanged();directorSourceView();directorRender();$('director-brief').textContent='';
 for(const[id,text]of Object.entries(doc.auxiliary_form))chooseValue(id,text);
 $('return-results').replaceChildren();$('return-raster-note').textContent='已恢复像素输入；旧规格报告未沿用，请重新检查实际候选。';
 catalogRevision.pending=null;catalogRevision.report=null;catalogRevision.baseline=null;catalogRevision.form=null;catalogRevision.previous=null;catalogRevision.serial++;$('catalog-preview').hidden=true;$('apply-catalog').disabled=true;
 for(const id of ['human-confirmed','ack-warnings','review-confirmed','promotion-confirmed','repair-confirmed','catalog-confirmed','studio-restore-confirmed'])$(id).checked=false;
 $('quality-only').checked=true;state.selected=null;state.plan=null;$('quality-summary').textContent='已恢复到质量优先；重新检查任务，不沿用旧型号选择。';
 state.dirty=false;reviewState.dirty=false;repairState.dirty=false;directorState.dirty=false;
 repairNotice('已随总备份恢复返工草稿；确认已清除，尚未执行。');directorNotice('已随总备份恢复导演草稿与真实素材；尚未生成或批准。');
}
function studioClearPending(){studioState.pending=null;studioState.baseline=null;studioState.serial++;$('studio-restore-preview').hidden=true;$('studio-restore-confirmed').checked=false;$('apply-studio').disabled=true;}
function studioNotice(text,error=false){$('studio-save-status').textContent=text;$('studio-save-status').classList.toggle('reason',error);}
function studioChanged(){
 if(studioState.pending&&studioState.baseline!==studioGuard()){
  studioState.pending=null;studioState.baseline=null;$('studio-restore-confirmed').checked=false;$('apply-studio').disabled=true;
  $('studio-restore-summary').textContent='预览后现场已变化，旧预览已失效。请重新选择总备份核验；当前工作没有被替换。';
 }
 if(studioState.verified&&studioState.verified!==canonical(studioView()))studioNotice('当前现场已变化。之前的本地备份仍在，但这些新修改还没有核验过总备份。');
}
function studioNeedsBackup(){return canonical(studioView())!==(studioState.verified||studioInitial);}
const studioInitial=canonical(studioView());
function studioHandled(fn){return handled(async event=>{try{return await fn(event);}catch(error){studioNotice('未完成：'+String(error.message||error),true);throw error;}});}
$('export-studio').addEventListener('click',studioHandled(async()=>{
 require(!studioBusy(),'其他文件操作仍在进行');studioState.busy=true;
 try{const result=await buildStudio();require(result.guard===studioGuard(),'工作现场已经变化，请重试');download(result.blob,'MANJU_STUDIO_'+(await hash(result.document)).slice(0,12)+'.zip');studioNotice(`已发起总备份下载，包含 ${result.document.media.length} 个去重文件。请保存后选回文件核验；当前不能声称已经落盘。`);status('总备份已交给浏览器下载。三个工作区和原素材未改动。');}finally{studioState.busy=false;}
}));
$('import-studio').addEventListener('change',studioHandled(async event=>{
 const file=event.target.files[0];if(!file)return;require(!studioBusy(),'其他文件操作仍在进行');studioState.busy=true;studioClearPending();const guard=studioGuard();
 try{const result=await readStudio(file);require(activeUIOperations<=1&&guard===studioGuard(),'核验期间工作现场变化，未生成恢复预览');studioState.pending=result;studioState.baseline=guard;const d=result.document;
  $('studio-restore-summary').textContent=`将替换三个工作区，不是追加或合并。\n镜头：${d.workspace.draft.form['shot-id']}\n去重素材：${d.media.length} 个，${d.media.reduce((n,m)=>n+m.bytes,0)} 字节\n审片历史：${d.workspace.review?.decisions.length||0} 条决定\n返工原片：${d.repair.source?.filename||'尚未绑定；原始文字仍保留'}\n导演锚点：${d.director.anchors.length} 个\n质量优先：开启；当前型号选择和确认：全部清除\n能力档、待填写设置随备份恢复；派生报告和未应用预览不恢复。`;
  $('studio-restore-preview').hidden=false;studioNotice('总备份已核验，当前工作尚未替换。查看预览后再确认恢复。');
 }finally{studioState.busy=false;event.target.value='';}
}));
$('studio-restore-confirmed').addEventListener('change',()=>{$('apply-studio').disabled=!$('studio-restore-confirmed').checked||!studioState.pending||studioState.baseline!==studioGuard();});
$('cancel-studio-restore').addEventListener('click',()=>{studioClearPending();studioNotice('已取消恢复，当前三个区保持原样。');});
$('apply-studio').addEventListener('click',studioHandled(()=>{
 require(!studioBusy()&&studioState.pending,'没有可应用的恢复预览');require($('studio-restore-confirmed').checked,'请先明确确认替换');require(studioState.baseline===studioGuard(),'预览已经过时，拒绝覆盖新工作');
 const result=studioState.pending;studioApplyRaw(result);studioClearPending();studioState.verified=canonical(studioView());studioNotice('三个工作区已从实际核验的ZIP恢复；原视频与图像可直接使用。质量优先开启，当前确认已清除。');status('总备份恢复完成，没有自动生成、选片或改写影片工程。');
}));
$('verify-studio-download').addEventListener('change',studioHandled(async event=>{
 const file=event.target.files[0];if(!file)return;require(!studioBusy(),'其他文件操作仍在进行');studioState.busy=true;const guard=studioGuard();
 try{const result=await readStudio(file);require(activeUIOperations<=1&&guard===studioGuard(),'核验期间现场变化，未标记为已备份');const matches=canonical(studioDocumentView(result.document))===canonical(studioView());
  if(matches){studioState.verified=canonical(studioView());state.dirty=false;reviewState.dirty=false;repairState.dirty=false;directorState.dirty=false;studioNotice('已核验你选回的本地总备份，内容与当前三个工作区一致。未替换任何工作，原文件未改动。');}
  else studioNotice('所选总备份有效，但与当前现场不同。没有替换当前工作，也没有把新修改标为已备份。');
 }finally{studioState.busy=false;event.target.value='';}
}));
// Legacy dirty flags can be cleared by partial JSON exports. The cross-area
// fingerprint is independent and only a verified full checkpoint can clear it.
window.addEventListener('beforeunload',event=>{if(studioNeedsBackup()){event.preventDefault();event.returnValue='';}});
for(const type of ['input','change','click'])document.addEventListener(type,()=>{queueMicrotask(studioChanged);});
window.ManjuStudio={state:studioState,view:studioView,normalize:normalizeStudio,build:buildStudio,read:readStudio,requiredMedia:studioMedia,needsBackup:studioNeedsBackup,changed:studioChanged};
