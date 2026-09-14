// Proposal data, never instructions. Same algorithm and report contract as Python.
const STORY_RETURN_SCHEMA='manju.story-return/v1',STORY_RETURN_LIMIT=2*1024*1024+4096;
const RETURN_PROJECT=['title','form','intent','ending'];
const RETURN_TEXT={sources:['name','text','provenance'],scenes:['episode','title','purpose','action','dialogue','before','after']};
const RETURN_STRUCT={sources:['kind','scope','from_scene_id','knower_id'],scenes:['source_ids','references','policy']};
const returnSame=(a,b)=>canonical(a)===canonical(b);
function returnStory(value){const d=storyNormalize(value);const singles=[d.title,...d.sources.flatMap(x=>[x.name,x.provenance]),...d.scenes.flatMap(x=>[x.title,x.episode])];require(singles.every(x=>!/[\r\n]/.test(x)),'单行故事字段不能保留换行，未应用');function check(v){if(typeof v==='string')require(!v.includes('\r'),'故事文字应使用 LF 换行，未应用');else if(Array.isArray(v))v.forEach(check);else if(v&&typeof v==='object')Object.values(v).forEach(check);}check(d);return d;}
function createStoryReturn(base,candidate){
 const a=returnStory(base),b=returnStory(candidate);require(a.project_id===b.project_id,'不是同一作品，不能按编号套用');require(returnSame(a.link,b.link),'故事改稿不能替换原镜头关联');
 const old=new Map(a.briefs.map(x=>[x.id,x])),next=new Map(b.briefs.map(x=>[x.id,x]));
 for(const[id,x]of old)require(next.has(id)&&returnSame(x,next.get(id)),'旧简报与历史观察不能重写或删除');
 require(returnSame(b.briefs.filter(x=>old.has(x.id)).map(x=>x.id),[...old.keys()]),'旧简报顺序不可重写');
 const p={schema_id:STORY_RETURN_SCHEMA,base:a,candidate:b,base_sha256:storyDigest(a),candidate_sha256:storyDigest(b),automatic_execution:false};require(encoder.encode(canonical(p)).length<=STORY_RETURN_LIMIT,'故事改稿超过文件上限');return p;
}
function parseStoryReturn(v){exactKeys(v,['schema_id','base','candidate','base_sha256','candidate_sha256','automatic_execution'],'故事改稿');require(v.schema_id===STORY_RETURN_SCHEMA&&v.automatic_execution===false,'故事改稿版本或边界无效');const p=createStoryReturn(v.base,v.candidate);require(returnSame(v,p),'故事改稿内容或校验值不符');return p;}
function returnSpecial(a,b){const s={};for(const k of Object.keys(RETURN_TEXT)){const old=new Set(a[k].map(x=>x.id)),next=new Set(b[k].map(x=>x.id));s[k]=new Set([...old].filter(x=>!next.has(x)).concat([...next].filter(x=>!old.has(x))));}return s;}
function returnStructure(d,special){const result={briefs:d.briefs};for(const[k,fields]of Object.entries(RETURN_STRUCT))result[k]=d[k].map(r=>special[k].has(r.id)?clone(r):Object.fromEntries(['id',...fields].map(f=>[f,r[f]])));return result;}
function previewStoryReturn(current,packet){
 const p=parseStoryReturn(packet),local=returnStory(current),a=p.base,b=p.candidate;require(local.project_id===a.project_id,'当前故事是另一部作品，未接回');const rows=[];
 function add(key,kind,id,field,before,after,now,present=true,bp=null,cp=null,ap=null,title=''){rows.push({key,kind,id,field,before,current:now,after,current_present:present,status:!present?'missing':returnSame(now,after)?'already_applied':returnSame(now,before)?'ready':'conflict',before_position:bp,current_position:cp,after_position:ap,title});}
 for(const f of RETURN_PROJECT)if(!returnSame(a[f],b[f]))add('project/'+f,'project',null,f,a[f],b[f],local[f],true,null,null,null,local.title);
 for(const[kind,fields]of Object.entries(RETURN_TEXT)){const old=new Map(a[kind].map((x,i)=>[x.id,[i+1,x]])),now=new Map(local[kind].map((x,i)=>[x.id,[i+1,x]]));b[kind].forEach((r,i)=>{if(!old.has(r.id))return;const[bp,x]=old.get(r.id),[cp,c]=now.get(r.id)||[null,null];for(const f of fields)if(!returnSame(x[f],r[f]))add((kind==='scenes'?'scene/':'source/')+r.id+'/'+f,kind,r.id,f,x[f],r[f],c?c[f]:null,c!==null,bp,cp,i+1,r.title??r.name??r.id);});}
 const special=returnSpecial(a,b),before=returnStructure(a,special),after=returnStructure(b,special);if(!returnSame(before,after))add('structure','structure',null,'structure',before,after,returnStructure(local,special),true,null,null,null,'人物、引用、场序与历史新增：整组接回');
 const r={schema_id:'manju.story-return-preview/v1',project_id:local.project_id,base_sha256:p.base_sha256,candidate_sha256:p.candidate_sha256,current_sha256:storyDigest(local),packet_sha256:storyDigest(p),rows,ready_keys:rows.filter(x=>x.status==='ready').map(x=>x.key),automatic_execution:false,media_unchanged:true,checks_only_this_local_snapshot:true};r.preview_sha256=storyDigest(r);return r;
}
function applyStoryReturn(current,packet,selected,expected){
 const r=previewStoryReturn(current,packet);require(r.preview_sha256===expected,'预览已过期，请重新比较当前内容');require(Array.isArray(selected)&&selected.length>0&&selected.every(k=>typeof k==='string')&&new Set(selected).size===selected.length,'请选择不重复的修改');const rows=new Map(r.rows.map(x=>[x.key,x]));require(selected.every(k=>rows.has(k)&&rows.get(k).status==='ready'),'所选项有冲突、缺失或已一致，未应用');
 const result=returnStory(current),b=packet.candidate;
 if(selected.includes('structure')){for(const[kind,fields]of Object.entries(RETURN_STRUCT)){const local=new Map(result[kind].map(x=>[x.id,x]));result[kind]=b[kind].map(x=>{if(!local.has(x.id))return clone(x);const y=clone(local.get(x.id));for(const f of fields)y[f]=clone(x[f]);return y;});}result.briefs=clone(b.briefs);}
 for(const key of selected){const row=rows.get(key);if(row.kind==='structure')continue;if(row.kind==='project')result[row.field]=clone(row.after);else result[row.kind].find(x=>x.id===row.id)[row.field]=clone(row.after);}
 return returnStory(result);
}
window.ManjuStoryReturnCore={create:createStoryReturn,parse:parseStoryReturn,preview:previewStoryReturn,apply:applyStoryReturn};
