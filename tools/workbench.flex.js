// Data-only customization. Compositions keep Studio/v1 and immutable source ZIPs.
const FLEX_SECTIONS=['shot','repair','director','catalog'];
const FLEX_LABELS={shot:'镜头与审片',repair:'局部返工',director:'导演材料',catalog:'能力档'};
const FLEX_FIELD_LABELS={'shot-id':'镜头名',task:'任务类型',prompt:'提示词',duration:'时长',resolution:'分辨率',ratio:'画幅',preserve:'必须保留',change:'允许改变',reviewer:'确认人','repair-start':'返工开始','repair-end':'返工结束','repair-before':'前文长度','repair-after':'后文长度','repair-preserve':'必须保留','repair-change':'允许改变','repair-audio':'声音要求','repair-human':'提出人','director-shot':'导演镜头名','director-preserve':'必须保留','director-change':'允许改变','director-audio':'声音要求'};
const TEMPLATE_FIELDS={shot:['task','prompt','duration','resolution','ratio','preserve','change'],repair:['repair-preserve','repair-change','repair-audio'],director:['director-preserve','director-change','director-audio']};
const flexState={busy:false,donor:null,template:null,pending:null,undo:null,serial:0};
function flexSelection(values,allowed=FLEX_SECTIONS){
 require(Array.isArray(values)&&values.length>0&&values.every(v=>typeof v==='string'&&allowed.includes(v))&&new Set(values).size===values.length,'请选择不重复的有效工作区');
 return allowed.filter(k=>values.includes(k));
}
function flexForm(doc,area){return area==='shot'?doc.workspace.draft.form:doc[area].form;}
async function flexCompose(target,donor,take){
 take=flexSelection(take);const a=await normalizeStudio(clone(target)),b=await normalizeStudio(clone(donor)),out=clone(a);
 if(take.includes('shot')){const cat=out.workspace.catalog;out.workspace=clone(b.workspace);out.workspace.catalog=cat;out.auxiliary_form=clone(b.auxiliary_form);}
 for(const area of ['repair','director'])if(take.includes(area))out[area]=clone(b[area]);
 if(take.includes('catalog'))out.workspace.catalog=clone(b.workspace.catalog);
 const bound=new Map([...b.media,...a.media].map(m=>[m.sha256,m])),wb=new Map(out.workspace.media.map(m=>[m.sha256,m])),effective=new Map([...bound,...wb]);
 out.workspace.media=[...requiredWorkspaceMedia(out.workspace).keys()].map(h=>clone(wb.get(h)));
 for(const source of [out.repair.source,out.director.source])if(source){require(bound.has(source.sha256),'缺少底片绑定');effective.set(source.sha256,{...bound.get(source.sha256),filename:source.filename});}
 for(const anchor of out.director.anchors)for(const raster of [anchor.frame,anchor.guide])if(raster){require(bound.has(raster.sha256),'缺少图像绑定');effective.set(raster.sha256,{...bound.get(raster.sha256),filename:raster.filename,mime_type:'image/png'});}
 out.media=[...studioMedia(out)].map(([h,size])=>{const m=effective.get(h);require(m&&m.bytes===size,'组合后的素材大小冲突或文件缺失');return clone(m);});
 return normalizeStudio(out);
}
function normalizeTemplate(value){
 exactKeys(value,['schema_id','name','notes','fields','quality_only_on_apply','contains_media','contains_approvals','automatic_execution'],'个人模板');
 require(value.schema_id==='manju.creative-template/v1'&&value.quality_only_on_apply===true&&value.contains_media===false&&value.contains_approvals===false&&value.automatic_execution===false,'模板版本或安全边界无效');
 require(typeof value.name==='string'&&value.name.length<=120&&value.name.trim().length>0&&!/[\x00-\x1f]/.test(value.name),'模板名称为空、过长或含控制字符');
 require(typeof value.notes==='string'&&value.notes.length<=4000,'模板说明过长或无效');
 onlyKeys(value.fields,Object.keys(TEMPLATE_FIELDS),'模板工作区');require(Object.keys(value.fields).length>0,'模板没有可复用的字段');
 for(const[area,fields]of Object.entries(value.fields)){
  onlyKeys(fields,TEMPLATE_FIELDS[area],'模板字段');require(Object.keys(fields).length>0,'模板区域为空');
  for(const text of Object.values(fields))require(typeof text==='string'&&text.length<=30000,'模板字段必须是有界文字，不能包含脚本对象');
 }
 require(jsonBytes(value).length<=2*1024*1024,'模板超过 2 MiB');return clone(value);
}
function makeTemplate(doc,name,notes='',areas=Object.keys(TEMPLATE_FIELDS),omitBlank=true){
 const fields={};for(const area of flexSelection(areas,Object.keys(TEMPLATE_FIELDS))){
  const form=flexForm(doc,area),selected={};for(const key of TEMPLATE_FIELDS[area]){require(typeof form[key]==='string','模板源字段不是文字');if(!omitBlank||form[key].trim())selected[key]=form[key];}
  if(Object.keys(selected).length)fields[area]=selected;
 }
 return normalizeTemplate({schema_id:'manju.creative-template/v1',name,notes,fields,quality_only_on_apply:true,contains_media:false,contains_approvals:false,automatic_execution:false});
}
function templateDraftView(view,template,mode='fill_empty'){
 require(['fill_empty','replace'].includes(mode),'未知模板应用方式');const out=clone(view),t=normalizeTemplate(template);let shotChanged=false;
 for(const[area,fields]of Object.entries(t.fields)){
  const form=flexForm(out,area);for(const[key,text]of Object.entries(fields)){require(typeof form[key]==='string','当前表单字段无效');if(mode==='replace'||!form[key].trim()){if(area==='shot'&&form[key]!==text)shotChanged=true;form[key]=text;}}
 }
 if(shotChanged){out.workspace.draft.stage='draft';out.workspace.draft.draftHash=null;out.workspace.draft.sourceContext=null;}
 return out;
}
async function applyCreativeTemplate(doc,template,mode='fill_empty'){
 return normalizeStudio(templateDraftView(await normalizeStudio(clone(doc)),template,mode));
}
async function templateViewDifference(before,after){
 const ap=flexParts(before),bp=flexParts(after),changes=[];
 for(const area of Object.keys(TEMPLATE_FIELDS)){const old=flexForm(before,area),fresh=flexForm(after,area);for(const key of Object.keys(old).sort())if(old[key]!==fresh[key])changes.push({area,field:key,before:old[key],after:fresh[key]});}
 return {schema_id:'manju.template-view-preview/v1',before_sha256:await hash(before),after_sha256:await hash(after),changed_sections:FLEX_SECTIONS.filter(k=>canonical(ap[k])!==canonical(bp[k])),retained_sections:FLEX_SECTIONS.filter(k=>canonical(ap[k])===canonical(bp[k])),field_changes:changes,added_media:[],removed_media:[],result_media_files:studioMedia(after).size,result_media_bytes:[...studioMedia(after).values()].reduce((n,x)=>n+x,0),metadata_only:true};
}
function assertTemplateViewRepresentable(view){
 // Validate all setters before the first live write. Browser number inputs and
 // textarea line endings can sanitize imported text; never hide that loss.
 for(const area of Object.keys(TEMPLATE_FIELDS))for(const key of TEMPLATE_FIELDS[area]){
  const text=flexForm(view,area)[key],field=$(key).cloneNode(true);
  if(field.tagName==='SELECT'&&![...field.options].some(o=>o.value===text)){const option=document.createElement('option');option.value=text;option.textContent=text;field.append(option);}
  field.value=text;
  require(field.value===text,`模板字段“${FLEX_FIELD_LABELS[key]||key}”无法原样写入浏览器。请修正数字或换行格式后重新预览；当前工作未改动。`);
 }
}
function applyTemplateView(view){
 assertTemplateViewRepresentable(view);
 // Text-only transaction: no file rebinding or invented complete archive. This
 // works even when a browser has restored draft records but not media bytes.
 for(const area of Object.keys(TEMPLATE_FIELDS))for(const key of TEMPLATE_FIELDS[area])chooseValue(key,flexForm(view,area)[key]);
 const draft=view.workspace.draft;state.stage=draft.stage;state.draftHash=draft.draftHash;state.sourceContext=clone(draft.sourceContext);
 showContext();invalidate();repairChanged();directorChanged();
 $('stage-status').textContent=state.stage==='final'?'当前阶段：定稿；任务未改动，仍需重新核对。':'当前阶段：草稿。模板不是批准，请重新检查型号约束。';
 $('return-results').replaceChildren();$('return-raster-note').textContent='文字设置已变化，旧规格报告未沿用。';
 catalogRevision.pending=null;catalogRevision.report=null;catalogRevision.baseline=null;catalogRevision.form=null;catalogRevision.serial++;$('catalog-preview').hidden=true;$('apply-catalog').disabled=true;
 for(const id of ['human-confirmed','ack-warnings','review-confirmed','promotion-confirmed','repair-confirmed','catalog-confirmed','studio-restore-confirmed'])$(id).checked=false;
 $('quality-only').checked=true;$('quality-summary').textContent='模板应用后仍为质量优先；重新检查任务，不沿用旧型号选择。';
}

function flexParts(doc){const w=clone(doc.workspace);delete w.catalog;delete w.media;return {shot:{workspace:w,auxiliary_form:doc.auxiliary_form},repair:doc.repair,director:doc.director,catalog:doc.workspace.catalog};}
async function studioDifference(before,after){
 const a=await normalizeStudio(clone(before)),b=await normalizeStudio(clone(after)),ap=flexParts(a),bp=flexParts(b),changes=[];
 for(const area of Object.keys(TEMPLATE_FIELDS)){const old=flexForm(a,area),fresh=flexForm(b,area);for(const key of Object.keys(old).sort())if(old[key]!==fresh[key])changes.push({area,field:key,before:old[key],after:fresh[key]});}
 const am=new Set(a.media.map(m=>m.sha256)),bm=new Set(b.media.map(m=>m.sha256));
 return {schema_id:'manju.studio-difference/v1',before_sha256:await hash(a),after_sha256:await hash(b),changed_sections:FLEX_SECTIONS.filter(k=>canonical(ap[k])!==canonical(bp[k])),retained_sections:FLEX_SECTIONS.filter(k=>canonical(ap[k])===canonical(bp[k])),field_changes:changes,added_media:[...bm].filter(h=>!am.has(h)).sort(),removed_media:[...am].filter(h=>!bm.has(h)).sort(),result_media_files:bm.size,result_media_bytes:b.media.reduce((n,m)=>n+m.bytes,0),quality_only_on_apply:true,confirmations_restored:false,automatic_execution:false,project_modified:false};
}
function flexNotice(text,error=false){$('flex-status').textContent=text;$('flex-status').classList.toggle('reason',error);}
function flexConfig(){return canonical({take:FLEX_SECTIONS.filter(k=>$('flex-take-'+k).checked),mode:$('flex-template-mode').value});}
function flexClearPreview(){flexState.pending=null;flexState.serial++;$('flex-preview').hidden=true;$('flex-confirmed').checked=false;$('flex-apply').disabled=true;}
function flexChanged(){
 if(flexState.pending&&(flexState.pending.guard!==studioGuard()||flexState.pending.config!==flexConfig())){flexClearPreview();flexNotice('预览后工作或取用选项已变化，旧预览失效；请重新预览，当前工作未被覆盖。');}
 const allowed=Boolean(flexState.undo&&flexState.undo.guard===studioGuard());
 $('flex-undo-confirmed').disabled=!allowed;
 if(!allowed)$('flex-undo-confirmed').checked=false;
 $('flex-undo').disabled=!allowed||!$('flex-undo-confirmed').checked;
}
function flexHandled(fn){return handled(async event=>{
 try{return await fn(event);}catch(error){flexNotice('未完成：'+String(error.message||error),true);throw error;}finally{flexChanged();}
});}
async function flexCapture(){const built=await buildStudio();return {document:built.document,bindings:studioBindings(),guard:built.guard,verified:studioState.verified};}
function flexShowPreview(before,after,bindings,report,title,guard,config,kind='composition'){
 require(guard===studioGuard()&&config===flexConfig()&&activeUIOperations<=1,'预览期间发生变化，请重试');
 flexState.pending={before,after:{document:after,bindings},report,guard,config,kind};
 const names=keys=>keys.map(k=>FLEX_LABELS[k]).join('、')||'无';
 $('flex-summary').textContent=`${title}${report.metadata_only?'（仅文字预览，不重读或要求绑定媒体）':''}\n将改变：${names(report.changed_sections)}\n保持原样：${names(report.retained_sections)}\n素材：新增 ${report.added_media.length}，不再引用 ${report.removed_media.length}，结果 ${report.result_media_files} 个 / ${report.result_media_bytes} 字节\n原始磁盘文件不会删除或覆盖。历史审片不跨任务转移；当前型号选择与确认全部清除。\n镜头字段变更的模板会退回草稿，不能沿用旧定稿晋升。\n应用前 ${report.before_sha256}\n应用后 ${report.after_sha256}`;
 $('flex-fields').replaceChildren();
 for(const item of report.field_changes){const row=element('div',undefined,'flex-field');const short=s=>s.length>500?s.slice(0,500)+'…（显示截断，原文完整保留）':s;
  row.textContent=`${FLEX_LABELS[item.area]} · ${FLEX_FIELD_LABELS[item.field]||item.field}\n原：${short(item.before)}\n新：${short(item.after)}`;$('flex-fields').append(row);
 }
 $('flex-confirmed').checked=false;$('flex-apply').disabled=true;$('flex-preview').hidden=false;flexNotice('预览已就绪，尚未改动工作。取消可以保留原样。');
}
$('flex-donor-file').addEventListener('change',flexHandled(async event=>{
 const file=event.target.files[0];if(!file)return;require(!studioBusy(),'其他文件操作仍在进行');flexState.busy=true;flexClearPreview();flexState.donor=null;$('flex-preview-compose').disabled=true;$('flex-release-donor').disabled=true;const guard=studioGuard();
 $('flex-donor-status').textContent='正在核验来源的完整文件与媒体…';
 try{const donor=await readStudio(file);require(guard===studioGuard()&&activeUIOperations<=1,'读取来源期间当前工作变化，请重新选择来源');flexState.donor=donor;$('flex-preview-compose').disabled=false;$('flex-release-donor').disabled=false;$('flex-donor-status').textContent=`已核验：${file.name}；镜头 ${donor.document.workspace.draft.form['shot-id']}；${donor.document.media.length} 个去重文件。当前工作未改变。`;}
 catch(error){$('flex-donor-status').textContent='来源未通过核验，没有保留可应用的来源。';throw error;}
 finally{flexState.busy=false;event.target.value='';}
}));
$('flex-release-donor').addEventListener('click',()=>{flexClearPreview();flexState.donor=null;$('flex-preview-compose').disabled=true;$('flex-release-donor').disabled=true;$('flex-donor-status').textContent='已释放来源文件；当前工作与磁盘备份不变。';});
$('flex-preview-compose').addEventListener('click',flexHandled(async()=>{
 require(!studioBusy()&&flexState.donor,'先选择一个有效来源总备份');flexClearPreview();const take=flexSelection(FLEX_SECTIONS.filter(k=>$('flex-take-'+k).checked)),config=flexConfig();flexState.busy=true;
 try{const before=await flexCapture(),donor=flexState.donor,after=await flexCompose(before.document,donor.document,take),bindings=new Map([...donor.bindings,...before.bindings]);
  const report=await studioDifference(before.document,after);flexShowPreview(before,after,bindings,report,'按工作区取用：'+take.map(k=>FLEX_LABELS[k]).join('、'),before.guard,config);
 }finally{flexState.busy=false;}
}));
$('flex-export-template').addEventListener('click',flexHandled(async()=>{
 require(!studioBusy(),'其他文件操作仍在进行');const value=makeTemplate(studioView(),$('flex-template-name').value,$('flex-template-notes').value,Object.keys(TEMPLATE_FIELDS).filter(k=>$('flex-template-'+k).checked),$('flex-template-omit-blank').checked);
 download(new Blob([jsonBytes(value)],{type:'application/json'}),'MANJU_TEMPLATE_'+(await hash(value)).slice(0,12)+'.json');flexNotice('已发起纯文字模板下载。它不是现场备份，不会清除未备份提醒；请确认下载列表。');
}));
$('flex-template-file').addEventListener('change',flexHandled(async event=>{
 const file=event.target.files[0];if(!file)return;require(!studioBusy(),'其他文件操作仍在进行');flexState.busy=true;flexClearPreview();flexState.template=null;$('flex-preview-template').disabled=true;
 try{const value=normalizeTemplate(await parseFile(file));flexState.template=value;$('flex-template-status').textContent=`已核验模板：${value.name}\n${value.notes}\n范围：${Object.keys(value.fields).map(k=>FLEX_LABELS[k]).join('、')}。不含素材、旧批准或型号选择。`;$('flex-preview-template').disabled=false;}
 catch(error){$('flex-template-status').textContent='模板未通过核验，未保留可应用的模板。';throw error;}
 finally{flexState.busy=false;event.target.value='';}
}));
$('flex-preview-template').addEventListener('click',flexHandled(async()=>{
 require(!studioBusy()&&flexState.template,'先核验一个个人模板');flexClearPreview();flexState.busy=true;const mode=$('flex-template-mode').value,config=flexConfig();
 try{const before={document:studioView(),guard:studioGuard(),verified:studioState.verified},after=templateDraftView(before.document,flexState.template,mode);assertTemplateViewRepresentable(after);const report=await templateViewDifference(before.document,after);
  flexShowPreview(before,after,null,report,mode==='fill_empty'?'个人模板：只填空白，已填写字段保留':'个人模板：替换模板明确列出的字段',before.guard,config,'template_fields');
 }finally{flexState.busy=false;}
}));
$('flex-confirmed').addEventListener('change',()=>{$('flex-apply').disabled=!$('flex-confirmed').checked||!flexState.pending||flexState.pending.guard!==studioGuard()||flexState.pending.config!==flexConfig();});
$('flex-cancel').addEventListener('click',()=>{flexClearPreview();flexNotice('已取消预览；当前所有工作保持原样。');});
$('flex-apply').addEventListener('click',flexHandled(()=>{
 require(!studioBusy()&&flexState.pending&&$('flex-confirmed').checked,'没有已确认的可应用预览');const p=flexState.pending;
 require(p.guard===studioGuard()&&p.config===flexConfig(),'预览过时，拒绝覆盖新工作');
 if(p.kind==='template_fields')applyTemplateView(p.after.document);else studioApplyRaw(p.after);studioClearPending();studioState.verified=p.before.verified;flexClearPreview();
 flexState.undo={before:p.before,kind:p.kind,guard:studioGuard()};$('flex-undo-confirmed').checked=false;flexChanged();
 studioNotice('已应用预览中的变更；请另存并核验新的总备份。旧ZIP和未选择的工作区内容没有改变。');flexNotice('变更完成，质量优先开启。可立即撤回一次；继续编辑后不能用旧撤回点覆盖新工作。');
}));
$('flex-undo-confirmed').addEventListener('change',flexChanged);
$('flex-undo').addEventListener('click',flexHandled(()=>{
 require(!studioBusy()&&flexState.undo&&$('flex-undo-confirmed').checked,'先明确确认撤回');const saved=flexState.undo;require(saved.guard===studioGuard(),'应用后已经继续编辑，拒绝覆盖新工作');
 if(saved.kind==='template_fields')applyTemplateView(saved.before.document);else studioApplyRaw(saved.before);studioClearPending();studioState.verified=saved.before.verified;flexState.undo=null;flexClearPreview();
 studioNotice('已撤回到本次变更前现场；当前确认仍需重新核对。');flexNotice('上一次变更已撤回；不读取或改动磁盘原备份。');
}));
$('flex-save-branch').addEventListener('click',flexHandled(async()=>{
 require(!studioBusy(),'其他文件操作仍在进行');const label=$('flex-branch-name').value.trim();require(label&&label.length<=80,'请填写最多80字符的版本名称');const safe=label.replace(/[\x00-\x1f<>:"/\\|?*]/g,'_').replace(/[ .]+$/g,'').slice(0,64);require(safe,'版本名无可用字符');flexState.busy=true;
 try{const result=await buildStudio();require(result.guard===studioGuard(),'保存期间现场变化，请重试');download(result.blob,'MANJU_STUDIO_'+safe+'_'+(await hash(result.document)).slice(0,12)+'.zip');studioNotice('已发起命名总备份下载；请选回实际保存的ZIP核验，尚未确认落盘。');flexNotice('命名副本使用标准 Studio/v1，可供后续整场恢复或挑选工作区；源文件未改动。');}
 finally{flexState.busy=false;}
}));
for(const type of ['input','change','click'])document.addEventListener(type,()=>queueMicrotask(flexChanged));
window.ManjuFlex={state:flexState,isBusy:()=>flexState.busy,changed:flexChanged,compose:flexCompose,normalizeTemplate,makeTemplate,applyTemplate:applyCreativeTemplate,templateDraftView,difference:studioDifference};
