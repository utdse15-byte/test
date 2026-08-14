
"use strict";
(function () {

  function reloadSoon() { setTimeout(function () { location.reload(); }, 500); }

  if (document.body.getAttribute("data-page") !== "/director") return;

  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-act]");
    if (btn) {
      var act = btn.getAttribute("data-act");
      if (act === "confirm") return doConfirm(btn.getAttribute("data-id"), btn);
      if (act === "run") return doRun(btn.getAttribute("data-id"), btn);
      if (act === "reject") return doReject(btn.getAttribute("data-id"), btn);
      if (act === "suggest-propose") return doSuggestPropose(btn);
      return;
    }
    if (ev.target.id === "dg-propose") return doCompose();
  });

  function doConfirm(id, btn) {
    if (btn) { btn.disabled = true; btn.textContent = "确认中…"; }
    post("/api/director/confirm", { id: id }).then(function (res) {
      if (res.status === 200) { toast("已确认提案；还需要单独执行", true); reloadSoon(); }
      else {
        if (btn) { btn.disabled = false; btn.textContent = "确认这份提案"; }
        toast((res.data && res.data.error) || "确认失败", false);
      }
    }).catch(function () {
      if (btn) { btn.disabled = false; btn.textContent = "确认这份提案"; }
      toast("连接本地服务失败", false);
    });
  }
  function doRun(id, btn) {
    if (btn) { btn.disabled = true; btn.textContent = "执行中…"; }
    post("/api/director/run", { id: id }).then(function (res) {
      if (res.status === 200) {
        var ok = res.data && res.data.ok;
        toast((ok ? "执行完成 " : "执行失败(第一处失败即停) ") + id, ok);
        reloadSoon();
      } else {
        if (btn) { btn.disabled = false; btn.textContent = "执行已确认提案"; }
        toast((res.data && res.data.error) || "执行失败", false);
      }
    }).catch(function () {
      if (btn) { btn.disabled = false; btn.textContent = "执行已确认提案"; }
      toast("连接本地服务失败", false);
    });
  }
  function doReject(id, btn) {
    if (!confirm("否决这份提案并保留记录？")) return;
    if (btn) { btn.disabled = true; btn.textContent = "否决中…"; }
    post("/api/director/reject", { id: id }).then(function (res) {
      if (res.status === 200) { toast("已否决并保留记录", true); reloadSoon(); }
      else {
        if (btn) { btn.disabled = false; btn.textContent = "否决并保留记录"; }
        toast((res.data && res.data.error) || "否决失败", false);
      }
    }).catch(function () {
      if (btn) { btn.disabled = false; btn.textContent = "否决并保留记录"; }
      toast("连接本地服务失败", false);
    });
  }
  function doSuggestPropose(btn) {
    var payload;
    try { payload = JSON.parse(btn.getAttribute("data-payload")); }
    catch (e) { toast("建议数据损坏", false); return; }
    btn.disabled = true;
    var original = btn.textContent;
    btn.textContent = "创建中…";
    post("/api/director/propose", payload).then(function (res) {
      if (res.status === 200) { toast("已创建可审阅提案", true); reloadSoon(); }
      else {
        btn.disabled = false; btn.textContent = original;
        toast((res.data && res.data.error) || "提案失败", false);
      }
    }).catch(function () {
      btn.disabled = false; btn.textContent = original;
      toast("连接本地服务失败", false);
    });
  }
  function doCompose() {
    var ta = document.getElementById("dg-actions");
    var why = document.getElementById("dg-why");
    var actions;
    try { actions = JSON.parse(ta.value); }
    catch (e) { toast("actions 不是合法 JSON", false); return; }
    if (!Array.isArray(actions) || !actions.length) { toast("actions 需为非空数组", false); return; }
    var btn = document.getElementById("dg-propose");
    if (btn) { btn.disabled = true; btn.textContent = "创建中…"; }
    post("/api/director/propose", { actions: actions, why: why ? why.value : "" })
      .then(function (res) {
        if (res.status === 200) { toast("已创建提案", true); reloadSoon(); }
        else {
          if (btn) { btn.disabled = false; btn.textContent = "创建提案"; }
          toast((res.data && res.data.error) || "提案失败", false);
        }
      }).catch(function () {
        if (btn) { btn.disabled = false; btn.textContent = "创建提案"; }
        toast("连接本地服务失败", false);
      });
  }
})();
