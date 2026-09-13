/* A navigation/presentation layer only: preserve live nodes, input values, guards,
   media bytes and all existing approval/export handlers. No persistence or network. */
(() => {
 'use strict';
 const $ = id => document.getElementById(id);
 if (!$('ux-sidebar') || !window.ManjuDesk) return;
 const main = document.querySelector('main');
 const views = {
  home: {title:'开始与保存',heading:'把注意力留给创作。',note:'打开一份材料继续工作；收工时，把原素材和待处理意见一起带走。',en:'YOUR LOCAL CREATIVE SPACE',target:'studio-home'},
  story: {title:'故事与人物',heading:'把故事展开，一场一场写。',note:'先写场景，再选人物与参考，最后记录制作版本。随时切换，未完成的文字也会保留。',en:'STORY & CHARACTERS',target:'story-section'},
  shot: {title:'镜头与素材',heading:'先把这个镜头说清楚。',note:'写下画面与声音，绑定参考素材，再选择适配路径。质量优先，不自动降档。',en:'SHOT & REFERENCES',target:'ux-shot'},
  review: {title:'审片与定稿',heading:'先看画面，再做决定。',note:'比较实际候选，保留每次人工意见。规格通过不等于创作批准。',en:'REVIEW & DECIDE',target:'review-section'},
  director: {title:'导演与精修',heading:'定好运动，再打磨画面。',note:'保留运动底片，提取关键时刻，带出精修，再把高分辨率目标图接回来。',en:'DIRECT & REFINE',target:'director-section'},
  repair: {title:'局部返工',heading:'只交代需要改变的部分。',note:'标记返工范围、上下文与保留要求。原视频始终保留，不自动剪切或覆盖。',en:'REFINE A MOMENT',target:'repair-section'},
  exchange: {title:'外部协作',heading:'带走材料，带回好修改。',note:'文字与原素材一起导出。返回时逐字段核对，不让外部旧稿覆盖本地新内容。',en:'WORK WITH YOUR TOOLS',target:'exchange-section'},
  flex: {title:'组合与模板',heading:'把做对的部分留下来。',note:'取用另一方案的工作区，或套用自己的文字模板。未选部分保持不变。',en:'REUSE & EXPLORE',target:'flex-section'}
 };
 let current='home',all=false,notificationTimer=null,saveSyncQueued=false,lastNotice='';
 const scrollPositions=new Map();
 const nodes=new Map();
 function register(node,view){if(node){node.dataset.uxView=view;nodes.set(node,view);}}
 register($('studio-home'),'home');
 const oldToolbar=main.querySelector(':scope > .toolbar');
 const tools=document.createElement('details');tools.id='ux-shot-tools';tools.dataset.uxDisclosure='';
 const toolsLabel=document.createElement('summary');toolsLabel.textContent='任务文件、能力档与单区备份';tools.append(toolsLabel);
 if(oldToolbar){oldToolbar.before(tools);tools.append(oldToolbar);}
 if($('workspace-section'))tools.append($('workspace-section'));
 register(tools,'shot');register($('catalog-preview'),'shot');register($('story-section'),'story');
 const grid=main.querySelector(':scope > .grid');if(grid){grid.id='ux-shot';register(grid,'shot');}
 for(const key of ['review','director','repair','exchange','flex'])register($(views[key].target),key);
 // Keep old backup controls available, but stop competing with the daily save action.
 const home=$('studio-home'),routes=home.querySelector('.studio-routes');
 const legacy=document.createElement('details');legacy.id='ux-legacy-backup';legacy.dataset.uxDisclosure='';
 const legacyTitle=document.createElement('summary');legacyTitle.textContent='旧版总备份与兼容恢复';legacy.append(legacyTitle);
 if(routes){let n=routes.nextSibling;while(n){const next=n.nextSibling;legacy.append(n);n=next;}home.append(legacy);
  const label=document.createElement('div');label.className='ux-home-kicker';label.innerHTML='<strong>从这里继续</strong><span>随时切换，输入不会丢</span>';routes.before(label);
 }
 const heading=document.createElement('div');heading.className='ux-page-heading';
 heading.innerHTML='<div><p id="ux-eyebrow" class="ux-eyebrow"></p><h1 id="ux-heading" tabindex="-1"></h1><p id="ux-description"></p></div><span class="ux-mode-tag">本地运行 · 原片保留</span>';
 main.prepend(heading);
 const status=$('status');if(status)heading.after(status);
 const help=main.querySelector(':scope > details.panel');if(help){help.id=help.id||'ux-help';register(help,'home');}
 const header=$('ux-topbar');
 const notice=document.createElement('div');notice.id='ux-notification';notice.hidden=true;
 const noticeText=document.createElement('p'),noticeClose=document.createElement('button');noticeClose.type='button';noticeClose.textContent='关闭';noticeClose.setAttribute('aria-label','关闭操作提示');notice.append(noticeText,noticeClose);document.body.append(notice);
 noticeClose.addEventListener('click',()=>{notice.hidden=true;lastNotice='';clearTimeout(notificationTimer);});
 function notify(text,error=false){
  if(!text||text===lastNotice)return;lastNotice=text;noticeText.textContent=text;notice.classList.toggle('error',error);notice.hidden=false;clearTimeout(notificationTimer);
  if(!error)notificationTimer=setTimeout(()=>{notice.hidden=true;lastNotice='';},6500);
 }
 function revealDetails(target){for(let p=target;p&&p!==main;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;}
 function viewFor(target){if(!target)return null;if(target.id==='workspace-section')return 'shot';for(let p=target;p&&p!==main;p=p.parentElement)if(p.dataset.uxView)return p.dataset.uxView;return null;}
 function present({scroll=true,focus=false}={}){
  for(const[node,view]of nodes)node.classList.toggle('ux-view-hidden',!all&&view!==current);
  document.body.classList.toggle('ux-all',all);
  for(const link of document.querySelectorAll('#ux-navigation [data-view]')){
   if(link.dataset.view===current&&!all)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
  }
  $('ux-show-all').setAttribute('aria-pressed',String(all));$('ux-show-all').textContent=all?'返回分区视图':'完整长页视图';
  const activeLink=document.querySelector('#ux-navigation [aria-current="page"]'),nav=$('ux-navigation');if(activeLink&&nav.scrollWidth>nav.clientWidth){const linkBox=activeLink.getBoundingClientRect(),navBox=nav.getBoundingClientRect();if(linkBox.left<navBox.left)nav.scrollLeft-=navBox.left-linkBox.left;else if(linkBox.right>navBox.right)nav.scrollLeft+=linkBox.right-navBox.right;}
  const info=views[current];$('ux-current').textContent=all?'完整长页':info.title;$('ux-heading').textContent=all?'所有工具，完整展开。':info.heading;
  $('ux-eyebrow').textContent=all?'COMPLETE WORKBENCH':info.en;$('ux-description').textContent=all?'这是原有的完整工作方式。需要专注时，切回分区视图；文字与素材保持不变。':info.note;
  window.ManjuComfort?.sync();
  // Avoid invisible video/audio continuing to play when switching tasks. Never seek or autoplay.
  if(!all)for(const media of main.querySelectorAll('video,audio'))if(media.closest('.ux-view-hidden'))media.pause();
  if(scroll)window.scrollTo({top:0,behavior:'auto'});if(focus)$('ux-heading').focus({preventScroll:true});
 }
 function navigate(view,{scroll=true,focus=false}={}){if(!views[view])return false;if(!all&&current!==view)scrollPositions.set(current,window.scrollY);current=view;present({scroll:false,focus});if(scroll)window.scrollTo({top:all?0:(scrollPositions.get(view)||0),behavior:'auto'});return true;}
 function reveal(id,{scroll=true,focus=false}={}){
  const target=$(id);if(!target)return false;
  const view=viewFor(target);if(view)navigate(view,{scroll:false});window.ManjuComfort?.reveal(target);revealDetails(target);
  if(scroll)target.scrollIntoView({block:'start',behavior:'auto'});
  if(focus){if(target.matches('input,textarea,select,button'))target.focus({preventScroll:true});else{$('ux-heading').focus({preventScroll:true});}}
  return true;
 }
 function showAll({scroll=true}={}){all=true;for(const e of document.querySelectorAll('[data-ux-disclosure]'))e.open=true;present({scroll});}
 $('ux-show-all').addEventListener('click',()=>{if(all){all=false;present({focus:true});}else showAll();});
 // In-page links remain real links with useful labels, no dispatch into data handlers.
 document.addEventListener('click',event=>{
  const a=event.target.closest('a[href^="#"]');if(!a||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
  const id=a.getAttribute('href').slice(1);if(!id)return;
  if(a.dataset.view){event.preventDefault();navigate(a.dataset.view,{focus:true});if(all)reveal(views[a.dataset.view].target);return;}
  if(id==='ux-content'){event.preventDefault();$('ux-heading').focus();return;}
  if($(id)&&viewFor($(id))){event.preventDefault();reveal(id,{focus:true});}
 });
 // Original workflows request navigation explicitly; no DOM-prototype patching.
 $('open-review')?.addEventListener('click',()=>reveal('review-section'));
 $('ux-open').addEventListener('click',()=>{navigate('home');$('intake-files').click();});
 function save(){if(window.ManjuDesk.isBusy()){notify('正在处理文件，请等当前操作完成。',true);return;}$('desk-save').click();syncSave();}
 $('ux-save').addEventListener('click',save);
 $('ux-save-detail').addEventListener('click',()=>reveal('desk-checkout',{focus:true}));
 $('ux-text-size').addEventListener('click',()=>{const enabled=document.body.classList.toggle('ux-large');$('ux-text-size').setAttribute('aria-pressed',String(enabled));$('ux-text-size').textContent=enabled?'标准字号':'大字阅读';});
 function syncSave(){
  const busy=window.ManjuDesk.isBusy();$('ux-save').disabled=busy;$('ux-save').textContent=busy?'正在整理…':'保存收工包 ↗';
  const strip=$('ux-save-strip'),changed=window.ManjuDesk.needsSave(),verified=Boolean(window.ManjuDesk.state.verified)&&!changed;
  strip.dataset.state=verified?'verified':changed?'dirty':'unverified';
  $('ux-save-summary').textContent=busy?'正在整理原素材，请等待；新输入不会冒充已保存。':verified?'当前收工包已核验；继续编辑会提示重新保存。':changed?'有未核验备份的修改 · 收工时请保存并选回核验。':'尚未核验收工包 · 文件仅在本地读取，不自动云备份。';
 }
 function scheduleSync(){if(saveSyncQueued)return;saveSyncQueued=true;queueMicrotask(()=>{saveSyncQueued=false;syncSave();});}
 for(const type of ['input','change','click'])document.addEventListener(type,scheduleSync);
 const observer=new MutationObserver(records=>{
  if(records.some(r=>r.target===status||status?.contains(r.target)))notify(status.textContent,status.classList.contains('error'));
  if(records.some(r=>r.target===$('desk-save-status')||$('desk-save-status').contains(r.target))){if($('desk-save-status').classList.contains('reason')||!['dirty','preview'].includes($('desk-save-status').dataset.noticeKind))notify($('desk-save-status').textContent,$('desk-save-status').classList.contains('reason'));scheduleSync();}
 });
 if(status)observer.observe(status,{childList:true,subtree:true,characterData:true});observer.observe($('desk-save-status'),{childList:true,subtree:true,characterData:true});
 // Unified intake can finish asynchronously. Surface its preview instead of hiding it.
 for(const id of ['intake-preview','desk-restore-preview','studio-restore-preview','catalog-preview']){
  const node=$(id);if(!node)continue;new MutationObserver(()=>{if(!node.hidden)reveal(id);}).observe(node,{attributes:true,attributeFilter:['hidden']});
 }
 // Optional local continuation is injected after the self-contained page starts.
 function connectContinuation(){const panel=$('continue-panel');if(panel&&!nodes.has(panel)){register(panel,'home');$('studio-home').after(panel);present({scroll:false});}}
 new MutationObserver(connectContinuation).observe(main,{childList:true});connectContinuation();
 const commands=[
  {name:'本地 IDE AI 接手',description:'保存收工包，再在本地工作目录编辑并核验返回。',target:'ide-guide',words:'AI IDE Codex Claude Cursor 接管'},
  {name:'故事、人物与制作版本',description:'作品形态、真实场序、知情范围、参考职责与变更复核。',target:'story-section',words:'剧情 长篇 人物 伏笔 前情 故事 版本 关系'},
  {name:'原尺寸查看关键帧与目标图',description:'进入导演区，点击缩略图打开大图。只查看，不修改或批准。',target:'director-anchors',words:'大图 放大 100% 看图 对比 精修 原图'},
  ...Object.entries(views).map(([key,v])=>({name:v.title,description:v.note,target:v.target,words:key})),
  {name:'找回改名 / 搬家的素材',description:'按内容核对原文件，不按同名替换。',target:'relink-panel',words:'缺失 重新绑定 hash 原片'},
  {name:'带出关键帧精修',description:'原图与逐图要求一起导出，返回后预览接回。',target:'retouch-section',words:'图片 修图 png 图像'},
  {name:'保存或核验收工包',description:'前往保存入口，不立即下载或替换。',target:'desk-checkout',words:'备份 导出 zip 保存 下载 核验'},
  {name:'检查模型适配',description:'按现有质量优先规则检查，不自动选择。',target:'check-plan',words:'模型 能力 参数 画幅 时长'},
  {name:'返回视频规格检查',description:'核对时长、画幅与像素；不等于画质批准。',target:'return-section',words:'分辨率 720 1080 视频 检查'},
  {name:'能力档与任务文件',description:'导入导出能力目录，变更前仍需预览确认。',target:'ux-shot-tools',words:'json catalog 配置 导入'},
  {name:'大字阅读 / 标准字号',description:'仅改变显示字号，原图与视频不变。',action:'size',words:'字体 放大 阅读'},
  {name:'完整长页 / 分区视图',description:'切换工具展示方式，输入和素材不变。',action:'all',words:'所有 工具 完整 视图'},
  {name:'本机续作恢复点',description:'需要使用可选本机入口；普通 HTML 不开启自动保存。',target:'continue-panel',words:'自动保存 本地 恢复点 续作'}
 ];
 const dialog=$('ux-search-dialog'),search=$('ux-search-input'),results=$('ux-search-results');let previousFocus=null;
 function runCommand(command){dialog.close();if(command.action==='size')$('ux-text-size').click();else if(command.action==='all')$('ux-show-all').click();else if(!reveal(command.target,{focus:true}))notify('普通 HTML 未开启本机续作。请从解压目录运行 OPEN_CONTINUE_WINDOWS.cmd（需要 Python 3.11+）。');}
 function renderSearch(){
  const terms=search.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean),found=commands.filter(c=>terms.every(t=>(c.name+' '+c.description+' '+c.words).toLocaleLowerCase().includes(t)));
  results.replaceChildren();$('ux-search-count').textContent=found.length?`${found.length} 个入口 · 只导航，不执行创作操作`:'没有匹配的入口。可试试“保存”“精修”“找回”。';
  for(const c of found){const button=document.createElement('button');button.type='button';const label=document.createElement('span');label.textContent=c.name;const small=document.createElement('small');small.textContent=c.description;label.append(small);const arrow=document.createElement('span');arrow.textContent='↗';arrow.setAttribute('aria-hidden','true');button.append(label,arrow);button.addEventListener('click',()=>runCommand(c));results.append(button);}
 }
 function openSearch(){if(document.querySelector('dialog[open]'))return;previousFocus=document.activeElement;search.value='';renderSearch();dialog.showModal();search.focus();}
 $('ux-search-open').addEventListener('click',openSearch);$('ux-search-close').addEventListener('click',()=>dialog.close());
 dialog.addEventListener('close',()=>{if(document.activeElement===document.body||dialog.contains(document.activeElement))previousFocus?.focus({preventScroll:true});});
 search.addEventListener('input',renderSearch);
 dialog.addEventListener('keydown',event=>{
  if(event.isComposing)return;if(event.key==='Escape'){event.preventDefault();dialog.close();return;}const buttons=[...results.querySelectorAll('button')],i=buttons.indexOf(document.activeElement);
  if(event.key==='ArrowDown'&&buttons.length){event.preventDefault();buttons[(i+1)%buttons.length].focus();}
  else if(event.key==='ArrowUp'&&buttons.length){event.preventDefault();if(i<=0)search.focus();else buttons[i-1].focus();}
  else if(event.key==='Enter'&&document.activeElement===search&&buttons.length){event.preventDefault();buttons[0].click();}
 });
 document.addEventListener('keydown',event=>{
  if(event.isComposing||event.keyCode===229||event.altKey)return;
  if((event.ctrlKey||event.metaKey)&&!event.shiftKey&&event.key.toLowerCase()==='k'){event.preventDefault();openSearch();}
  if((event.ctrlKey||event.metaKey)&&!event.shiftKey&&event.key.toLowerCase()==='s'){event.preventDefault();if(!document.querySelector('dialog[open]'))save();}
 });
 const currentHash=()=>{let id;try{id=decodeURIComponent(location.hash.slice(1));}catch{return;}if(id&&$(id))reveal(id);};
 window.addEventListener('hashchange',currentHash);
 window.ManjuExperience={version:1,navigate,reveal,showAll,state:()=>({view:current,all,large:document.body.classList.contains('ux-large')}),openSearch};
 document.body.classList.add('ux-ready');present({scroll:false});syncSave();currentHash();
})();
