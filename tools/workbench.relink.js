// Restore file bindings only. No import of task text, candidate identities or approvals.
const relinkState={busy:false,pending:null,serial:0};
const relinkIds=new WeakMap();let relinkNext=1;
function relinkFileId(file){if(!file)return null;if(!relinkIds.has(file))relinkIds.set(file,relinkNext++);return relinkIds.get(file);}
function relinkSlots(){
 const slots=[];
 const add=(record,label,get,set,kind='asset')=>{if(record)slots.push({record,label,get,set,kind});};
 for(const a of state.assets)add(a,'镜头 / '+ROLE_NAMES[a.role],()=>state.files.get(a.sha256),e=>state.files.set(a.sha256,e));
 for(const a of reviewState.document?.session.request.assets||[])add(a,'审片原任务 / '+ROLE_NAMES[a.role],()=>reviewState.files.get(a.sha256),e=>reviewState.files.set(a.sha256,e));
 for(const c of [...reviewState.pending,...(reviewState.document?.session.candidates||[])])add(c,'审片候选',()=>reviewState.files.get(c.sha256),e=>reviewState.files.set(c.sha256,e),'video');
 add(repairState.source,'返工原片',()=>repairState.entry,e=>{repairState.entry=e;},'video');
 add(directorState.source,'导演底片',()=>directorState.entry,e=>{directorState.entry=e;},'video');
 for(const a of directorState.anchors)for(const [kind,rec]of [['frames',a.frame],['guides',a.guide]]){
  if(rec){const key=directorRasterName(rec,kind);add(rec,'导演 '+a.id+(kind==='frames'?' / 原帧':' / 目标图'),()=>directorState.files.get(key),e=>directorState.files.set(key,e),'png');}
 }
 return slots;
}
function relinkBound(slot){const e=slot.get();return Boolean(e&&e.sha256===slot.record.sha256&&e.file?.size===slot.record.bytes);}
function relinkGuard(){return canonical({desk:deskGuard(),bindings:relinkSlots().map(s=>[s.label,s.record.sha256,relinkFileId(s.get()?.file)])});}
function relinkNotice(text,error=false){$('relink-status').textContent=text;$('relink-status').classList.toggle('reason',error);}
function relinkClear(){relinkState.pending=null;relinkState.serial++;$('relink-preview').hidden=true;$('relink-confirmed').checked=false;$('relink-apply').disabled=true;}
function relinkInventory(){
 const grouped=new Map();for(const s of relinkSlots()){
  const h=s.record.sha256;require(!grouped.has(h)||grouped.get(h).bytes===s.record.bytes,'同一素材的大小记录冲突，先修正导入记录');
  if(!grouped.has(h))grouped.set(h,{sha256:h,bytes:s.record.bytes,uses:[],names:[],missing:0});
  const r=grouped.get(h);r.uses.push(s.label);r.names.push(s.record.filename||s.record.path);if(!relinkBound(s))r.missing++;
 }
 return [...grouped.values()].map(r=>({...r,names:[...new Set(r.names)],uses:[...new Set(r.uses)]}));
}
function relinkShowInventory(){
 const all=relinkInventory(),missing=all.filter(r=>r.missing),root=$('relink-inventory');root.replaceChildren();
 for(const r of missing){const item=element('div',undefined,'relink-item');item.append(element('strong',r.names.join(' / ')),element('div',r.uses.join('；')),element('code',r.sha256),element('div',r.bytes+' 字节'));root.append(item);}
 if(!missing.length)root.append(element('p',all.length?'当前所有引用已有文件绑定。这里未重新读取或解码全部文件；完整保存仍会校验原字节。':'当前没有需要接回的素材记录。新素材请用原来的添加入口。','hint'));
 return missing;
}
function relinkEntry(entry,record){
 const name=(record.filename||record.path).split('/').pop();safePath(name);
 // Use the recorded portable name for playback/export even if the disk file was renamed.
 const types={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp','.mp4':'video/mp4','.mov':'video/quicktime','.webm':'video/webm','.wav':'audio/wav','.mp3':'audio/mpeg','.m4a':'audio/mp4'};
 return {...entry,file:new File([entry.file],name,{type:types[extension(name)]||entry.file.type})};
}
async function relinkMeasure(entry,slots){
 const videos=slots.filter(s=>s.kind==='video');if(videos.length){const named=relinkEntry(entry,videos[0].record),actual=await measuredCandidate(named.file,named);
  for(const s of videos)for(const k of ['width','height','duration_ms'])require(s.record[k]===null||s.record[k]===actual[k],'视频尺寸或时长与原记录不同；不接回：'+s.label);
 }
 const rasters=slots.filter(s=>s.kind==='png');if(rasters.length){const named=relinkEntry(entry,rasters[0].record),actual=(await directorPNG(named.file)).raster;
  for(const s of rasters)require(s.record.width===actual.width&&s.record.height===actual.height,'目标 PNG 尺寸与原记录不同；不接回');
 }
}
async function relinkPrepare(files=[]){
 require(!studioBusy()&&!relinkState.busy,'其他文件操作仍在进行');require(files.length<=256,'一次最多选择 256 个文件；可分批找回');
 relinkClear();const guard=relinkGuard(),serial=relinkState.serial,slots=relinkSlots().filter(s=>!relinkBound(s));
 const needed=new Map();for(const s of slots){const h=s.record.sha256;if(!needed.has(h))needed.set(h,[]);needed.get(h).push(s);}
 require([...studioMedia(studioView()).values()].reduce((n,size)=>n+size,0)<=MAX_TOTAL,'所需素材超过 512 MiB；未扩大或压缩现有额度');
 if(!needed.size){relinkShowInventory();relinkNotice('没有缺失绑定，不改动已有素材。');return;}
 relinkState.busy=true;studioState.busy=true;$('relink-cancel').disabled=false;
 const matched=new Map(),ignored=[],invalid=[];
 try{
  // Other workspaces may already hold exactly the file a missing slot needs.
  const candidates=[...studioBindings().values()].map(e=>e.file).concat(files),seen=new Set();
  const sizes=new Set([...needed.values()].map(s=>s[0].record.bytes));
  for(const file of candidates){
   if(seen.has(file))continue;seen.add(file);
   require(serial===relinkState.serial,'已取消查找，当前工作未改变');require(guard===relinkGuard(),'查找期间工作或绑定已变化，请重新检查');
   if(!sizes.has(file.size)||file.size>MAX_FILE||file.size===0){ignored.push(file.name);continue;}
   relinkNotice('正在只读核对文件内容：'+file.name+'。尚未接回任何素材。');
   try{
    const entry=await inspectFile(file),refs=needed.get(entry.sha256);
    if(!refs||refs[0].record.bytes!==file.size){ignored.push(file.name);continue;}
    if(matched.has(entry.sha256))continue;
    await relinkMeasure(entry,refs);matched.set(entry.sha256,entry);
   }catch(e){invalid.push(file.name+'：'+(e.message||e));}
  }
  require(serial===relinkState.serial,'已取消查找，当前工作未改变');require(guard===relinkGuard(),'查找期间工作或绑定已变化，请重新检查');
  relinkState.pending={guard,serial,matched,slots,ignored,invalid};
  const root=$('relink-rows');root.replaceChildren();
  for(const [h,entry]of matched){const row=element('div',undefined,'relink-item');row.append(element('strong','找到：'+entry.file.name),element('div',needed.get(h).map(s=>s.label).join('；')),element('code',h));root.append(row);}
  for(const text of invalid)root.append(element('p','未接回：'+text,'reason'));
  $('relink-summary').textContent=`可接回 ${matched.size} 份原内容，覆盖 ${slots.filter(s=>matched.has(s.record.sha256)).length} 个缺失用途。仍缺 ${needed.size-matched.size} 份；${ignored.length} 个非匹配文件忽略，不作为新素材。`;
  $('relink-preview').hidden=false;relinkNotice(matched.size?'核对完成，尚未接回。确认后仅修复匹配的缺失绑定，原文、角色、候选及历史决定不变。':'没有找到相同内容。文件名相同也不能替代原片；新版本请作为新候选导入。');
 }finally{relinkState.busy=false;studioState.busy=false;relinkShowInventory();}
}
async function relinkApply(){
 require(!studioBusy()&&!relinkState.busy,'其他文件操作仍在进行');const p=relinkState.pending;
 require(p&&p.matched.size&&$('relink-confirmed').checked,'请先核对并明确确认接回');require(p.guard===relinkGuard(),'预览已经过时，当前工作未变');
 relinkState.busy=true;studioState.busy=true;
 try{
  // Re-read before commit: a selected on-disk file could have changed since preview.
  const verified=new Map();for(const[h,entry]of p.matched){const e=await inspectFile(entry.file);require(e.sha256===h&&e.file.size===entry.file.size,'原文件在预览后发生变化，整次接回取消');verified.set(h,e);}
  require(p===relinkState.pending&&p.guard===relinkGuard(),'核对期间工作已变化，整次接回取消');
  const changes=p.slots.filter(s=>verified.has(s.record.sha256)).map(s=>({slot:s,entry:relinkEntry(verified.get(s.record.sha256),s.record)}));
  // No awaits after this point. All metadata and all bytes verified before any binding changes.
  for(const {slot,entry}of changes)slot.set(entry);
  const repair=changes.some(x=>x.slot.label==='返工原片'),director=changes.some(x=>x.slot.label.startsWith('导演'));
  invalidate();reviewChanged();renderAssets();renderReview();
  if(repair)showRepairSource();if(director){directorSourceView();directorRender();}
  for(const id of ['review-confirmed','promotion-confirmed','repair-confirmed','director-confirmed','catalog-confirmed'])if($(id))$(id).checked=false;
  $('quality-only').checked=true;deskState.verified=null;studioState.verified=null;
  relinkClear();relinkShowInventory();relinkNotice(`已接回 ${verified.size} 份原内容、${changes.length} 个用途。未改稿、未添加候选、未生成或批准。请重新保存收工包并选回核验。`);
 }finally{relinkState.busy=false;studioState.busy=false;studioChanged();deskChanged();}
}
function relinkChanged(){
 if(relinkState.pending&&relinkState.pending.guard!==relinkGuard()){relinkClear();relinkNotice('工作或文件绑定已变化，旧找回预览已失效。新工作没有被覆盖。');}
 $('relink-apply').disabled=relinkState.busy||!relinkState.pending?.matched.size||!$('relink-confirmed').checked;
}
function relinkHandle(fn){return handled(async e=>{try{await fn(e);}catch(error){relinkClear();relinkNotice('未接回：'+(error.message||error),true);throw error;}finally{relinkChanged();}});}
$('relink-check').addEventListener('click',relinkHandle(()=>{relinkShowInventory();relinkNotice('按现有引用列出缺失绑定；尚未读取磁盘或改动素材。');}));
$('relink-files').addEventListener('change',relinkHandle(async e=>{try{await relinkPrepare([...e.target.files]);}finally{e.target.value='';}}));
$('relink-existing').addEventListener('click',relinkHandle(()=>relinkPrepare()));
$('relink-confirmed').addEventListener('change',relinkChanged);
$('relink-apply').addEventListener('click',relinkHandle(relinkApply));
$('relink-cancel').addEventListener('click',()=>{relinkClear();relinkNotice('已取消查找或预览，当前工作和磁盘原文件未改动。');});
$('relink-report').addEventListener('click',relinkHandle(()=>{
 const report=relinkInventory().filter(r=>r.missing),lines=['Manju 缺失原素材清单（只读，不包含媒体，不是备份）','只接受相同 SHA-256 和字节数；同名新版本请作为新候选导入。',''];
 for(const r of report)lines.push('用途：'+r.uses.join('；'),'原名：'+r.names.join(' / '),'SHA-256：'+r.sha256,'字节：'+r.bytes,'');
 download(new Blob([lines.join('\n')],{type:'text/plain;charset=utf-8'}),'MANJU_MISSING_MEDIA.txt');relinkNotice('已发起缺失清单下载。这不是媒体备份，也没有消除收工提醒。');
}));
for(const type of ['input','change','click'])document.addEventListener(type,()=>queueMicrotask(relinkChanged));
window.ManjuRelink={state:relinkState,inventory:relinkInventory,prepare:relinkPrepare,apply:relinkApply,guard:relinkGuard};
