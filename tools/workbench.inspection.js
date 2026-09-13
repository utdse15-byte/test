/* Local original-image inspection. Only presentation state changes; original
   blob URLs remain owned by the director workspace. No network or persistence. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id), dialog=$('ux-image-dialog');
 if(!dialog||!window.ManjuExperience)return;
 const stage=$('ux-image-stage'),surface=$('ux-image-surface'),image=$('ux-image-original');
 let items=[],index=0,scale=1,mode='fit',previousFocus=null,previousOverflow='',loadToken=0,drag=null,active=false;
 const thumbnails=()=>[...$('director-anchors').querySelectorAll('img')].filter(img=>img.closest('[data-anchor]')&&/^(blob:|data:image\/png[;,])/.test(img.currentSrc||img.src));
 function current(){return items[index];}
 function layout(center=false){
  if(!dialog.open||!image.naturalWidth||!image.naturalHeight)return;
  const cw=Math.max(1,stage.clientWidth-2),ch=Math.max(1,stage.clientHeight-2);
  const rx=(stage.scrollLeft+cw/2)/Math.max(1,surface.offsetWidth),ry=(stage.scrollTop+ch/2)/Math.max(1,surface.offsetHeight);
  if(mode==='fit')scale=Math.min(1,cw/image.naturalWidth,ch/image.naturalHeight);
  const width=Math.max(1,Math.round(image.naturalWidth*scale)),height=Math.max(1,Math.round(image.naturalHeight*scale));
  surface.style.width=width+'px';surface.style.height=height+'px';surface.style.marginTop=Math.max(0,(ch-height)/2)+'px';
  stage.classList.toggle('ux-pannable',width>cw||height>ch);
  stage.scrollLeft=center?Math.max(0,(width-cw)/2):rx*width-cw/2;
  stage.scrollTop=center?Math.max(0,(height-ch)/2):ry*height-ch/2;
  $('ux-image-zoom').textContent=Math.round(scale*100)+'%';
  $('ux-image-fit').setAttribute('aria-pressed',String(mode==='fit'));
  $('ux-image-actual').setAttribute('aria-pressed',String(mode==='manual'&&scale===1));
  $('ux-image-minus').disabled=scale<=.1;$('ux-image-plus').disabled=scale>=4;
 }
 function show(n){
  if(!items.length)return;
  index=Math.max(0,Math.min(items.length-1,n));const source=current();
  if(!source?.isConnected){closeViewer();return;}
  const token=++loadToken,anchor=source.closest('[data-anchor]').dataset.anchor;
  $('ux-image-title').textContent=anchor+' · '+source.alt;
  $('ux-image-meta').textContent='正在读取原图…';
  $('ux-image-prev').disabled=index===0;$('ux-image-next').disabled=index===items.length-1;
  mode='fit';image.alt=anchor+' '+source.alt;image.style.visibility='hidden';stage.setAttribute('aria-busy','true');
  image.onload=()=>{if(token!==loadToken||!dialog.open)return;image.style.visibility='visible';stage.setAttribute('aria-busy','false');$('ux-image-meta').textContent=`${image.naturalWidth} × ${image.naturalHeight} 像素 · ${index+1} / ${items.length} · 原文件只读`;layout(true);};
  image.onerror=()=>{if(token!==loadToken)return;stage.setAttribute('aria-busy','false');$('ux-image-meta').textContent='这张原图暂时无法显示，请关闭后检查素材绑定。';};
  image.src=source.currentSrc||source.src;
 }
 function open(source,trigger){
  if(document.querySelector('dialog[open]'))return;
  items=thumbnails();index=items.indexOf(source);if(index<0)return;
  previousFocus=trigger;previousOverflow=document.body.style.overflow;
  document.querySelectorAll('video,audio').forEach(media=>{if(!media.paused)media.pause();});
  active=true;dialog.showModal();document.body.style.overflow='hidden';show(index);$('ux-image-close').focus({preventScroll:true});
 }
 function hydrate(){
  for(const source of thumbnails()){
   if(source.closest('.ux-image-trigger'))continue;
   const button=document.createElement('button');button.type='button';button.className='ux-image-trigger';
   button.setAttribute('aria-haspopup','dialog');button.setAttribute('aria-controls','ux-image-dialog');
   button.setAttribute('aria-label','查看 '+source.closest('[data-anchor]').dataset.anchor+' '+source.alt+'大图');
   const caption=document.createElement('span');caption.textContent='查看大图 · 支持原尺寸';
   source.before(button);button.append(source,caption);button.addEventListener('click',()=>open(source,button));
  }
  // Async workspace restoration can replace the original thumbnails. Never keep
  // presenting a revoked/stale URL as the newly bound image.
  if(dialog.open&&!current()?.isConnected)closeViewer();
 }
 new MutationObserver(hydrate).observe($('director-anchors'),{childList:true,subtree:true});hydrate();
 function cleanup(){
  if(!active)return;active=false;loadToken++;image.onload=image.onerror=null;image.removeAttribute('src');
  document.body.style.overflow=previousOverflow;drag=null;stage.classList.remove('ux-dragging');items=[];
  if(previousFocus?.isConnected)previousFocus.focus({preventScroll:true});else $('ux-heading').focus({preventScroll:true});
 }
 function closeViewer(){if(dialog.open)dialog.close();cleanup();}
 $('ux-image-close').addEventListener('click',closeViewer);
 dialog.addEventListener('cancel',event=>{event.preventDefault();closeViewer();});
 // close is queued by the browser. Cleanup is synchronous for our actions; an
 // older queued close must never tear down a newly opened viewer.
 dialog.addEventListener('close',()=>{if(!dialog.open)cleanup();});
 $('ux-image-prev').addEventListener('click',()=>show(index-1));$('ux-image-next').addEventListener('click',()=>show(index+1));
 $('ux-image-fit').addEventListener('click',()=>{mode='fit';layout(true);});
 $('ux-image-actual').addEventListener('click',()=>{mode='manual';scale=1;layout(true);});
 function zoom(factor){mode='manual';scale=Math.max(.1,Math.min(4,scale*factor));layout();}
 $('ux-image-minus').addEventListener('click',()=>zoom(1/1.25));$('ux-image-plus').addEventListener('click',()=>zoom(1.25));
 dialog.addEventListener('keydown',e=>{
  if(e.isComposing||e.ctrlKey||e.metaKey||e.altKey)return;
  if(e.key==='Escape'){e.preventDefault();closeViewer();}
  else if(e.key==='ArrowLeft'){e.preventDefault();show(index-1);}
  else if(e.key==='ArrowRight'){e.preventDefault();show(index+1);}
  else if(e.key==='+'||e.key==='='){e.preventDefault();zoom(1.25);}
  else if(e.key==='-'){e.preventDefault();zoom(1/1.25);}
  else if(e.key==='0'){e.preventDefault();mode='fit';layout(true);}
  else if(e.key==='1'){e.preventDefault();mode='manual';scale=1;layout(true);}
 });
 stage.addEventListener('pointerdown',e=>{if(e.pointerType!=='mouse'||e.button!==0||!stage.classList.contains('ux-pannable'))return;drag={x:e.clientX,y:e.clientY,left:stage.scrollLeft,top:stage.scrollTop,id:e.pointerId};stage.setPointerCapture(e.pointerId);stage.classList.add('ux-dragging');e.preventDefault();});
 stage.addEventListener('pointermove',e=>{if(!drag||e.pointerId!==drag.id)return;stage.scrollLeft=drag.left+drag.x-e.clientX;stage.scrollTop=drag.top+drag.y-e.clientY;});
 for(const event of ['pointerup','pointercancel','lostpointercapture'])stage.addEventListener(event,()=>{drag=null;stage.classList.remove('ux-dragging');});
 new ResizeObserver(()=>layout()).observe(stage);

 // Validation metadata is supplied only by an actual failed getRequest action,
 // never guessed from a supplier response or a loaded external file.
 const issueValues=new Map();
 const messages={
  'shot-id':'给这个镜头填写一个编号，便于导出后找回。最多 120 字。',
  prompt:'写下这个镜头的画面、动作或声音，再检查适配。最多 16000 字。',
  duration:'填写 1 至 120 的整数秒，或留空。实际可用时长仍由所选模型检查。',
  resolution:'分辨率名称最多 20 字；请选择或填写明确规格。',
  ratio:'画幅名称最多 20 字；请选择或填写明确画幅。'
 };
 function clearIssue(input){
  issueValues.delete(input.id);
  const id='ux-error-'+input.id;$(id)?.setAttribute('hidden','');input.removeAttribute('aria-invalid');
  const ids=(input.getAttribute('aria-describedby')||'').split(/\s+/).filter(x=>x&&x!==id);
  if(ids.length)input.setAttribute('aria-describedby',ids.join(' '));else input.removeAttribute('aria-describedby');
 }
 for(const id of Object.keys(messages))$(id)?.addEventListener('input',e=>clearIssue(e.currentTarget));
 window.addEventListener('manju:request-error',event=>{
  const {inputId,triggerId}=event.detail||{},input=$(inputId);if(!input||!messages[inputId])return;
  let help=$('ux-error-'+inputId);if(!help){help=document.createElement('span');help.id='ux-error-'+inputId;help.className='ux-field-error';input.after(help);}
  issueValues.set(inputId,input.value);help.textContent=messages[inputId];help.hidden=false;input.setAttribute('aria-invalid','true');
  const ids=new Set((input.getAttribute('aria-describedby')||'').split(/\s+/).filter(Boolean));ids.add(help.id);input.setAttribute('aria-describedby',[...ids].join(' '));
  // Only synchronous failure of the actively pressed action moves focus. Late
  // errors never pull focus away from new writing or from an open dialog.
  if(triggerId&&document.activeElement?.id===triggerId&&!document.querySelector('dialog[open]')){window.ManjuExperience.reveal(inputId,{scroll:false,focus:true});(input.closest('label.field')||input).scrollIntoView({block:'start',behavior:'auto'});}
 });
 const issueObserver=new MutationObserver(()=>{for(const [id,value] of issueValues)if($(id).value!==value)clearIssue($(id));});
 for(const id of ['status','desk-save-status'])issueObserver.observe($(id),{childList:true,subtree:true,characterData:true});
})();
