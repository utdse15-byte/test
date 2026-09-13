/* View composition only. Never serialize navigation into creative data. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id);
 const panes=[...document.querySelectorAll('[data-scene-pane]')];
 let step='write';
 function sync(){
  const all=Boolean(window.ManjuExperience?.state().all);
  for(const p of panes)p.classList.toggle('comfort-hidden',!all&&p.dataset.scenePane!==step);
  for(const b of document.querySelectorAll('[data-scene-step]')){
   const selected=b.dataset.sceneStep===step;b.setAttribute('aria-selected',String(selected));b.tabIndex=selected?0:-1;
  }
 }
 function choose(value,focus=false){
  if(!panes.some(p=>p.dataset.scenePane===value))return;
  step=value;sync();if(focus)$('scene-step-'+step).focus({preventScroll:true});
 }
 function reveal(node){const p=node.closest('[data-scene-pane]');if(p)choose(p.dataset.scenePane);}
 document.querySelectorAll('[data-scene-step]').forEach(b=>b.addEventListener('click',()=>choose(b.dataset.sceneStep)));
 document.querySelectorAll('[data-scene-go]').forEach(b=>b.addEventListener('click',()=>{choose(b.dataset.sceneGo,true);$('scene-step-'+step).scrollIntoView({block:'nearest'});}));
 document.querySelector('.comfort-steps')?.addEventListener('keydown',event=>{
  const buttons=[...document.querySelectorAll('[data-scene-step]')],i=buttons.indexOf(document.activeElement);
  if(i<0||event.isComposing)return;
  let next=i;if(event.key==='ArrowRight')next=(i+1)%buttons.length;else if(event.key==='ArrowLeft')next=(i+buttons.length-1)%buttons.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=buttons.length-1;else return;
  event.preventDefault();choose(buttons[next].dataset.sceneStep,true);
 });
 $('story-open-shortcut')?.addEventListener('click',()=>$('story-file').click());
 $('story-create')?.addEventListener('click',()=>{if($('story-settings'))$('story-settings').open=true;});
 function summary(){
  const doc=window.ManjuStory?.view();
  const head=document.querySelector('.story-heading h2');if(head)head.textContent=doc?.title||'故事工作本';
  document.querySelector('.story-empty-start')?.classList.toggle('comfort-hidden',Boolean(doc));
 }
 for(const id of ['story-title','story-form'])$(id)?.addEventListener('input',summary);
 if($('story-count'))new MutationObserver(summary).observe($('story-count'),{childList:true,characterData:true,subtree:true});
 summary();
 const routes=document.querySelector('.studio-routes');
 if(routes){const a=document.createElement('a');a.href='#story-section';a.innerHTML='<strong>写故事与人物</strong><span>梳理剧情、关系与每场变化</span>';routes.prepend(a);}
 window.ManjuComfort={sync,reveal,choose,state:()=>({step})};sync();
})();
