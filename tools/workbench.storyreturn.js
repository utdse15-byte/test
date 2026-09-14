// Preview state is temporary. Accepted story uses the existing Desk/v2 save path.
const storyReturnState={packet:null,pending:null,undo:null,serial:0,selected:new Set(),page:0};
const returnNames={ready:'可接回',conflict:'双方都改过 · 保留本地',missing:'对象已不存在 · 不按同名套用',already_applied:'内容已一致 · 不重复接回'};
const RETURN_PAGE=40;
const returnText=v=>v===null?'（当前不存在）':typeof v==='string'?(v===''?'（空白）':v):JSON.stringify(v,null,2);
function returnNotice(text,error=false){$('story-return-status').textContent=text;$('story-return-status').classList.toggle('error',error);}
function returnRefresh(){
 const p=storyReturnState.pending;
 if(p&&p.guard!==deskGuard()){storyReturnState.pending=null;storyReturnState.selected.clear();for(const x of $('story-return-rows').querySelectorAll('input')){x.checked=false;x.disabled=true;}$('story-return-files').open=true;$('story-return-summary').textContent='比较后当前工作已变化，旧选择失效。请用当前稿重新比较；没有覆盖新输入。';}
 const n=storyReturnState.selected.size;$('story-return-apply').disabled=!storyReturnState.pending||!n;$('story-return-apply').textContent=n?`接回所选 ${n} 项修改`:'接回所选修改';
 $('story-return-preview').disabled=!storyReturnState.packet;$('story-return-download').disabled=!storyReturnState.packet;
 for(const id of ['story-return-select','story-return-clear'])$(id).disabled=!storyReturnState.pending;
 $('story-return-undo').disabled=!storyReturnState.undo||storyReturnState.undo.guard!==deskGuard();
}
function returnStructuralSummary(row){
 const box=element('div');
 for(const[kind,label]of [['sources','人物与依据'],['scenes','场景'],['briefs','历史简报']]){
  const a=new Map(row.before[kind].map(x=>[x.id,x])),b=new Map(row.after[kind].map(x=>[x.id,x]));
  const added=[...b.keys()].filter(x=>!a.has(x)),removed=[...a.keys()].filter(x=>!b.has(x)),changed=[...b.keys()].filter(x=>a.has(x)&&!returnSame(a.get(x),b.get(x)));
  if(added.length)box.append(element('p',label+'新增：'+added.join('、'),'return-structure-line'));
  if(removed.length)box.append(element('p',label+'移除：'+removed.join('、'),'return-structure-line'));
  if(changed.length)box.append(element('p',label+'设置或引用改变：'+changed.join('、'),'return-structure-line'));
  if(!returnSame([...a.keys()],[...b.keys()]))box.append(element('p',label+'顺序：'+[...a.keys()].join(' → ')+'\n变为：'+[...b.keys()].join(' → '),'return-structure-line'));
 }
 box.append(element('p','整组默认不选。当前结构或要删除的正文后来变了，将拒绝整组；未勾选的现存正文保留。','hint'));
 const detail=element('details',undefined,'return-base');detail.append(element('summary','查看完整结构与三方记录'));
 for(const[k,label]of [['before','当时的原稿'],['current','本地现在'],['after','外部建议']])detail.append(element('h4',label),element('pre',returnText(row[k])));
 box.append(detail);return box;
}
function renderStoryReturn(){
 const r=storyReturnState.pending?.report;if(!r)return;const root=$('story-return-rows');root.replaceChildren();const boxes=new Map();
 const start=storyReturnState.page*RETURN_PAGE;
 for(const row of r.rows.slice(start,start+RETURN_PAGE)){
  const key=row.kind+'/'+(row.id||'');let box=boxes.get(key);if(!box){box=element('section',undefined,'return-object');boxes.set(key,box);root.append(box);box.append(element('h3',row.kind==='project'?'作品设置':row.title||'未命名'));if(row.id)box.append(element('p',(row.kind==='scenes'?`当前第 ${row.current_position??'∅'} 场 · 导出时第 ${row.before_position} 场 · `:'')+row.id,'hint'));}
  const field=element('div',undefined,'return-field');field.dataset.status=row.status;field.dataset.returnField=row.key;
  const label=element('label',undefined,'checkline'),check=document.createElement('input');check.type='checkbox';check.dataset.returnKey=row.key;check.disabled=row.status!=='ready';check.checked=storyReturnState.selected.has(row.key);
  const title=row.kind==='structure'?'结构整组（新增 / 删除 / 引用 / 场序 / 历史）':STORY_DIFF_FIELDS[row.kind==='project'?'story':row.kind]?.[row.field]||row.field;
  label.append(check,document.createTextNode(title+' · '+returnNames[row.status]));field.append(label);check.addEventListener('change',()=>{check.checked?storyReturnState.selected.add(row.key):storyReturnState.selected.delete(row.key);returnRefresh();});
  if(row.kind==='structure')field.append(returnStructuralSummary(row));else{
   const pair=element('div',undefined,'return-pair');for(const[k,l]of [['current','本地现在 · 未选就保留'],['after','外部新稿 · 只接回所选']]){const sec=element('section'),pre=element('pre',returnText(row[k]));pre.tabIndex=0;sec.append(element('h4',l),pre);pair.append(sec);}field.append(pair);
   const d=element('details',undefined,'return-base');d.append(element('summary','查看当时交给 AI 的原文'),element('pre',returnText(row.before)));field.append(d);
  }box.append(field);
 }
 const ready=r.rows.filter(x=>x.status==='ready').length,conflict=r.rows.filter(x=>['missing','conflict'].includes(x.status)).length,done=r.rows.filter(x=>x.status==='already_applied').length;
 $('story-return-summary').textContent=`${ready} 项可接回 · ${conflict} 项保留本地 / 需另行处理 · ${done} 项已一致。未选文字、镜头、素材和历史保持。`+(r.rows.length>RETURN_PAGE?' 每页40项，所选总数包含其他页；结构组永远单独勾选。':'');
 if(!r.rows.length)root.append(element('p','这份改稿没有故事变化。','hint'));
 const pages=Math.ceil(r.rows.length/RETURN_PAGE)||1;$('story-return-paging').hidden=pages===1;$('story-return-page-label').textContent=`第 ${storyReturnState.page+1} / ${pages} 页`;$('story-return-prev').disabled=storyReturnState.page===0;$('story-return-next').disabled=storyReturnState.page+1>=pages;
 $('story-return-results').hidden=false;returnRefresh();
}
function returnCompare(selectReady=true){
 require(storyReturnState.packet&&storyState.doc,'请先打开同一作品的故事和改稿');
 storyReturnState.pending=null;storyReturnState.selected.clear();$('story-return-results').hidden=true;
 const report=previewStoryReturn(storyView(),storyReturnState.packet);storyReturnState.pending={report,guard:deskGuard()};storyReturnState.page=0;storyReturnState.selected=new Set(selectReady?report.rows.filter(r=>r.status==='ready'&&r.kind!=='structure').map(r=>r.key):[]);renderStoryReturn();$('story-return-files').open=false;$('story-return-section').dataset.loaded='true';return report;
}
async function returnLoad(file){
 const serial=++storyReturnState.serial,guard=deskGuard();storyReturnState.pending=null;storyReturnState.selected.clear();$('story-return-results').hidden=true;returnRefresh();
 try{require(file&&file.size>0&&file.size<=STORY_RETURN_LIMIT,'改稿为空或超过文件上限');
  const p=parseStoryReturn(strictJSON(new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer())));
  require(serial===storyReturnState.serial&&guard===deskGuard(),'读取期间工作已变，未覆盖新输入');previewStoryReturn(storyView(),p);storyReturnState.packet=p;
  returnCompare();$('story-return-section').open=true;returnNotice('已载入：'+file.name+'。尚未接回，冲突保留本地。');
 }catch(e){if(serial!==storyReturnState.serial)return;$('story-return-files').open=true;$('story-return-results').hidden=true;returnNotice('新改稿未载入：'+e.message+'。当前故事不变，上一份有效改稿仍可另存或重新比较。',true);throw e;}finally{returnRefresh();}
}
$('story-return-file').addEventListener('change',storyHandler(async e=>{try{if(e.target.files[0])await returnLoad(e.target.files[0]);}finally{e.target.value='';}}));
$('story-return-preview').addEventListener('click',storyHandler(()=>{returnCompare();returnNotice('已用当前稿重新比较，没有改写正文。');}));
$('story-return-select').addEventListener('click',()=>{const p=storyReturnState.pending;if(!p)return;storyReturnState.selected=new Set(p.report.rows.filter(r=>r.status==='ready'&&r.kind!=='structure').map(r=>r.key));renderStoryReturn();});
$('story-return-clear').addEventListener('click',()=>{storyReturnState.selected.clear();renderStoryReturn();});
for(const[id,delta]of [['story-return-prev',-1],['story-return-next',1]])$(id).addEventListener('click',()=>{if(!storyReturnState.pending)return;storyReturnState.page+=delta;renderStoryReturn();$('story-return-results').scrollIntoView({block:'start'});});
$('story-return-apply').addEventListener('click',storyHandler(()=>{
 require(!studioBusy(),'其他文件操作仍在进行');const p=storyReturnState.pending;require(p&&p.guard===deskGuard(),'预览已过期，请重新比较');
 const before=storyView(),selected=[...storyReturnState.selected],after=applyStoryReturn(before,storyReturnState.packet,selected,p.report.preview_sha256);
 storyReturnState.pending=null;storyState.doc=after;storyRender();$('quality-only').checked=true;invalidate();storyChanged();
 storyReturnState.undo={doc:before,guard:deskGuard(),selected};returnCompare(false);returnNotice(`已接回 ${selected.length} 项；未选内容保留。旧简报不改写，相关故事变化仍需复核。请保存新的收工包。`);returnRefresh();
}));
$('story-return-undo').addEventListener('click',storyHandler(()=>{
 const u=storyReturnState.undo;require(u&&u.guard===deskGuard(),'后来又写了新内容，旧撤回不能覆盖它');const doc=returnStory(u.doc);
 storyReturnState.undo=null;storyReturnState.pending=null;storyState.doc=doc;storyRender();invalidate();storyChanged();const r=returnCompare(false);storyReturnState.selected=new Set(u.selected.filter(k=>r.ready_keys.includes(k)));renderStoryReturn();returnNotice('已撤回上一次接回；原素材未动，已清除的确认和批准不会恢复。');returnRefresh();
}));
$('story-return-dismiss').addEventListener('click',()=>{storyReturnState.pending=null;storyReturnState.selected.clear();$('story-return-results').hidden=true;returnNotice('已收起。未接回的内容还在原 JSON 中，可重新比较或另存。');returnRefresh();});
$('story-return-download').addEventListener('click',storyHandler(()=>{require(storyReturnState.packet,'没有已载入的改稿');download(new Blob([encoder.encode(canonical(storyReturnState.packet))],{type:'application/json'}),'STORY_RETURN_'+storyReturnState.packet.candidate.project_id+'.json');returnNotice('已发起待处理改稿下载，不含媒体。请在下载列表确认；它不在收工包中。');}));
for(const event of ['input','change','click'])document.addEventListener(event,()=>queueMicrotask(returnRefresh));
window.ManjuStoryReturn={state:storyReturnState,load:returnLoad,compare:returnCompare,refresh:returnRefresh};
