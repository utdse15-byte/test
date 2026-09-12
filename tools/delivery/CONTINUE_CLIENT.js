
/* MANJU_CONTINUE_CLIENT */
(() => {
  'use strict';
  const cfg = window.MANJU_CONTINUE_CONFIG;
  if (!cfg || !window.ManjuDesk || !window.ManjuWorkbench || !window.ManjuStudio || ManjuDesk.restoreSourceVersion !== 1) return;
  const $ = id => document.getElementById(id);
  const panel = document.createElement('section');
  panel.className = 'panel'; panel.id = 'continue-panel';
  panel.innerHTML = `<h2>本机续作 <span class="tag">可选 · 不上传</span></h2>
<p class="hint">误刷新或重新启动后，从下方恢复点找回工作。只保存三个工作区和已载入的待处理材料，不备份主影片目录。文件在用户主目录的 <code>ManjuRecovery</code>，不是浏览器缓存，也不是独立备份。</p>
<label class="checkline"><input id="continue-auto" type="checkbox">在本窗口开启自动续作：停笔后保存，每次成功后只保留本窗口最近 3 份；不会清理其他窗口。总容量 2 GiB，最多 64 份，达到上限会暂停。</label>
<div class="actions"><button type="button" id="continue-save" class="primary">立即存一份恢复点</button><button type="button" id="continue-list" aria-controls="continue-points" aria-expanded="false">查看已有恢复点</button></div>
<p id="continue-status" class="status" role="status" aria-live="polite">尚未开启自动续作。只有点击保存或主动勾选后才写入。仍可正常使用首页收工包。</p>
<div id="continue-points" hidden role="region" aria-label="已有恢复点"><div class="actions"><h3>已有恢复点：核验、取回或另存</h3><button type="button" id="continue-hide" aria-controls="continue-points">收起列表</button></div>
<p id="continue-usage" class="hint"></p>
<div class="actions"><button type="button" id="continue-index">读取内容摘要（只读）</button><button type="button" id="continue-index-stop" hidden>停止读取后续摘要</button></div>
<p id="continue-index-status" class="hint" role="status" aria-live="polite">摘要只在主动点击后读取，不上传、不恢复、不改动原文件。大型恢复点需要读取完整校验值。</p>
<label class="field">筛选已读取的内容<input id="continue-search" type="search" maxlength="240" placeholder="镜头名、方案名、提示词、返工或导演要求"></label>
<p id="continue-filter-status" class="hint" role="status"></p>
<div id="continue-summary" class="warningbox" hidden></div><label class="field">选择一份恢复点<select id="continue-choice"><option value="">尚未读取</option></select></label>
<div class="actions"><button type="button" id="continue-preview" disabled>核验并预览恢复</button><button type="button" id="continue-download" disabled>另存收工 ZIP</button><button type="button" id="continue-delete" class="danger" disabled>删除所选恢复点</button><button type="button" id="continue-clean" hidden>清理中断的临时写入</button></div>
<p class="hint">列表不代表已解码视频。恢复仍使用原来的完整核验和明确确认；不会自动接受意见、选片或生成。删除前先下载需要的副本。</p></div>
<p class="warningbox">同一块硬盘上的恢复点不能防止硬盘损坏、目录误删或机器丢失。收工时仍应下载收工 ZIP 并选回核验。保存尚未成功、正在输入或服务已停止时，不保证最后修改可恢复。</p>`;
  document.querySelector('main').prepend(panel);
  const styles = document.createElement('style');
  styles.textContent = '#continue-panel{overflow-wrap:anywhere}#continue-panel select{max-width:100%;min-width:0}#continue-panel code{overflow-wrap:anywhere}#continue-panel .actions{display:flex;flex-wrap:wrap}#continue-panel .actions button{min-width:0;white-space:normal}#continue-panel .checkline{line-height:1.65}#continue-points h3{flex:1;min-width:160px;margin:.4em 0}#continue-hide{flex:0 0 auto;min-width:88px;white-space:nowrap!important}';
  document.head.append(styles);
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  const windowId = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
  const local = {busy:false, sequence:0, last:null, observed:ManjuDesk.fingerprint(),
    changed:Date.now(), attempted:0, points:[], summaries:new Map(), summaryErrors:new Map(), scanning:false, stopScan:false, noticeAfterBusy:false, localPending:null, lastPoint:null};
  const note = (text, error=false) => { $('continue-status').textContent = text; $('continue-status').classList.toggle('error', error); };
  const fingerprint = () => ManjuDesk.fingerprint();
  const humanSize = n => (n / 1024 / 1024).toFixed(2) + ' MiB';
  function syncButtons() {
    const selected = local.points.some(p => p.id === $('continue-choice').value);
    for (const id of ['continue-preview','continue-download','continue-delete']) $(id).disabled=local.busy||!selected;
    $('continue-save').disabled=local.busy; $('continue-list').disabled=local.busy; $('continue-clean').disabled=local.busy;
    $('continue-index').disabled=local.busy||!local.points.length;
    $('continue-index-stop').hidden=!local.scanning;
  }
  function observed() {
    const value=fingerprint(),changed=value!==local.observed;
    if(changed){local.observed=value;local.changed=Date.now();local.noticeAfterBusy=true;}
    if(!local.busy&&local.noticeAfterBusy){
      local.noticeAfterBusy=false;
      if(value!==local.last)note($('continue-auto').checked?'有新修改，等待停笔后写入本机恢复点。尚未保存这份新输入。':'有新修改尚未存入恢复点；可立即保存，或下载首页收工包。');
    }
    return value;
  }
  async function hashBlob(blob) {
    const h=new ManjuWorkbench.SHA256();
    for(let i=0;i<blob.size;i+=1024*1024)h.update(new Uint8Array(await blob.slice(i,i+1024*1024).arrayBuffer()));
    return h.hex();
  }
  async function request(path, options={}, format='json') {
    // Own the complete body read, not only HTTP headers. A stalled response must
    // release busy state and never become a successful save after a timeout.
    const controller=new AbortController();let timer;
    const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>{
      controller.abort();reject(new Error('本机响应超时，未确认完成；服务可能已经写入，请重新查看恢复点'));
    },cfg.request_timeout_ms||180000);});
    const operation=(async()=>{
      const response=await fetch(cfg.base+path,{...options,cache:'no-store',redirect:'error',credentials:'same-origin',signal:controller.signal,
        headers:{'X-Manju-Token':cfg.token,...options.headers}});
      if(!response.ok){const payload=await response.json().catch(()=>({}));throw new Error(payload.error||'本机服务未完成请求');}
      return await (format==='blob'?response.blob():response.json());
    })();
    try{return await Promise.race([operation,timeout]);}finally{clearTimeout(timer);}
  }
  const summaryKey = record => record.id+':'+record.sha256;
  const taskNames = {create:'从零创建',animate:'静帧动画',bridge:'连接首尾帧',edit:'视频编辑',extend:'视频延展',reference:'参考生成',perform:'表演参考'};
  function showSummary() {
    const record=local.points.find(p=>p.id===$('continue-choice').value);
    const box=$('continue-summary');box.replaceChildren();box.hidden=!record;
    if(!record)return;
    const value=local.summaries.get(summaryKey(record));
    if(!value){box.textContent=local.summaryErrors.has(summaryKey(record))?'该恢复点的摘要未通过核验。原文件保留，不代表无法由旧版恢复；可先另存检查。':'尚未读取这份内容；点击上方“读取内容摘要”后再筛选。';return;}
    const line=(label,text)=>{const row=document.createElement('p');row.textContent=label+'：'+text;box.append(row);};
    line('镜头',value.shot_id.trim()||'未命名');
    if(value.branch_name.trim())line('方案',value.branch_name);
    line('任务',taskNames[value.task]||value.task||'未指定');
    line('内容摘录',value.prompt||'尚未填写');
    line('素材与审片',`${value.media_files} 份素材 · ${humanSize(value.media_bytes)} · ${value.candidates} 个候选 · ${value.review_records} 条历史审片`);
    line('返工',value.has_repair_video?'已含原视频'+(value.repair_note?' · '+value.repair_note:''):'未绑定原视频'+(value.repair_note?' · '+value.repair_note:''));
    line('导演材料',`${value.has_director_video?'已含运动底片':'未绑定运动底片'} · ${value.director_anchors} 个关键时刻`+(value.director_note?' · '+value.director_note:''));
    line('尚未处理',`${value.pending_external_edit?'含外部改稿':'无外部改稿'}；${value.pending_template?'含个人模板 '+value.template_name:'无待处理模板'}`);
    line('范围','仅显示已校验字节中的文字摘要，长文会截短；没有解码视频、恢复现场或确认独立备份。');
  }
  function drawChoices(preferred=$('continue-choice').value) {
    const query=$('continue-search').value.trim().toLocaleLowerCase();
    const visible=local.points.filter(p=>{
      if(!query)return true;
      const value=local.summaries.get(summaryKey(p));
      return value&&[value.shot_id,value.branch_name,value.prompt,value.repair_note,value.director_note,value.template_name,p.created_utc].join('\n').toLocaleLowerCase().includes(query);
    });
    $('continue-choice').replaceChildren(...visible.map(p=>{
      const value=local.summaries.get(summaryKey(p)),o=document.createElement('option');o.value=p.id;
      o.textContent=(value?((value.shot_id.trim()||'未命名')+(value.branch_name.trim()?' / '+value.branch_name.trim():'')):'未读内容')+' · '+new Date(p.created_utc).toLocaleString()+' · '+humanSize(p.bytes)+' · 窗口 '+p.window.slice(0,8);
      return o;
    }));
    if(!visible.length){const o=document.createElement('option');o.value='';o.textContent=local.points.length?'没有匹配的已读摘要':'没有已发布的恢复点';$('continue-choice').append(o);}
    if(visible.some(p=>p.id===preferred))$('continue-choice').value=preferred;
    const unread=local.points.filter(p=>!local.summaries.has(summaryKey(p))).length;
    $('continue-filter-status').textContent=`显示 ${visible.length} / ${local.points.length} 份；${unread} 份尚无可用摘要。筛选只查已读短摘要，不搜索完整提示词或媒体内容。`;
    showSummary();syncButtons();
  }
  async function refresh(open=false) {
    const data=await request('points');
    const prior=$('continue-choice').value;local.points=data.points;
    const current=new Set(data.points.map(summaryKey));
    for(const map of [local.summaries,local.summaryErrors])for(const key of map.keys())if(!current.has(key))map.delete(key);
    $('continue-usage').textContent=`已存 ${data.points.length} 份，使用 ${humanSize(data.bytes_used)} / ${humanSize(data.maximum_bytes)}。`+
      (data.damaged.length?`有 ${data.damaged.length} 份不完整目录，保留未覆盖；请在恢复目录另存检查。`:'')+
      (data.unfinished?`上次中断留下 ${data.unfinished} 个临时写入，可明确清理；已完成恢复点不受影响。`:'');
    $('continue-clean').hidden=!data.unfinished;
    if(open){$('continue-points').hidden=false;$('continue-list').setAttribute('aria-expanded','true');}
    drawChoices(prior);return data;
  }
  async function readSummaries() {
    if(local.busy||ManjuDesk.isBusy())return;
    local.busy=true;local.scanning=true;local.stopScan=false;syncButtons();
    $('continue-index-status').textContent='开始重新读取恢复点列表与摘要，先前结果不是本次核验完成。';
    let done=0,failed=0;
    try{
      await refresh(true);
      const pending=local.points.slice();
      for(const record of pending){
        if(local.stopScan)break;
        $('continue-index-status').textContent=`正在核验摘要 ${done+1} / ${pending.length}。可以继续编辑；本次读取不写入恢复点。`;
        try{
          const result=await request('points/'+record.id+'/summary',{headers:{'X-Manju-Sha256':record.sha256}});
          if(result.point?.id!==record.id||result.point?.sha256!==record.sha256||result.point?.bytes!==record.bytes||result.summary?.schema_id!=='manju.recovery-summary/v1'||result.summary.restored!==false||result.summary.independent_backup!==false)throw new Error('摘要与所选恢复点不符');
          if(local.points.some(p=>summaryKey(p)===summaryKey(record))){local.summaries.set(summaryKey(record),result.summary);local.summaryErrors.delete(summaryKey(record));}
        }catch(error){failed++;local.summaries.delete(summaryKey(record));local.summaryErrors.set(summaryKey(record),String(error.message));}
        done++;drawChoices();
      }
      $('continue-index-status').textContent=(local.stopScan?'已停止读取后续摘要。':'摘要读取完成。')+`本次读取 ${done} 份，${failed} 份未能核验。原文件和当前工作不变；刷新页面后需重新读取摘要。`;
    }catch(error){$('continue-index-status').textContent='摘要未完成：'+error.message+'。当前工作和原文件未改变。';}
    finally{local.busy=false;local.scanning=false;syncButtons();observed();}
  }
  async function save({automatic=false}={}) {
    if(automatic&&!$('continue-auto').checked)return;
    if(local.busy||ManjuDesk.isBusy()||ManjuStudio.state.busy||ManjuWorkbench.state.busy||window.ManjuFlex?.isBusy()||window.ManjuExchange?.isBusy())return;
    local.busy=true;local.attempted=Date.now();syncButtons();
    const initial=observed();
    note('正在生成完整收工包并写入本机；此刻仍不能声称已保存。');
    try {
      ManjuDesk.state.busy=true;
      const built=await ManjuDesk.build();
      const sha=await hashBlob(built.blob);
      if(initial!==fingerprint())throw new Error('写入前工作已经变化，本次未上传旧现场；停笔后重试。');
      if(automatic&&!$('continue-auto').checked){note('自动续作已关闭，本次尚未上传的现场没有写入。已有恢复点保留。');return;}
      const result=await request('points',{method:'POST',body:built.blob,headers:{'Content-Type':'application/zip',
        'X-Manju-Window':windowId,'X-Manju-Sequence':String(++local.sequence),'X-Manju-Sha256':sha}});
      if(result.point.sha256!==sha||result.point.bytes!==built.blob.size)throw new Error('服务回执与实际收工包不一致；未标记成功。');
      local.last=initial;local.lastPoint=result.point;
      note(initial===fingerprint()?`已写入并核对本机恢复点 · ${new Date(result.point.created_utc).toLocaleTimeString()}。这不是独立下载备份。`:
        '上一份现场已写入本机，但保存期间又有新输入；新内容尚未保存。');
      if(result.warnings.length)note('新恢复点已写入，旧点清理未完成，暂时保留。可在列表另存和删除。');
      try{await refresh();}catch(_){note('恢复点写入已确认，但列表刷新失败。当前稿件未改变；可重试读取。',true);}
    } catch(error) {
      if(!/工作已经变化|期间.*改变|已变化/.test(error.message))$('continue-auto').checked=false;
      note('续作未完成：'+error.message+' 请保留当前页面并另存收工包；素材未齐时，先用外部工具往返区导出文字。旧恢复点不因失败先行删除。',true);
    } finally {
      ManjuDesk.state.busy=false;local.busy=false;local.attempted=Date.now();ManjuDesk.changed();syncButtons();
    }
  }
  async function fetchPoint(record) {
    const blob=await request('points/'+record.id,{},'blob');
    if(blob.size!==record.bytes||await hashBlob(blob)!==record.sha256)throw new Error('读取的实际字节与所选恢复点不符；当前工作未改变。');
    return blob;
  }
  async function selectedAction(kind) {
    if(local.busy||ManjuDesk.isBusy())return;
    const record=local.points.find(p=>p.id===$('continue-choice').value);if(!record)return;
    if(kind==='delete'&&!confirm('只删除所选本机恢复点？此操作不可撤回，不修改当前页面和已下载的备份。需要保留时请先另存收工 ZIP。'))return;
    const before=fingerprint();local.busy=true;syncButtons();
    try{
      if(kind==='delete'){
        await request('points/'+record.id,{method:'DELETE',headers:{'X-Manju-Sha256':record.sha256}});
        if(local.lastPoint?.id===record.id){local.last=null;local.lastPoint=null;$('continue-auto').checked=false;}
        await refresh(true);note('所选恢复点已删除，当前页面与其他恢复点未改变。若删除了本窗口最新点，自动续作已暂停。');return;
      }
      const blob=await fetchPoint(record);
      if(kind==='download'){
        ManjuWorkbench.download(blob,'MANJU_DESK_'+record.id+'.zip');
        note('已发起所选恢复点的收工 ZIP 下载。请到下载列表保存，再用首页收工核验选回；尚未确认你保存到了独立位置。');return;
      }
      if(before!==fingerprint())throw new Error('读取期间有新输入，未用旧恢复点覆盖。请重新预览。');
      if(ManjuDesk.isBusy())throw new Error('另一个文件操作正在进行，请稍后重试。');
      ManjuDesk.state.busy=true;
      try{await ManjuDesk.preview(blob,{source:"local_recovery"});}
      finally{ManjuDesk.state.busy=false;}
      note('已核验本机恢复点。当前工作尚未替换，请在原收工恢复区查看影响并明确确认。');
    }catch(error){note('未完成：'+error.message,true);}
    finally{local.busy=false;syncButtons();ManjuDesk.changed();}
  }
  $('continue-save').addEventListener('click',()=>save());
  $('continue-list').addEventListener('click',()=>refresh(true).catch(e=>note('无法读取恢复点：'+e.message,true)));
  $('continue-choice').addEventListener('change',()=>{showSummary();syncButtons();});
  $('continue-search').addEventListener('input',()=>drawChoices());
  $('continue-hide').addEventListener('click',()=>{$('continue-points').hidden=true;$('continue-list').setAttribute('aria-expanded','false');$('continue-list').focus();});
  $('continue-index').addEventListener('click',readSummaries);
  $('continue-index-stop').addEventListener('click',()=>{local.stopScan=true;$('continue-index-status').textContent='已请求停止；当前一次校验结束或超时后，不再读取下一份。';});
  for(const [id,kind]of [['continue-preview','preview'],['continue-download','download'],['continue-delete','delete']])$(id).addEventListener('click',()=>selectedAction(kind));
  $('continue-clean').addEventListener('click',async()=>{
    if(local.busy||!confirm('仅清理中断后尚未发布的临时写入？已完成恢复点和当前页面不变。'))return;
    local.busy=true;syncButtons();
    try{const data=await request('cleanup',{method:'POST',body:''});await refresh(true);note('已清理 '+data.removed+' 个未完成写入目录。');}
    catch(e){note('未清理：'+e.message,true);}finally{local.busy=false;syncButtons();}
  });
  $('continue-auto').addEventListener('change',()=>{
    observed();
    note($('continue-auto').checked?'本窗口已开启自动续作。停笔约 8 秒后尝试写入，相邻自动保存至少间隔 30 秒。尚未写入本次工作。':'本窗口自动续作已关闭。已经发出的写入可能完成；已有恢复点仍保留。');
  });
  // Save while the page is alive, never promise an unload/close-time async save.
  function tick(){
    const current=observed();
    if($('continue-auto').checked&&!local.busy&&current!==local.last&&Date.now()-local.changed>=cfg.auto_delay_ms&&Date.now()-local.attempted>=cfg.minimum_gap_ms)save({automatic:true});
  }
  let timer=null;
  function watch(){if(timer===null)timer=setInterval(tick,1000);}
  window.addEventListener('pagehide',()=>{clearInterval(timer);timer=null;});
  window.addEventListener('pageshow',()=>{local.changed=Date.now();watch();});
  watch();
  refresh().catch(e=>note('续作目录暂不可读：'+e.message+' 普通收工下载仍可使用。',true));
  window.ManjuContinuation={state:local,save,refresh,readSummaries,drawChoices,hashBlob,selectedAction,windowId,isWatching:()=>timer!==null};
})();
