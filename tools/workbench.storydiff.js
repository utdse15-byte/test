// Derived read-only story comparison. No acceptance, storage or approval state.
const STORY_DIFF_FIELDS={"story": {"title": "作品名称", "form": "作品形态", "intent": "作品意图", "ending": "作者结局字段", "link": "镜头关联"}, "scenes": {"episode": "分集", "title": "场景名称", "purpose": "这一场的作用", "action": "实际动作与因果", "dialogue": "对白", "before": "入场状态", "after": "离场变化", "source_ids": "引用依据", "references": "参考素材职责", "policy": "前情与知情范围"}, "sources": {"kind": "依据类型", "name": "名称", "text": "人物 / 依据正文", "scope": "可见范围", "from_scene_id": "起始场景", "knower_id": "知情人物", "provenance": "来源说明"}, "briefs": {"scene_id": "关联场景", "content": "已记录的制作内容", "content_sha256": "制作内容校验值", "observation": "实际成片复盘"}};
const STORY_DIFF_NOTES=["只比较这两份故事的字面内容与结构，不做剧情语义、画质或创作批准。", "结局字段未改，不等于其他改动不会影响结局；需连贯阅读人物动机和因果。", "编号识别对象，位置表示当时的实际场序；新增一场造成的位置后移不等于重写。", "包含作者计划与历史正文，分享前检查范围；报告不是素材备份或自动合并指令。"];
function storyDiffChanges(a,b,fields){
 const rows=[];
 for(const [field,label] of Object.entries(fields)){
  const av=a===null?null:a[field],bv=b===null?null:b[field];
  if((a===null)!==(b===null)||canonical(av)!==canonical(bv))rows.push({field,label,before:av,after:bv,before_present:a!==null,after_present:b!==null});
 }
 return rows;
}
function storyDiffEntities(before,after,kind){
 const left=before?.[kind]||[],right=after?.[kind]||[],a=new Map(left.map(x=>[x.id,x])),b=new Map(right.map(x=>[x.id,x]));
 const ap=new Map(left.map((x,i)=>[x.id,i+1])),bp=new Map(right.map((x,i)=>[x.id,i+1]));
 const rows=[],unchanged=[],sequence=[...right.map(x=>x.id),...left.filter(x=>!b.has(x.id)).map(x=>x.id)];
 for(const id of sequence){
  const old=a.get(id)||null,next=b.get(id)||null,changes=storyDiffChanges(old,next,STORY_DIFF_FIELDS[kind]),present=old!==null&&next!==null;
  const status=old===null?'added':next===null?'removed':changes.length?'changed':'unchanged',shift=present&&ap.get(id)!==bp.get(id);
  if(status==='unchanged')unchanged.push(id);
  if(status==='unchanged'&&!(kind==='scenes'&&shift))continue;
  const title=kind==='scenes'?'title':kind==='sources'?'name':'scene_id';
  rows.push({id,status,title_before:old?.[title]??null,title_after:next?.[title]??null,before_position:ap.get(id)??null,after_position:bp.get(id)??null,position_changed:shift,changes,unchanged_fields:Object.keys(STORY_DIFF_FIELDS[kind]).filter(k=>present&&!changes.some(r=>r.field===k))});
 }
 const ao=left.map(x=>x.id),bo=right.map(x=>x.id);
 return {rows,unchanged,order:{before:ao,after:bo,changed:canonical(ao)!==canonical(bo),shared_order_changed:canonical(ao.filter(x=>b.has(x)))!==canonical(bo.filter(x=>a.has(x)))}};
}
function compareStories(before,after){
 const a=before===null?null:storyNormalize(before),b=after===null?null:storyNormalize(after);
 const relationship=a===null&&b===null?'empty':a===null?'created':b===null?'removed':a.project_id===b.project_id?'same_project':'different_project';
 const report={schema_id:'manju.story-diff/v1',before_sha256:storyDigest(a),after_sha256:storyDigest(b),before_project_id:a?.project_id??null,after_project_id:b?.project_id??null,before_title:a?.title??null,after_title:b?.title??null,relationship,changed:canonical(a)!==canonical(b),read_only:true,automatic_execution:false,semantic_validation:false,merges_later_browser_edits:false,notes:[...STORY_DIFF_NOTES],story_fields:[],scenes:[],sources:[],briefs:[],unchanged_scene_ids:[],orders:{},brief_status_changes:[],ending_field_unchanged:null,last_scene_content_unchanged:null,last_scene_id:null};
 if(relationship==='different_project')return report;
 report.story_fields=storyDiffChanges(a,b,STORY_DIFF_FIELDS.story);
 for(const kind of ['scenes','sources','briefs']){const data=storyDiffEntities(a,b,kind);report[kind]=data.rows;report.orders[kind]=data.order;if(kind==='scenes')report.unchanged_scene_ids=data.unchanged;}
 if(a!==null&&b!==null){
  report.ending_field_unchanged=a.ending===b.ending;
  if(a.scenes.length&&b.scenes.length&&a.scenes.at(-1).id===b.scenes.at(-1).id){report.last_scene_id=a.scenes.at(-1).id;report.last_scene_content_unchanged=canonical(a.scenes.at(-1))===canonical(b.scenes.at(-1));}
  const previous=new Map(a.briefs.map(x=>[x.id,x]));
  for(const brief of b.briefs){const old=previous.has(brief.id)?storyBriefStatus(a,previous.get(brief.id)):null,next=storyBriefStatus(b,brief);if(old===null||canonical(old)!==canonical(next))report.brief_status_changes.push({id:brief.id,scene_id:brief.scene_id,before:old,after:next});}
 }
 return report;
}
function storyDiffValue(value,present=true){if(!present)return '（本侧不存在）';if(value==='')return '（空白）';return typeof value==='string'?value:JSON.stringify(value,null,2);}
function renderStoryDiff(container,report,labels={before:'当前页面',after:'待恢复版本'}){
 container.replaceChildren();container.className='story-diff';
 container.dataset.beforeHash=report.before_sha256;container.dataset.afterHash=report.after_sha256;
 if(report.relationship==='empty')return;
 if(report.relationship==='different_project'){
  container.append(element('p','这是另一部作品，不按同名编号比较场景；恢复会替换当前故事。','diff-warning'));
  container.append(element('p',`${report.before_title||'未命名'} [${report.before_project_id}] → ${report.after_title||'未命名'} [${report.after_project_id}]`,'diff-meta'));return;
 }
 const details=element('details'),summary=element('summary');details.open=report.relationship==='same_project'&&report.changed;
 const content=report.scenes.filter(x=>x.status!=='unchanged'),parts=[];
 if(report.relationship==='created')parts.push('新增故事');else if(report.relationship==='removed')parts.push('移除故事');
 if(content.length)parts.push(`${content.length} 场内容变化`);
 if(report.sources.length)parts.push(`${report.sources.length} 项人物 / 依据变化`);
 if(report.story_fields.length)parts.push(`${report.story_fields.length} 项作品设置变化`);
 if(report.briefs.length)parts.push(`${report.briefs.length} 项版本 / 复盘变化`);
 if(report.orders.scenes.changed)parts.push('场序变化');
 summary.textContent=report.changed?'故事对照 · '+(parts.join(' · ')||'排列变化'):'故事内容未变 · 查看核对范围';
 details.append(summary);container.append(details);
 const lead=element('p','当前页面 ↔ 待恢复版本；不代表原导出基线。只读，不自动合并。','diff-meta');details.append(lead);
 const facts=[];
 if(report.ending_field_unchanged!==null)facts.push(report.ending_field_unchanged?'作者结局字段未改':'作者结局字段有改动');
 if(report.relationship==='same_project')facts.push(report.orders.scenes.changed?'场序有变化':'场序未改');

 if(facts.length)details.append(element('p',facts.join(' · '),'diff-facts'));
 details.append(element('p','字面未改不代表剧情因果未变，请连贯阅读。','diff-meta'));
 function pair(row){
  const grid=element('div',undefined,'diff-pair');
  for(const[side,label]of [['before',labels.before],['after',labels.after]]){const box=element('section',undefined,'diff-'+side),head=element('h5',label),pre=element('pre',storyDiffValue(row[side],row[side+'_present']));pre.tabIndex=0;box.append(head,pre);grid.append(box);}return grid;
 }
 for(const row of report.story_fields){const article=element('article',undefined,'diff-entry');article.append(element('h4',row.label),pair(row));details.append(article);}
 for(const[kind,title]of [['scenes','场景'],['sources','人物与依据'],['briefs','版本与复盘']]){
  if(!report[kind].length)continue;const section=element('section',undefined,'diff-group');if(kind!=='scenes'||report[kind].length>1)section.append(element('h3',title));details.append(section);
  for(const item of report[kind]){
   const card=element('article',undefined,'diff-entry');card.dataset.storyId=item.id;
   const tag={added:'新增',removed:'移除',changed:'内容有改动',unchanged:'内容未改，仅位置变化'}[item.status];
   card.append(element('h4',item.title_after||item.title_before||'未命名'),element('p',`${kind==='scenes'?(item.before_position===item.after_position?`第 ${item.after_position} 场 · `:`第 ${item.before_position??'∅'} 位 → 第 ${item.after_position??'∅'} 位 · `):''}${item.id} · ${tag}`,'diff-meta'));
   if(item.unchanged_fields.length){const kept=element('details',undefined,'diff-unchanged');kept.append(element('summary',`${item.unchanged_fields.length} 项未改`),element('p',item.unchanged_fields.map(k=>STORY_DIFF_FIELDS[kind][k]).join('、')));card.append(kept);}
   for(const row of item.changes){const block=element('details',undefined,'diff-field');block.open=['action','dialogue','text'].includes(row.field);block.append(element('summary',row.label),pair(row));card.append(block);}section.append(card);
  }
 }
 if(report.brief_status_changes.length){const section=element('details');section.append(element('summary',`${report.brief_status_changes.length} 份制作简报的复核状态变化（旧版未改写）`));for(const item of report.brief_status_changes)section.append(element('p',`${item.id} · ${item.scene_id}：${item.before?.status||'无旧记录'} → ${item.after.status}；${item.after.changes.join('、')}`,'diff-meta'));details.append(section);}
 const audit=element('details');audit.append(element('summary','完整排列与未改场景编号'),element('pre',JSON.stringify({orders:report.orders,unchanged_scene_ids:report.unchanged_scene_ids,last_scene_id:report.last_scene_id,last_scene_content_unchanged:report.last_scene_content_unchanged},null,2)));details.append(audit);
 details.append(element('p','报告可能含作者私密计划与旧正文。它不是素材备份，也不证明是谁或哪个模型做了修改。','diff-meta'));
}
function presentStoryDiff(id,before,after){const report=compareStories(before,after);renderStoryDiff($(id),report);return report;}
function clearStoryDiff(id,message=''){const e=$(id);if(!e)return;e.replaceChildren();delete e.dataset.beforeHash;delete e.dataset.afterHash;if(message)e.append(element('p',message,'diff-warning'));}
window.ManjuStoryDiff={compare:compareStories,render:renderStoryDiff,present:presentStoryDiff,clear:clearStoryDiff};
