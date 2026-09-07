// Complete offline checkpoints. Imports validate in isolation before replacing UI state.
const WORKSPACE_FORM = ['shot-id','task','prompt','duration','resolution','ratio','preserve','change','reviewer'];
const WORKSPACE_REVIEW_FORM = {candidate:'review-candidate',verdict:'review-verdict',human:'review-human',notes:'review-notes',score:'review-score'};
let workspaceBusy = false;
function exactKeys(value, keys, label) {
 onlyKeys(value, keys, label);
 require(Object.keys(value).length===keys.length, label+' 缺少字段');
}
function requiredWorkspaceMedia(doc) {
 const refs=[...doc.draft.assets,...doc.pending];
 if(doc.review)refs.push(...doc.review.session.request.assets,...doc.review.session.candidates);
 const result=new Map();
 for(const item of refs){require(!result.has(item.sha256)||result.get(item.sha256)===item.bytes,'相同内容哈希的大小声明冲突');result.set(item.sha256,item.bytes);}
 return result;
}
async function normalizeWorkspace(value) {
 exactKeys(value,['schema_id','draft','catalog','review','pending','review_form','reveal','media','confirmations_restored','project_modified'],'工作现场');
 require(value.schema_id==='manju.authoring-workspace/v1'&&value.confirmations_restored===false&&value.project_modified===false,'工作现场版本或人工边界不正确');
 const d=value.draft;
 exactKeys(d,['version','form','assets','stage','draftHash','sourceContext'],'原始草稿');
 require(d.version===1,'未知草稿版本');exactKeys(d.form,WORKSPACE_FORM,'草稿表单');
 for(const text of Object.values(d.form))require(typeof text==='string'&&text.length<=30000,'草稿文本类型或长度不正确');
 require(Array.isArray(d.assets)&&d.assets.length<=32,'草稿素材数量不正确');
 const assets=d.assets.map(normalizeAsset),ids=new Set(),paths=new Map();
 for(const a of assets){require(!ids.has(a.id),'素材编号重复');ids.add(a.id);const key=a.path.normalize('NFC').toLowerCase(),identity=canonical([a.path,a.sha256,a.bytes]);require(!paths.has(key)||paths.get(key)===identity,'素材路径冲突');paths.set(key,identity);}
 require((d.stage==='draft'&&d.draftHash===null)||(d.stage==='final'&&hashPattern.test(d.draftHash)),'草稿阶段或晋升标识无效');
 if(d.sourceContext!==null){const c=d.sourceContext;exactKeys(c,['source_plan','source_plan_sha256','warnings'],'原项目上下文');require(c.source_plan&&typeof c.source_plan==='object'&&!Array.isArray(c.source_plan)&&await hash(c.source_plan)===c.source_plan_sha256,'原项目上下文哈希错误');require(Array.isArray(c.warnings)&&c.warnings.every(x=>typeof x==='string'),'上下文告警格式错误');}
 const cat=normalizeCatalog(value.catalog),review=value.review===null?null:await normalizeReview(value.review);
 require(Array.isArray(value.pending)&&value.pending.length<=12&&(!review||!value.pending.length),'待审候选与固定场次冲突');
 const pending=value.pending.map(candidateValue);require(new Set(pending.map(c=>c.sha256)).size===pending.length,'候选内容重复');
 exactKeys(value.review_form,Object.keys(WORKSPACE_REVIEW_FORM),'审片表单');
 for(const text of Object.values(value.review_form))require(typeof text==='string'&&text.length<=6000,'审片表单文本无效');
 require(typeof value.reveal==='boolean','揭示文件名状态无效');
 require(Array.isArray(value.media)&&value.media.length<=100,'媒体记录数量不正确');
 const media=value.media.map(m=>{exactKeys(m,['sha256','bytes','filename','mime_type'],'媒体绑定');require(hashPattern.test(m.sha256),'媒体哈希无效');integer(m.bytes,1,MAX_FILE,'本地媒体大小');safePath(m.filename);require(!m.filename.includes('/'),'媒体只使用文件名');require(typeof m.mime_type==='string'&&m.mime_type.length<=100&&!/[\x00-\x1f]/.test(m.mime_type),'媒体类型无效');return {...m};});
 const doc={schema_id:value.schema_id,draft:{...clone(d),assets},catalog:cat,review,pending,review_form:clone(value.review_form),reveal:value.reveal,media,confirmations_restored:false,project_modified:false};
 const required=requiredWorkspaceMedia(doc),bound=new Map(media.map(m=>[m.sha256,m.bytes]));
 require(bound.size===media.length&&canonical([...required].sort())===canonical([...bound].sort()),'工作现场必须包含全部且仅被引用的实际素材和候选');
 require([...bound.values()].reduce((n,v)=>n+v,0)<=MAX_TOTAL,'工作现场超过本地 512 MiB 上限');
 require(jsonBytes(doc).length<=2*1024*1024,'工作现场元数据超过 2 MiB');
 return doc;
}
function workspaceView() {
 return {draft:clone(rawDraft()),catalog:clone(catalog),review:clone(reviewState.document),pending:clone(reviewState.pending),
  review_form:Object.fromEntries(Object.entries(WORKSPACE_REVIEW_FORM).map(([k,id])=>[k,$(id).value])),reveal:reviewState.reveal};
}
async function buildWorkspace() {
 const snapshot=workspaceView(),before=canonical(snapshot),bindings=new Map([...state.files,...reviewState.files]);
 const required=requiredWorkspaceMedia(snapshot),media=[],entries=[],manifest={schema_id:'manju.workspace-manifest/v1',files:{}};
 for(const [h,size] of required){
  const found=bindings.get(h);require(found,'尚未绑定实际文件：'+h.slice(0,12)+'。请先重新选择原文件。');
  status('正在备份并核验：'+found.file.name);
  const checked=await inspectFile(found.file);require(checked.sha256===h&&checked.file.size===size,'文件发生变化，拒绝生成不完整备份');
  media.push({sha256:h,bytes:size,filename:checked.file.name,mime_type:checked.file.type});
  const name='media/'+h;entries.push({name,data:checked.file,crc:checked.crc});manifest.files[name]=h;
 }
 const doc=await normalizeWorkspace({schema_id:'manju.authoring-workspace/v1',...snapshot,media,confirmations_restored:false,project_modified:false});
 const metadata=jsonBytes(doc);entries.push({name:'WORKSPACE.json',data:metadata,crc:crc32(metadata)});manifest.files['WORKSPACE.json']=await hash(metadata);
 const inventory=jsonBytes(manifest);entries.push({name:'MANIFEST.json',data:inventory,crc:crc32(inventory)});
 require(before===canonical(workspaceView()),'任务或审片记录在备份期间变化，请重新保存');
 return {document:doc,blob:zipStore(entries)};
}
async function storedZipMembers(blob) {
 require(blob&&blob.size>=22&&blob.size<=MAX_TOTAL+4*1024*1024,'工作现场 ZIP 为空或超过大小上限');
 const end=new DataView(await blob.slice(blob.size-22).arrayBuffer());
 require(end.getUint32(0,true)===0x06054b50&&end.getUint16(4,true)===0&&end.getUint16(6,true)===0&&end.getUint16(20,true)===0,'只读取本工具导出的无注释 ZIP');
 const count=end.getUint16(10,true),length=end.getUint32(12,true),start=end.getUint32(16,true);
 require(count>=2&&count<=128&&count===end.getUint16(8,true)&&length<=128*286&&start+length===blob.size-22,'ZIP 中央目录无效');
 const bytes=new Uint8Array(await blob.slice(start,start+length).arrayBuffer()),view=new DataView(bytes.buffer),decoder=new TextDecoder('utf-8',{fatal:true});
 const entries=new Map();let cursor=0,localEnd=0,total=0;
 for(let i=0;i<count;i++){
  require(cursor+46<=length&&view.getUint32(cursor,true)===0x02014b50,'ZIP 目录损坏');
  const flags=view.getUint16(cursor+8,true),method=view.getUint16(cursor+10,true),crc=view.getUint32(cursor+16,true),packed=view.getUint32(cursor+20,true),size=view.getUint32(cursor+24,true),n=view.getUint16(cursor+28,true),extra=view.getUint16(cursor+30,true),comment=view.getUint16(cursor+32,true),offset=view.getUint32(cursor+42,true);
  require(n>0&&n<=240&&cursor+46+n<=length&&!extra&&!comment&&view.getUint16(cursor+34,true)===0,'ZIP 路径或额外字段无效');
  const name=decoder.decode(bytes.subarray(cursor+46,cursor+46+n));safePath(name);
  require(name==='WORKSPACE.json'||name==='MANIFEST.json'||/^media\/[a-f0-9]{64}$/.test(name),'工作现场包含未允许的路径');
  require(!entries.has(name)&&!(flags&~0x800)&&method===0&&packed===size&&size>0&&size<=(name.endsWith('.json')?2*1024*1024:MAX_FILE),'ZIP 重复、压缩、加密或大小不符合工作现场格式');
  require((view.getUint32(cursor+38,true)>>>16&0xf000)!==0xa000,'不接受链接');
  require(offset===localEnd&&offset+30+n+size<=start,'ZIP 数据重叠、缺口或越界');
  const local=new Uint8Array(await blob.slice(offset,offset+30+n).arrayBuffer()),lv=new DataView(local.buffer);
  require(lv.getUint32(0,true)===0x04034b50&&lv.getUint16(6,true)===flags&&lv.getUint16(8,true)===method&&lv.getUint32(14,true)===crc&&lv.getUint32(18,true)===size&&lv.getUint32(22,true)===size&&lv.getUint16(26,true)===n&&lv.getUint16(28,true)===0&&decoder.decode(local.subarray(30))===name,'ZIP 本地目录与中央目录不一致');
  const data=blob.slice(offset+30+n,offset+30+n+size);entries.set(name,{name,data,crc,size});
  localEnd=offset+30+n+size;cursor+=46+n;total+=size;
 }
 require(cursor===length&&localEnd===start&&total<=MAX_TOTAL+4*1024*1024,'ZIP 未完整闭合或超限');return entries;
}
async function readWorkspace(blob) {
 const entries=await storedZipMembers(blob);
 require(entries.has('WORKSPACE.json')&&entries.has('MANIFEST.json'),'工作现场元数据缺失');
 const parse=async name=>{const e=entries.get(name),bytes=new Uint8Array(await e.data.arrayBuffer());require(crc32(bytes)===e.crc,'元数据 CRC 不符');return strictJSON(new TextDecoder('utf-8',{fatal:true}).decode(bytes));};
 const manifest=await parse('MANIFEST.json');exactKeys(manifest,['schema_id','files'],'备份清单');
 require(manifest.schema_id==='manju.workspace-manifest/v1'&&manifest.files&&typeof manifest.files==='object'&&!Array.isArray(manifest.files),'未知清单');
 require(canonical(Object.keys(manifest.files).sort())===canonical([...entries.keys()].filter(n=>n!=='MANIFEST.json').sort()),'备份清单不完整');
 const doc=await normalizeWorkspace(await parse('WORKSPACE.json')),expected=['WORKSPACE.json','MANIFEST.json',...doc.media.map(m=>'media/'+m.sha256)].sort();
 require(canonical(expected)===canonical([...entries.keys()].sort()),'存在未引用或缺失的文件');
 const bindings=new Map();
 for(const [name,e]of entries){
  if(name==='MANIFEST.json')continue;
  const binding=doc.media.find(m=>'media/'+m.sha256===name);
  const file=new File([e.data],binding?binding.filename:name,{type:binding?binding.mime_type:'application/json'}),checked=await inspectFile(file);
  require(checked.crc===e.crc&&checked.sha256===manifest.files[name],'文件 CRC 或 SHA-256 不符：'+name);
  if(binding){require(binding.bytes===file.size&&binding.sha256===checked.sha256,'媒体身份不符');bindings.set(binding.sha256,checked);}
 }
 return {document:doc,bindings};
}
function applyWorkspace(verified) {
 const {document:doc,bindings}=verified;
 // No awaits after this point: UI state changes as one synchronous transaction.
 state.generation++;
 for(const [id,text]of Object.entries(doc.draft.form))chooseValue(id,text);
 state.assets=clone(doc.draft.assets);state.stage=doc.draft.stage;state.draftHash=doc.draft.draftHash;state.sourceContext=clone(doc.draft.sourceContext);state.files=new Map(bindings);catalog=clone(doc.catalog);
 reviewState.document=clone(doc.review);reviewState.pending=clone(doc.pending);reviewState.files=new Map(bindings);reviewState.reveal=doc.reveal;reviewState.formRevision++;
 reviewChanged();renderReview();
 for(const [key,id]of Object.entries(WORKSPACE_REVIEW_FORM))chooseValue(id,doc.review_form[key]);
 for(const id of ['human-confirmed','ack-warnings','review-confirmed','promotion-confirmed'])$(id).checked=false;
 showContext();renderAssets();invalidate();
 $('stage-status').textContent=state.stage==='final'?'当前阶段：定稿，恢复草稿 '+state.draftHash:'当前阶段：草稿，素材和审片记录已恢复。';
 $('workspace-status').textContent=`已恢复 ${doc.media.length} 个实际文件、${doc.review?.decisions.length||0} 条决定。当前确认已清除；原影片工程没有被改动。`;
 status('完整工作现场已恢复。历史决定仍在，但所选模型和当前确认需要重新核对。');
}
$('export-workspace').addEventListener('click',handled(async()=>{
 require(!workspaceBusy&&!state.busy&&!reviewState.busy,'文件操作仍在进行');workspaceBusy=true;
 try{const result=await buildWorkspace();download(result.blob,'MANJU_WORKSPACE_'+(await hash(result.document)).slice(0,12)+'.zip');$('workspace-status').textContent=`已触发完整备份下载：${result.document.media.length} 个文件。请在下载列表确认保存成功；本页面不能确认磁盘保存。`;status('备份包含当前草稿、能力档、审片记录和实际素材。请保留 ZIP 原件。');}finally{workspaceBusy=false;}
}));
$('import-workspace').addEventListener('change',handled(async event=>{
 const file=event.target.files[0];if(!file)return;
 require(!workspaceBusy&&!state.busy&&!reviewState.busy,'文件操作仍在进行');workspaceBusy=true;
 const before=canonical(workspaceView());
 try{status('正在完整核验备份；当前页面保持不变。');const result=await readWorkspace(file);require(before===canonical(workspaceView()),'核验期间页面已变化，未覆盖当前工作');
  require(confirm(`备份核验通过：${result.document.media.length} 个实际文件、${result.document.review?.decisions.length||0} 条审片决定。替换当前页面工作现场？请先保存当前工作。不会改写磁盘影片工程。`),'保留当前工作现场');applyWorkspace(result);
 }finally{workspaceBusy=false;event.target.value='';}
}));
window.ManjuWorkspace={normalizeWorkspace,requiredWorkspaceMedia,workspaceView,buildWorkspace,readWorkspace,storedZipMembers,applyWorkspace};
