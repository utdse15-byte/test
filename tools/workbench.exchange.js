// External text is editable data, not an immutable backup or authorization.
const EXCHANGE_SECTIONS=['shot','review','repair','director','settings'];
const EXCHANGE_FIELDS={shot:['shot-id','task','prompt','duration','resolution','ratio','preserve','change','reviewer'],review:['notes'],repair:['repair-start','repair-end','repair-before','repair-after','repair-preserve','repair-change','repair-audio','repair-human'],director:['director-shot','director-preserve','director-change','director-audio'],settings:['return-width','return-height','promotion-resolution']};
const EXCHANGE_LABELS={shot:'镜头',review:'待提交审片说明',repair:'局部返工',director:'导演材料',settings:'规格设置'};
const EXCHANGE_STATUS={unchanged:'外部未改，保留本地',already_applied:'已是同文',ready:'可带回',conflict:'双方都改了，默认保留本地',context_changed:'对应镜头/素材已变，禁止套用'};
const exchangeState={busy:false,edit:null,pending:null,undo:null,serial:0,lastReport:null};
function exchangeLimit(key){const [area,...rest]=key.split('/'),field=rest.join('/');if(EXCHANGE_FIELDS[area]?.includes(field))return area==='review'?6000:30000;if(/^director\/anchors\/A[0-9]{2}\/target$/.test(key))return 4000;throw new Error('未知或只读字段：'+key);}
function exchangeText(key,text){require(typeof text==='string'&&text.length<=exchangeLimit(key)&&!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(text),'交换文字超长、类型不正确或 Unicode 不完整：'+key);}
function exchangeForm(doc,area){return area==='shot'?doc.workspace.draft.form:area==='review'?doc.workspace.review_form:area==='settings'?doc.auxiliary_form:doc[area].form;}
async function exchangeFields(doc){
 const w=doc.workspace,d=w.draft,shot={shot_id:d.form['shot-id'],assets:d.assets,origin:d.sourceContext?.source_plan_sha256??null};
 const scopes={shot,review:{session:w.review?.session?.session_sha256??null,pending:w.pending.map(c=>c.sha256).sort()},repair:{request:doc.repair.request===null?null:await hash(doc.repair.request),source:doc.repair.source?.sha256??null},director:{shot_id:doc.director.form['director-shot'],source:doc.director.source?.sha256??null},settings:shot},values={},contexts={};
 for(const[area,keys]of Object.entries(EXCHANGE_FIELDS)){const context=await hash(scopes[area]);for(const field of keys){const key=area+'/'+field,text=exchangeForm(doc,area)[field];exchangeText(key,text);values[key]=text;contexts[key]=context;}}
 for(const a of doc.director.anchors){const key='director/anchors/'+a.id+'/target';exchangeText(key,a.target);require(!Object.hasOwn(values,key),'重复锚点');values[key]=a.target;contexts[key]=await hash({...scopes.director,anchor:a.id,time:a.source_time_ms,frame:a.frame.sha256,guide:a.guide?.sha256??null});}
 return {values,contexts};
}
async function normalizeExchange(value){
 exactKeys(value,['schema_id','sections','base','values','contexts','base_sha256','transfers_approval','automatic_execution'],'外部改稿');require(value.schema_id==='manju.external-edit/v1'&&value.transfers_approval===false&&value.automatic_execution===false,'外部改稿版本或人工边界不符');
 const sections=flexSelection(value.sections,EXCHANGE_SECTIONS);require(canonical(sections)===canonical(value.sections),'工作区顺序无效');
 onlyKeys(value.base,Object.keys(value.base||{}),'原文');const keys=Object.keys(value.base).sort();require(keys.length>0&&keys.length<=41,'外部改稿字段数量无效');exactKeys(value.values,keys,'外部文字');exactKeys(value.contexts,keys,'上下文');
 for(const key of keys){require(sections.includes(key.split('/')[0]),'字段不在声明的区域');exchangeText(key,value.base[key]);exchangeText(key,value.values[key]);require(hashPattern.test(value.contexts[key]),'上下文哈希无效');}
 require(await hash({sections,base:value.base,contexts:value.contexts})===value.base_sha256,'导出基线被改动：只能修改 values，不要修改 base/contexts/字段名');require(encoder.encode(canonical(value)).length<=2*1024*1024,'外部改稿超过 2 MiB');return clone(value);
}
async function makeExchange(doc,sections=EXCHANGE_SECTIONS){
 sections=flexSelection(sections,EXCHANGE_SECTIONS);const current=await exchangeFields(doc),base={},contexts={};
 for(const key of Object.keys(current.values).sort())if(sections.includes(key.split('/')[0])){base[key]=current.values[key];contexts[key]=current.contexts[key];}
 return normalizeExchange({schema_id:'manju.external-edit/v1',sections,base,values:clone(base),contexts,base_sha256:await hash({sections,base,contexts}),transfers_approval:false,automatic_execution:false});
}
async function previewExchange(doc,edit){
 edit=await normalizeExchange(edit);const current=await exchangeFields(doc),rows=[];
 for(const key of Object.keys(edit.base).sort()){const base=edit.base[key],value=edit.values[key],local=current.values[key]??null;let state;
  if(value===base)state='unchanged';else if(!Object.hasOwn(current.values,key)||current.contexts[key]!==edit.contexts[key])state='context_changed';else if(value===local)state='already_applied';else if(local===base)state='ready';else state='conflict';
  rows.push({key,base,local,external:value,status:state});}
 return {schema_id:'manju.external-edit-preview/v1',edit_sha256:await hash(edit),current_sha256:await hash(current),rows,transfers_approval:false,automatic_execution:false};
}
function exchangeSet(view,key,text){const match=key.match(/^director\/anchors\/(A[0-9]{2})\/target$/);if(match){const a=view.director.anchors.find(a=>a.id===match[1]);require(a,'对应锚点已经不存在');a.target=text;}else{const[area,...parts]=key.split('/');exchangeForm(view,area)[parts.join('/')]=text;}}
function exchangeDOMField(key){const[area,field]=key.split('/');return area==='review'?'review-notes':/^director\/anchors\//.test(key)?null:field;}
function assertExchangeRepresentable(key,text){
 const id=exchangeDOMField(key),field=id?$(id).cloneNode(true):document.createElement('textarea');
 if(field.tagName==='SELECT'&&![...field.options].some(o=>o.value===text)){const op=element('option',text);op.value=text;field.append(op);}field.value=text;
 require(field.value===text,'字段 '+key+' 不能原样写入浏览器。请修正数字或换行格式；当前工作未改动。');
}
async function applyExchange(doc,edit,take){
 const report=await previewExchange(doc,edit),eligible=new Map(report.rows.filter(r=>['ready','conflict'].includes(r.status)).map(r=>[r.key,r]));require(Array.isArray(take)&&take.length>0&&new Set(take).size===take.length&&take.every(k=>eligible.has(k)),'请选择不重复的可带回或冲突字段；上下文改变的字段不可强行带入');
 const after=clone(doc);for(const key of take)exchangeSet(after,key,eligible.get(key).external);
 if(take.some(k=>k.startsWith('shot/')&&k!=='shot/reviewer')){after.workspace.draft.stage='draft';after.workspace.draft.draftHash=null;after.workspace.draft.sourceContext=null;}
 return {document:after,report:{...report,applied:[...take].sort(),not_applied:report.rows.filter(r=>r.external!==r.base&&!take.includes(r.key)).map(r=>r.key)}};
}
function exchangeTextMembers(edit){return Object.fromEntries(Object.keys(edit.base).sort().map((key,i)=>['text-'+edit.base_sha256.slice(0,16)+'-'+String(i+1).padStart(3,'0')+'.txt',key]));}
async function overlayExchangeTexts(edit,files){
 const out=await normalizeExchange(edit),names=exchangeTextMembers(out);require(files.length>0&&new Set(files.map(f=>f.name)).size===files.length&&files.every(f=>Object.hasOwn(names,f.name))&&files.reduce((n,f)=>n+f.size,0)<=2*1024*1024,'请选择此 EDIT.json 对应且不重名的 text-材料ID-xxx.txt，总计最多 2 MiB');
 for(const file of files){require(file.size<=120003,'文字文件过大');const text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer()).replace(/\r\n?/g,'\n');out.values[names[file.name]]=text;}
 return normalizeExchange(out);
}
function exchangeUses(doc,sections){
 const rows=[],w=doc.workspace;function add(r,usage){if(r)rows.push({sha256:r.sha256,bytes:r.bytes,filename:r.filename||r.path.split('/').at(-1),usage});}
 if(sections.includes('shot'))for(const a of w.draft.assets)add(a,'shot/'+a.role+'/'+a.id);
 if(sections.includes('review')){for(const c of [...w.pending,...(w.review?.session.candidates||[])])add(c,'review/candidate');for(const a of w.review?.session.request.assets||[])add(a,'review/request/'+a.role+'/'+a.id);}
 if(sections.includes('repair'))add(doc.repair.source,'repair/source');
 if(sections.includes('director')){add(doc.director.source,'director/motion-source');for(const a of doc.director.anchors)for(const kind of ['frame','guide'])add(a[kind],'director/'+a.id+'/'+kind);}
 return rows;
}
function exchangeMediaIndex(doc,sections,includeMedia){
 sections=flexSelection(sections,EXCHANGE_SECTIONS);const map=new Map();for(const r of exchangeUses(doc,sections)){
  if(!map.has(r.sha256)){safePath(r.filename);require(!r.filename.includes('/'),'素材必须有可移植文件名');let ext=extension(r.filename);require(!['.ttf','.otf','.ttc','.woff','.woff2'].includes(ext),'不分发字体文件');if(![...IMAGE_EXTS,...VIDEO_EXTS,...AUDIO_EXTS].includes(ext))ext='.bin';map.set(r.sha256,{sha256:r.sha256,bytes:r.bytes,path:'media/'+r.sha256+ext,original_names:[],uses:[],included:includeMedia});}
  const m=map.get(r.sha256);require(m.bytes===r.bytes&&r.bytes>0&&r.bytes<=MAX_FILE,'素材大小冲突或超限');if(!m.original_names.includes(r.filename))m.original_names.push(r.filename);if(!m.uses.includes(r.usage))m.uses.push(r.usage);}
 require([...map.values()].reduce((n,r)=>n+r.bytes,0)<=MAX_TOTAL,'所选素材超过 512 MiB');return {schema_id:'manju.external-media-map/v1',sections,files:[...map.keys()].sort().map(h=>map.get(h)),media_modified:false,transfers_approval:false,automatic_execution:false};
}
function exchangeReadme(edit,index){
 const rows=['# Manju 外部编辑包 / External editing kit','','这不是备份或生成授权。解压后可直接打开 media/ 原素材；没有转码、缩图或上传。','编辑 EDIT.json 的 values，保留 base、contexts、base_sha256 和字段名。','也可编辑 texts/ 内的 UTF-8 文本。回到第09区先加载原 EDIT.json，再选择改过的 text-材料ID-xxx.txt。','两种方式选一种即可；加载文本文件会更新对应 values。CRLF/CR 显式转换为 LF，空格保留。','只导回 EDIT.json 或选中的文本，不重打包 ZIP。说明、媒体映射和历史批准不作为可执行输入。','导入先逐字段三方比较。冲突默认保留本地，换底片/关键帧的旧修改拒绝带入。','外部媒体结果请通过原素材/候选/目标图入口显式绑定；不会根据文件名替换原片。','JSON 的哈希不证明作者身份或版权。分享前自行检查隐私文字、素材权利和文件名。','','## 文字文件与字段'];
 for(const[name,key]of Object.entries(exchangeTextMembers(edit)))rows.push(`- texts/${name}: ${key}`);rows.push('','## 素材','包含实际字节：'+(index.files.some(r=>r.included)?'是':'否'),'MEDIA_MAP.json 记录所有用途、原文件名与 SHA-256。同一内容在多个区域使用时只复制一次。','');return rows.join('\n');
}
async function buildExchangeKit(doc,bindings,sections,includeMedia){
 const edit=await makeExchange(doc,sections),index=exchangeMediaIndex(doc,edit.sections,includeMedia),entries=[];
 const push=(name,data)=>entries.push({name,data,crc:crc32(data)});
 push('EDIT.json',encoder.encode(canonical(edit)));push('MEDIA_MAP.json',encoder.encode(canonical(index)));push('README.md',encoder.encode(exchangeReadme(edit,index)));
 for(const[name,key]of Object.entries(exchangeTextMembers(edit)))push('texts/'+name,encoder.encode(edit.values[key]));
 for(const r of index.files)if(r.included){const found=bindings.get(r.sha256);require(found,'缺少实际文件 '+r.sha256.slice(0,12)+'；可先导出纯文字 JSON');const checked=await inspectFile(found.file);require(checked.sha256===r.sha256&&checked.file.size===r.bytes,'素材发生变化，未导出不完整材料');entries.push({name:r.path,data:checked.file,crc:checked.crc});}
 return {edit,index,blob:zipStore(entries)};
}
function exchangeNotice(text,error=false){$('exchange-status').textContent=text;$('exchange-status').classList.toggle('reason',error);}
function exchangeClearPreview(){exchangeState.pending=null;exchangeState.serial++;$('exchange-preview-box').hidden=true;$('exchange-confirmed').checked=false;$('exchange-apply').disabled=true;}
function exchangeChanged(){
 $('exchange-save-returned').disabled=!exchangeState.edit||exchangeState.busy;
 if(exchangeState.pending&&exchangeState.pending.guard!==studioGuard()){exchangeClearPreview();exchangeNotice('当前工作已变化，旧外部修改预览失效。请重新预览，未覆盖新输入。');}
 const allowed=Boolean(exchangeState.undo&&exchangeState.undo.guard===studioGuard());$('exchange-undo-confirmed').disabled=!allowed;if(!allowed)$('exchange-undo-confirmed').checked=false;$('exchange-undo').disabled=!allowed||!$('exchange-undo-confirmed').checked;
 const selected=[...$('exchange-rows').querySelectorAll('input[data-key]:checked')].length;
 $('exchange-apply').disabled=!(exchangeState.pending&&$('exchange-confirmed').checked&&selected>0);
}
function exchangeHandled(fn){return handled(async event=>{try{return await fn(event);}catch(error){exchangeNotice('未完成：'+String(error.message||error),true);throw error;}finally{exchangeChanged();}});}
function exchangeSelection(){return flexSelection(EXCHANGE_SECTIONS.filter(k=>$('exchange-'+k).checked),EXCHANGE_SECTIONS);}
function exchangeApplyView(view,take){
 const before=studioView();const get=(v,key)=>{const a=key.match(/^director\/anchors\/(A[0-9]{2})\/target$/);if(a)return v.director.anchors.find(x=>x.id===a[1]).target;const[area,...rest]=key.split('/');return exchangeForm(v,area)[rest.join('/')];};
 // Preflight every DOM setter before the first live write. No awaits below.
 for(const key of take)assertExchangeRepresentable(key,get(view,key));
 for(const key of take){const value=get(view,key),id=exchangeDOMField(key);if(id)chooseValue(id,value);else directorState.anchors.find(a=>key==='director/anchors/'+a.id+'/target').target=value;}
 const draft=view.workspace.draft;state.stage=draft.stage;state.draftHash=draft.draftHash;state.sourceContext=clone(draft.sourceContext);
 showContext();invalidate();if(take.some(k=>k.startsWith('repair/')))repairChanged();if(take.some(k=>k.startsWith('director/'))){directorChanged();directorRender();}
 if(take.includes('review/notes'))reviewState.formRevision++;
 for(const id of ['human-confirmed','ack-warnings','review-confirmed','promotion-confirmed','repair-confirmed','catalog-confirmed','studio-restore-confirmed'])$(id).checked=false;
 $('quality-only').checked=true;$('quality-summary').textContent='外部文字应用后仍为质量优先；重新检查，不沿用旧批准。';$('stage-status').textContent=state.stage==='final'?'镜头意图未变，定稿仍需重新核对。':'当前阶段：草稿。外部改稿不授权生成或选片。';
 $('return-results').replaceChildren();catalogRevision.pending=null;catalogRevision.report=null;catalogRevision.baseline=null;catalogRevision.form=null;catalogRevision.serial++;$('catalog-preview').hidden=true;$('apply-catalog').disabled=true;
 studioClearPending();flexClearPreview();return before;
}
$('exchange-export-json').addEventListener('click',exchangeHandled(async()=>{
 require(!studioBusy(),'其他文件操作仍在进行');const guard=studioGuard(),doc=studioView(),sections=exchangeSelection();exchangeState.busy=true;
 try{const edit=await makeExchange(doc,sections);require(guard===studioGuard()&&activeUIOperations<=1,'读取期间工作改变，请重试');require(jsonBytes(edit).length<=2*1024*1024,'排版后的 JSON 超过 2 MiB；请选择较小范围');download(new Blob([jsonBytes(edit)],{type:'application/json'}),'MANJU_EDIT_'+edit.base_sha256.slice(0,12)+'.json');exchangeNotice('已发起可编辑 JSON 下载。只修改 values，再加载预览；没有媒体字节，不是备份。');}finally{exchangeState.busy=false;}
}));
$('exchange-export-kit').addEventListener('click',exchangeHandled(async()=>{
 require(!studioBusy(),'其他文件操作仍在进行');const guard=studioGuard(),doc=studioView(),sections=exchangeSelection(),includeMedia=$('exchange-media').checked;exchangeState.busy=true;
 try{const result=await buildExchangeKit(doc,studioBindings(),sections,includeMedia);require(guard===studioGuard()&&canonical(sections)===canonical(exchangeSelection())&&includeMedia===$('exchange-media').checked&&activeUIOperations<=1,'打包期间工作或导出选项改变，未导出过时材料');download(result.blob,'MANJU_EXTERNAL_'+result.edit.base_sha256.slice(0,12)+'.zip');exchangeNotice('已发起外部编辑包下载。'+result.index.files.filter(r=>r.included).length+' 个原字节素材；请到下载列表保存。没有把交换材料当成总备份。');}finally{exchangeState.busy=false;}
}));
$('exchange-import-json').addEventListener('change',exchangeHandled(async event=>{
 const file=event.target.files[0];if(!file)return;require(!studioBusy(),'其他文件操作仍在进行');exchangeState.busy=true;const serial=exchangeState.serial;const previous=exchangeState.edit;const guard=studioGuard();
 try{require(file.size<=2*1024*1024,'JSON 最大 2 MiB');const data=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer()),edit=await normalizeExchange(strictJSON(data));require(serial===exchangeState.serial&&previous===exchangeState.edit&&guard===studioGuard(),'读取期间材料或现场变化，保留原改稿，请重试');exchangeClearPreview();exchangeState.edit=edit;$('exchange-preview').disabled=false;$('exchange-import-texts').disabled=false;$('exchange-import-status').textContent=`已加载 ${file.name}，${Object.keys(edit.base).length} 个字段。未改变工作；可以再选择修改过的 TXT，或直接预览 JSON 中的 values。`;}
 catch(error){exchangeClearPreview();$('exchange-import-status').textContent=exchangeState.edit?'新材料未通过核验；保留上一份有效改稿，可另存或重新预览。当前工作未改变。':'新材料未通过核验；尚无有效改稿，当前工作未改变。';throw error;}
 finally{exchangeState.busy=false;event.target.value='';}
}));
$('exchange-import-texts').addEventListener('change',exchangeHandled(async event=>{
 const files=Array.from(event.target.files);if(!files.length)return;require(!studioBusy()&&exchangeState.edit,'先加载对应的 EDIT.json');exchangeState.busy=true;exchangeClearPreview();const serial=exchangeState.serial;
 try{const edit=await overlayExchangeTexts(exchangeState.edit,files);require(serial===exchangeState.serial,'读取期间材料变化，请重试');exchangeState.edit=edit;$('exchange-import-status').textContent=`已载入 ${files.length} 个外部 TXT，明确将 CRLF/CR 转为 LF；其他空白保留。尚未应用，点击预览返回修改。`;}
 finally{exchangeState.busy=false;event.target.value='';}
}));
$('exchange-save-returned').addEventListener('click',exchangeHandled(async()=>{
 require(!studioBusy()&&exchangeState.edit,'没有可保存的外部改稿');const edit=clone(exchangeState.edit),serial=exchangeState.serial;exchangeState.busy=true;
 try{const checked=await normalizeExchange(edit);require(serial===exchangeState.serial,'材料读取期间改变，请重试');require(jsonBytes(checked).length<=2*1024*1024,'排版后的 JSON 超过 2 MiB');download(new Blob([jsonBytes(checked)],{type:'application/json'}),'MANJU_RETURNED_EDIT_'+checked.base_sha256.slice(0,12)+'.json');exchangeNotice('已发起外部改稿 JSON 下载，包含尚未应用的 TXT/JSON 修改。当前三个工作区没有改变，也没有新批准。');}
 finally{exchangeState.busy=false;}
}));
$('exchange-preview').addEventListener('click',exchangeHandled(async()=>{
 require(!studioBusy()&&exchangeState.edit,'先加载 EDIT.json');exchangeClearPreview();exchangeState.busy=true;const guard=studioGuard(),view=studioView(),serial=exchangeState.serial,edit=clone(exchangeState.edit);
 try{const report=await previewExchange(view,edit);require(guard===studioGuard()&&serial===exchangeState.serial&&activeUIOperations<=1,'预览期间工作变化，请重新预览');
 exchangeState.pending={guard,before:view,edit,report};exchangeState.lastReport=report;const changed=report.rows.filter(r=>r.external!==r.base);$('exchange-summary').textContent=`外部改动 ${changed.length} 项；可带回 ${changed.filter(r=>r.status==='ready').length}；冲突 ${changed.filter(r=>r.status==='conflict').length}；上下文改变 ${changed.filter(r=>r.status==='context_changed').length}。未改字段会保留本地新内容。`;$('exchange-rows').replaceChildren();
 for(const row of changed){const box=element('div',undefined,'exchange-row'),label=element('label',undefined,'checkline'),check=document.createElement('input');check.type='checkbox';check.dataset.key=row.key;check.checked=row.status==='ready';check.disabled=!['ready','conflict'].includes(row.status);check.setAttribute('aria-label','带回 '+row.key);check.addEventListener('change',()=>{$('exchange-confirmed').checked=false;exchangeChanged();});label.append(check,element('strong',row.key+' · '+EXCHANGE_STATUS[row.status]));box.append(label);const cols=element('div',undefined,'exchange-columns');for(const[key,title]of [['base','导出原文'],['local','当前本地'],['external','外部修改']]){const col=element('div');col.append(element('small',title),element('pre',row[key]===null?'（对应字段不存在）':row[key]));cols.append(col);}box.append(cols);$('exchange-rows').append(box);}
 $('exchange-preview-box').hidden=false;$('exchange-confirmed').checked=false;$('exchange-report').disabled=false;exchangeNotice('预览完成，尚未应用。冲突默认不勾选，可逐字段决定；内容已按纯文本显示。');}
 finally{exchangeState.busy=false;}
}));
$('exchange-confirmed').addEventListener('change',exchangeChanged);
$('exchange-cancel').addEventListener('click',()=>{exchangeClearPreview();exchangeNotice('预览已取消，当前工作保持不变。');});
$('exchange-clear').addEventListener('click',()=>{exchangeClearPreview();exchangeState.edit=null;$('exchange-preview').disabled=true;$('exchange-import-texts').disabled=true;$('exchange-import-status').textContent='已释放外部材料。当前工作和磁盘文件不变。';});
$('exchange-apply').addEventListener('click',exchangeHandled(async()=>{
 require(!studioBusy()&&exchangeState.pending&&$('exchange-confirmed').checked,'没有已确认的修改预览');const p=exchangeState.pending,take=[...$('exchange-rows').querySelectorAll('input[data-key]:checked')].map(e=>e.dataset.key),serial=exchangeState.serial;require(p.guard===studioGuard(),'预览已过时');exchangeState.busy=true;
 try{const result=await applyExchange(p.before,p.edit,take);require(p.guard===studioGuard()&&serial===exchangeState.serial&&activeUIOperations<=1&&$('exchange-confirmed').checked&&canonical(take)===canonical([...$('exchange-rows').querySelectorAll('input[data-key]:checked')].map(e=>e.dataset.key)),'确认期间现场或选择变化，未覆盖新工作');exchangeApplyView(result.document,take);exchangeState.lastReport=result.report;exchangeClearPreview();exchangeState.undo={before:p.before,take,guard:studioGuard()};$('exchange-report').disabled=false;exchangeNotice(`已带回 ${take.length} 个字段；${result.report.not_applied.length} 个外部改动未应用。原素材与旧决定不变。请另存总备份；应用记录可下载。`);}
 finally{exchangeState.busy=false;}
}));
$('exchange-report').addEventListener('click',exchangeHandled(async()=>{require(exchangeState.lastReport,'没有比较记录');download(new Blob([jsonBytes(exchangeState.lastReport)],{type:'application/json'}),'MANJU_EXTERNAL_COMPARISON.json');}));
$('exchange-undo-confirmed').addEventListener('change',exchangeChanged);
$('exchange-undo').addEventListener('click',exchangeHandled(()=>{require(!studioBusy()&&exchangeState.undo&&$('exchange-undo-confirmed').checked,'请确认撤回');const saved=exchangeState.undo;require(saved.guard===studioGuard(),'继续编辑后不能用旧撤回点覆盖');exchangeApplyView(saved.before,saved.take);exchangeState.undo=null;exchangeClearPreview();exchangeNotice('已恢复本次外部文字应用前的现场，当前确认仍需重新核对；磁盘文件未改变。');}));
for(const type of ['input','change','click'])document.addEventListener(type,()=>queueMicrotask(exchangeChanged));
window.ManjuExchange={state:exchangeState,isBusy:()=>exchangeState.busy,changed:exchangeChanged,fields:exchangeFields,make:makeExchange,normalize:normalizeExchange,preview:previewExchange,apply:applyExchange,textMembers:exchangeTextMembers,overlayTexts:overlayExchangeTexts,mediaIndex:exchangeMediaIndex,buildKit:buildExchangeKit};
