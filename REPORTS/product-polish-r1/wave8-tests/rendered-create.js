
"use strict";
(function () {
  if (document.body.getAttribute("data-page") !== "/create") return;


  function reloadSoon() { setTimeout(function () { location.reload(); }, 500); }

  // -------- one editor visible at a time; ✎编辑 swaps stages in ---------
  function showStage(stage) {
    var current = document.querySelector(".cw-editor:not(.hidden) .cw-text");
    if (current && isDirty(current) && current.getAttribute("data-stage") !== stage) {
      if (!confirm("当前内容尚未保存，仍要切换吗？")) return;
    }
    var editors = document.querySelectorAll(".cw-editor");
    var found = false;
    for (var i = 0; i < editors.length; i++) {
      var match = editors[i].getAttribute("data-stage") === stage;
      /* the server marks inactive editors with the hidden CLASS — the swap
       * must clear that class on the target, not only flip the attribute
       * (the attribute alone never unhid anything; GUI polish wave). */
      editors[i].classList.toggle("hidden", !match);
      editors[i].hidden = !match;
      if (match) found = true;
    }
    var special = document.querySelector(".cw-special");
    if (special) special.hidden = found;
    var materials = document.querySelectorAll("[data-source-stage]");
    for (var j = 0; j < materials.length; j++) {
      var active = materials[j].getAttribute("data-source-stage") === stage;
      if (active) materials[j].setAttribute("aria-current", "true");
      else materials[j].removeAttribute("aria-current");
    }
    if (found) {
      var ta = document.querySelector('.cw-editor[data-stage="' + cssq(stage) + '"] .cw-text');
      if (ta) ta.focus();
    }
  }
  function cssq(s) { return String(s).replace(/["\\]/g, "\\$&"); }

  function isDirty(ta) { return !!ta && ta.value !== ta.defaultValue; }
  function updateDirty(ta) {
    if (!ta) return;
    var section = ta.closest(".cw-editor");
    var state = section && section.querySelector(".cw-save-state");
    var dirty = isDirty(ta);
    if (state) {
      state.textContent = dirty ? "未保存" : "已保存";
      state.classList.toggle("is-dirty", dirty);
    }
  }

  // ------------------------------------------ skill modal (escaped body) ---
  function openSkill(id) {
    var modal = document.getElementById("cw-modal");
    var title = document.getElementById("cw-modaltitle");
    var bodyEl = document.getElementById("cw-modalbody");
    if (!modal) return;
    title.textContent = "技能 " + id;
    bodyEl.textContent = "加载中…";
    modal.hidden = false;
    fetch("/api/create/skill?id=" + encodeURIComponent(id), {
      headers: { "X-Manju-Token": TOKEN }
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        return { status: r.status, data: d };
      });
    }).then(function (res) {
      if (res.status === 200 && res.data && typeof res.data.text === "string") {
        title.textContent = res.data.name || id;
        bodyEl.textContent = res.data.text;   // textContent = no HTML injection
      } else {
        bodyEl.textContent = (res.data && res.data.error) || "技能读取失败";
      }
    }).catch(function () {
      bodyEl.textContent = "无法连接本地服务，技能内容没有加载。";
    });
  }
  function closeModal() {
    var modal = document.getElementById("cw-modal");
    if (modal) modal.hidden = true;
  }

  // ---------------------------------------------------------- actions -----
  function doSave(stage) {
    var ta = document.querySelector('.cw-editor[data-stage="' + cssq(stage) + '"] .cw-text');
    if (!ta) return;
    var btn = document.querySelector('[data-act="save"][data-stage="' + cssq(stage) + '"]');
    if (btn && btn.disabled) return;
    if (btn) { btn.disabled = true; btn.textContent = "保存中…"; }
    post("/api/create/save", { stage: stage, text: ta.value }).then(function (res) {
      if (res.status === 200) {
        ta.defaultValue = ta.value;
        updateDirty(ta);
        toast("已保存并重新检查进度", true);
        reloadSoon();
      } else {
        if (btn) { btn.disabled = false; btn.textContent = "保存并检查进度"; }
        toast((res.data && res.data.error) || "保存失败", false);
      }
    }).catch(function () {
      if (btn) { btn.disabled = false; btn.textContent = "保存并检查进度"; }
      toast("无法连接本地服务；内容仍保留在编辑器中。", false);
    });
  }
  function doScaffold(stage) {
    var ta = document.querySelector('.cw-editor[data-stage="' + cssq(stage) + '"] .cw-text');
    if (ta && isDirty(ta) && ta.value.trim()) {
      toast("编辑器里有未保存内容；先保存当前草稿，再决定是否需要模板。", false);
      ta.focus();
      return;
    }
    var btn = document.querySelector('[data-act="scaffold"][data-stage="' + cssq(stage) + '"]');
    if (btn && btn.disabled) return;
    var original = btn ? btn.textContent : "生成模板";
    if (btn) { btn.disabled = true; btn.textContent = "生成中…"; }
    post("/api/create/scaffold", { stage: stage }).then(function (res) {
      if (res.status === 200) { toast("已生成模板 " + stage + " — 编辑它填内容", true); reloadSoon(); }
      else {
        if (btn) { btn.disabled = false; btn.textContent = original; }
        toast((res.data && res.data.error) || "生成模板失败", false);
      }
    }).catch(function () {
      if (btn) { btn.disabled = false; btn.textContent = original; }
      toast("无法连接本地服务；项目没有被修改。", false);
    });
  }
  function doCopy(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { toast("已复制:" + text, true); },
        function () { toast("复制失败,请手动选择", false); }
      );
    } else {
      toast("请手动复制:" + text, true);
    }
  }

  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-act]");
    if (!btn) return;
    var act = btn.getAttribute("data-act");
    if (act === "skill") return openSkill(btn.getAttribute("data-skill"));
    if (act === "edit") return showStage(btn.getAttribute("data-stage"));
    if (act === "save") return doSave(btn.getAttribute("data-stage"));
    if (act === "scaffold") return doScaffold(btn.getAttribute("data-stage"));
    if (act === "copy") return doCopy(btn.getAttribute("data-copy"));
    if (act === "modal-close") {
      // close only when the backdrop or an explicit close control is hit
      if (btn.id === "cw-modal" && ev.target !== btn) return;
      return closeModal();
    }
  });
  document.addEventListener("input", function (ev) {
    if (ev.target && ev.target.classList && ev.target.classList.contains("cw-text")) {
      updateDirty(ev.target);
    }
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") closeModal();
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s") {
      var ta = document.querySelector(".cw-editor:not(.hidden) .cw-text");
      if (ta) {
        ev.preventDefault();
        doSave(ta.getAttribute("data-stage"));
      }
    }
  });
  window.addEventListener("beforeunload", function (ev) {
    var textareas = document.querySelectorAll(".cw-text");
    for (var i = 0; i < textareas.length; i++) {
      if (isDirty(textareas[i])) {
        ev.preventDefault();
        ev.returnValue = "";
        return "";
      }
    }
  });
})();
