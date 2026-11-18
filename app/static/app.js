(() => {
  "use strict";
  const BOOT = JSON.parse(document.getElementById("boot").textContent);
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  // ---------- theme ----------
  function toggleTheme() {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch (e) { /* storage blocked */ }
  }

  // ---------- sticky offsets (CLAUDE.md §17: measured, not constants) ----------
  function measure() {
    const top = $("#topbar"), filter = $("#filter-form");
    if (top) document.documentElement.style.setProperty("--topbar-h", top.offsetHeight + "px");
    if (filter) document.documentElement.style.setProperty("--filter-h", filter.offsetHeight + "px");
  }
  if ("ResizeObserver" in window) {
    const ro = new ResizeObserver(measure);
    ["#topbar", "#filter-form"].forEach((s) => $(s) && ro.observe($(s)));
  } else {
    window.addEventListener("resize", measure);
  }
  measure();

  // ---------- helpers ----------
  let toastTimer;
  function toast(msg, isError = false) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.toggle("error", isError);
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), isError ? 6000 : 3500);
  }

  async function api(method, url, body) {
    const res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty body */ }
    if (!res.ok) {
      const err = new Error("request failed");
      err.errors = (data && data.errors) || { _: `요청이 실패했습니다 (${res.status})` };
      throw err;
    }
    return data;
  }

  function confirmBox(text, yesLabel = "확인") {
    const dlg = $("#confirm-modal");
    $("#confirm-text").textContent = text;
    $("#confirm-yes").textContent = yesLabel;
    return new Promise((resolve) => {
      const done = (v) => { cleanup(); dlg.close(); resolve(v); };
      const yes = () => done(true), no = () => done(false);
      const cancel = (e) => { e.preventDefault(); done(false); };
      function cleanup() {
        $("#confirm-yes").removeEventListener("click", yes);
        $("#confirm-no").removeEventListener("click", no);
        dlg.removeEventListener("cancel", cancel);
      }
      $("#confirm-yes").addEventListener("click", yes);
      $("#confirm-no").addEventListener("click", no);
      dlg.addEventListener("cancel", cancel);
      dlg.showModal();
      $("#confirm-no").focus();
    });
  }

  function showErrors(form, errors) {
    $$(".invalid", form).forEach((el) => el.classList.remove("invalid"));
    $$(".field-error", form).forEach((el) => el.remove());
    $$("[data-error]", form).forEach((el) => (el.textContent = ""));
    const general = [];
    for (const [key, msg] of Object.entries(errors || {})) {
      const m = key.match(/^log(\d+)\.(\w+)$/);
      const input = m ? null : form.elements[key];
      if (input && input.closest) {
        input.classList.add("invalid");
        const p = document.createElement("span");
        p.className = "field-error";
        p.textContent = msg;
        input.closest(".field").appendChild(p);
        const panel = input.closest("[data-panel]");
        if (panel && general.length === 0 && !form._tabShown) { showTab(form, panel.dataset.panel); form._tabShown = true; }
      } else {
        general.push(m ? `진행 이력: ${msg}` : msg);
      }
    }
    form._tabShown = false;
    const box = $("[data-error='_']", form);
    if (box) box.textContent = general.join(" ");
  }

  function fill(form, data) {
    for (const el of form.elements) {
      if (!el.name || !(el.name in data)) continue;
      if (el.type === "checkbox") el.checked = !!data[el.name];
      else el.value = data[el.name] ?? "";
    }
  }

  function read(form, names) {
    const out = {};
    for (const n of names) {
      const el = form.elements[n];
      out[n] = el.type === "checkbox" ? el.checked : el.value;
    }
    return out;
  }

  function weekOptions(select, selected, extra = []) {
    const opts = [...BOOT.weeks];
    for (const k of extra) if (!opts.some((o) => o.key === k)) opts.push({ key: k, label: k.replace(/^\d+-W0?/, "") + "주" });
    select.innerHTML = opts.map((o) => `<option value="${o.key}">${o.label}</option>`).join("");
    if (selected) select.value = selected;
  }

  function setUrlParam(key, value) {
    const u = new URL(location.href);
    if (value == null) u.searchParams.delete(key); else u.searchParams.set(key, value);
    history.replaceState(null, "", u);
  }

  // closing a dialog with unsaved changes asks first (Esc, backdrop, close buttons)
  function guardDialog(dlg, isDirty, onClosed) {
    async function tryClose() {
      if (isDirty() && !(await confirmBox("저장하지 않은 변경이 있습니다. 닫을까요?", "닫기"))) return;
      dlg.close();
      onClosed && onClosed();
    }
    dlg.addEventListener("cancel", (e) => { e.preventDefault(); tryClose(); });
    dlg.addEventListener("click", (e) => { if (e.target === dlg) tryClose(); });
    $$("[data-close]", dlg).forEach((b) => b.addEventListener("click", tryClose));
  }

  function showTab(form, name) {
    $$("[role=tab]", form).forEach((t) => t.setAttribute("aria-selected", String(t.dataset.tab === name)));
    $$("[data-panel]", form).forEach((p) => (p.hidden = p.dataset.panel !== name));
  }

  // ---------- company modal ----------
  const companyDlg = $("#company-modal"), companyForm = $("#company-form");
  const COMPANY_FIELDS = ["name", "field", "consultant", "start_date", "end_date", "kpi_mode"];
  let companyId = null, companyImpact = null, companyDirty = false;
  companyForm.addEventListener("input", () => (companyDirty = true));
  guardDialog(companyDlg, () => companyDirty);

  async function openCompany(id) {
    companyId = id;
    companyDirty = false;
    showErrors(companyForm, {});
    companyForm.reset();
    $("#company-modal-title").textContent = id ? "협력사 수정" : "협력사 추가";
    $("[data-action='company-delete']", companyForm).hidden = !id;
    if (id) {
      try {
        const data = await api("GET", `/api/companies/${id}`);
        fill(companyForm, data);
        companyImpact = data.impact;
      } catch (e) { return toast(e.errors._ || "불러오지 못했습니다", true); }
    }
    companyDlg.showModal();
    companyForm.elements.name.focus();
  }

  companyForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const body = read(companyForm, COMPANY_FIELDS);
      const res = companyId ? await api("PUT", `/api/companies/${companyId}`, body) : await api("POST", "/api/companies", body);
      companyDirty = false;
      location.href = `/c/${res.id}`;
    } catch (err) { showErrors(companyForm, err.errors); }
  });

  $("[data-action='company-delete']", companyForm).addEventListener("click", async () => {
    const name = companyForm.elements.name.value;
    const i = companyImpact || { tasks: 0, logs: 0, notes: 0 };
    const parts = [`과제 ${i.tasks}건`, `진행 이력 ${i.logs}건`, `주간 메모 ${i.notes}건`];
    const text = `「${name}」을(를) 삭제하면 ${parts.join(", ")}이 함께 삭제됩니다.\n되돌릴 수 없습니다. 정말 삭제할까요?`;
    if (!(await confirmBox(text, "삭제"))) return;
    try {
      await api("DELETE", `/api/companies/${companyId}`);
      companyDirty = false;
      location.href = "/";
    } catch (err) { showErrors(companyForm, err.errors); }
  });

  // ---------- task modal ----------
  const taskDlg = $("#task-modal"), taskForm = $("#task-form");
  const TASK_FIELDS = ["category", "sub_category", "task_type", "title", "owner", "status", "start_date", "due_date",
    "done_date", "is_added", "improve_item", "unit", "target", "before_val", "after_val", "effect", "participants",
    "to_be", "saved_hours", "saved_basis", "money_effect", "qual_effect", "check_items"];
  let task = null, logs = [], deletedLogs = new Set(), taskDirty = false, taskImpact = null;

  taskForm.addEventListener("input", () => (taskDirty = true));
  taskForm.addEventListener("change", () => (taskDirty = true));
  guardDialog(taskDlg, () => taskDirty, () => setUrlParam("task", null));
  $$("[role=tab]", taskForm).forEach((t) => t.addEventListener("click", () => showTab(taskForm, t.dataset.tab)));

  const toNum = (v) => (/^-?\d+(\.\d+)?$/.test(String(v).trim().replace(/,/g, "")) ? Number(String(v).trim().replace(/,/g, "")) : null);
  const fmt = (x) => (Math.abs(x - Math.round(x)) < 1e-9 ? Math.round(x).toLocaleString("ko-KR")
    : x.toLocaleString("ko-KR", { maximumFractionDigits: 2 }));

  function refreshEffect() {
    const f = taskForm.elements;
    const b = toNum(f.before_val.value), a = toNum(f.after_val.value);
    const auto = b !== null && a !== null;
    f.effect.readOnly = auto;
    const changed = !task || ["before_val", "after_val", "unit"].some((k) => (task[k] ?? "") !== f[k].value.trim());
    if (auto && (changed || !f.effect.value)) {
      const diff = b - a, head = (f.unit.value.split("/")[0] || "").trim();
      f.effect.value = `${fmt(Math.abs(diff))}${head}${diff > 0 ? "↓" : diff < 0 ? "↑" : ""}`;
      f.rate_view.value = b ? `${(Math.abs(diff) / b * 100).toFixed(1)}%` : "";
    } else if (!auto && changed) {
      f.rate_view.value = "";
    }
    $("#effect-hint").textContent = auto ? "개선전·개선후가 숫자라 개선효과·개선율을 자동 계산합니다."
      : "개선전·개선후가 비었거나 숫자가 아니면 개선효과를 직접 입력합니다.";
  }
  ["before_val", "after_val", "unit"].forEach((n) => taskForm.elements[n].addEventListener("input", refreshEffect));

  function refreshStatus(e) {
    const f = taskForm.elements, done = f.status.value === "완료";
    f.done_date.disabled = !done;
    if (e && done && !f.done_date.value) f.done_date.value = BOOT.today;  // only when the user picks 완료
    if (!done) f.done_date.value = "";
    const hints = [];
    if (done && (!task || task.progress !== 100)) hints.push(`저장하면 ${BOOT.currentWeek.replace(/^\d+-W0?/, "")}주차 진척률이 100%로 기록됩니다.`);
    if (task && task.status === "완료" && !done) hints.push("완료를 해제하면 실제 완료일이 비워집니다.");
    $("#status-hint").textContent = hints.join(" ");
  }
  taskForm.elements.status.addEventListener("change", refreshStatus);

  function renderLogs() {
    const body = $("#log-rows");
    $("#log-count").textContent = logs.length;
    body.innerHTML = "";
    if (!logs.length) {
      body.innerHTML = `<tr><td colspan="5" class="empty">기록이 없습니다.</td></tr>`;
      return;
    }
    for (const lg of logs) {
      const tr = document.createElement("tr");
      tr.dataset.id = lg.id;
      tr.classList.toggle("deleted", deletedLogs.has(lg.id));
      tr.innerHTML = `
        <td><select data-k="week" aria-label="주차"></select></td>
        <td><input type="number" min="0" max="100" step="1" data-k="progress" aria-label="진척률"></td>
        <td><textarea rows="2" data-k="content" aria-label="진행 내용"></textarea></td>
        <td><input data-k="issue" aria-label="이슈"></td>
        <td><button type="button" class="icon-btn" aria-label="기록 삭제" title="${deletedLogs.has(lg.id) ? "삭제 취소" : "삭제"}">${
          deletedLogs.has(lg.id) ? "↺" : document.querySelector("[data-action='task-delete'] svg").outerHTML}</button></td>`;
      weekOptions($("[data-k=week]", tr), lg.week_key, [lg.week_key]);
      $("[data-k=progress]", tr).value = lg.progress;
      $("[data-k=content]", tr).value = lg.content || "";
      $("[data-k=issue]", tr).value = lg.issue || "";
      $("button", tr).addEventListener("click", async () => {
        if (deletedLogs.has(lg.id)) deletedLogs.delete(lg.id);
        else if (await confirmBox(`${lg.week_key.replace(/^\d+-W0?/, "")}주차 기록을 삭제할까요?\n저장 버튼을 누르면 삭제됩니다.`, "삭제")) deletedLogs.add(lg.id);
        taskDirty = true;
        renderLogs();
      });
      body.appendChild(tr);
    }
  }

  function refreshNewWeekHint() {
    const wk = taskForm.elements.new_week.value;
    const hit = logs.find((l) => l.week_key === wk);
    $("#new-week-hint").textContent = hit ? "이 주차 기록이 이미 있습니다 — 저장하면 그 기록을 고칩니다." : "";
  }
  taskForm.elements.new_week.addEventListener("change", refreshNewWeekHint);

  async function openTask(id, flash) {
    taskDirty = false;
    deletedLogs = new Set();
    showErrors(taskForm, {});
    taskForm.reset();
    $("#task-flash").textContent = flash || "";
    task = null; logs = []; taskImpact = null;
    if (id) {
      try {
        const data = await api("GET", `/api/tasks/${id}`);
        task = data.task; logs = data.logs; taskImpact = data.impact;
      } catch (e) { setUrlParam("task", null); return toast(e.errors._ || "과제를 불러오지 못했습니다", true); }
      fill(taskForm, task);
      taskForm.elements.no.value = task.no;
      taskForm.elements.progress_view.value = task.progress_week
        ? `${task.progress}% · ${task.progress_week.replace(/^\d+-W0?/, "")}주차 기록` : "0% · 기록 없음";
      taskForm.elements.rate_view.value = task.rate != null ? `${(task.rate * 100).toFixed(1)}%` : "";
      $("#task-modal-title").textContent = task.title;
      setUrlParam("task", id);
    } else {
      $("#task-modal-title").textContent = "과제 등록";
      taskForm.elements.status.value = "대기";
      taskForm.elements.progress_view.value = "0% · 기록 없음";
    }
    $("[data-action='task-delete']", taskForm).hidden = !id;
    $("#log-new").hidden = !id;
    $("#log-locked").hidden = !!id;
    $(".log-table", taskForm).hidden = !id;
    weekOptions(taskForm.elements.new_week, BOOT.currentWeek);
    refreshNewWeekHint();
    renderLogs();
    refreshEffect();
    refreshStatus();
    showTab(taskForm, "basic");
    taskDlg.showModal();
    taskForm.elements.title.focus();
  }

  taskForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = taskForm.elements;
    const upsert = logs.filter((l) => !deletedLogs.has(l.id)).map((l) => {
      const tr = $(`#log-rows tr[data-id="${l.id}"]`);
      return { id: l.id, week: $("[data-k=week]", tr).value, progress: $("[data-k=progress]", tr).value,
               content: $("[data-k=content]", tr).value, issue: $("[data-k=issue]", tr).value, _orig: l };
    }).filter((x) => x.week !== x._orig.week_key || Number(x.progress) !== x._orig.progress
      || x.content !== (x._orig.content || "") || x.issue !== (x._orig.issue || ""))
      .map(({ _orig, ...rest }) => rest);
    if (task && (f.new_progress.value !== "" || f.new_content.value.trim() || f.new_issue.value.trim())) {
      if (f.new_progress.value === "") {
        return showErrors(taskForm, { _: "새 기록의 진척률을 입력하세요 (진행 이력 탭)." }), showTab(taskForm, "logs");
      }
      upsert.push({ week: f.new_week.value, progress: f.new_progress.value, content: f.new_content.value, issue: f.new_issue.value });
    }
    const t = read(taskForm, TASK_FIELDS);
    t.done_date = f.done_date.disabled ? "" : t.done_date;
    const payload = { task: t, logs: { upsert, delete: [...deletedLogs] } };
    try {
      const res = task ? await api("PUT", `/api/tasks/${task.id}`, payload)
        : await api("POST", "/api/tasks", { ...payload, company_id: BOOT.companyId });
      taskDirty = false;
      const msg = ["저장되었습니다", ...(res.notices || [])].join(" · ");
      try { sessionStorage.setItem("flash", msg); } catch (err) { /* storage blocked */ }
      const u = new URL(location.href);
      u.searchParams.set("task", res.id);
      location.replace(u);  // re-render cards and list; the modal reopens from ?task=
    } catch (err) { showErrors(taskForm, err.errors); }
  });

  $("[data-action='task-delete']", taskForm).addEventListener("click", async () => {
    const n = (taskImpact && taskImpact.logs) || 0;
    if (!(await confirmBox(`「${task.title}」 과제를 삭제하면 진행 이력 ${n}건이 함께 삭제됩니다.\n되돌릴 수 없습니다. 정말 삭제할까요?`, "삭제"))) return;
    try {
      await api("DELETE", `/api/tasks/${task.id}`);
      taskDirty = false;
      setUrlParam("task", null);
      try { sessionStorage.setItem("toast", `「${task.title}」 과제를 삭제했습니다`); } catch (err) { /* */ }
      location.reload();
    } catch (err) { showErrors(taskForm, err.errors); }
  });

  // ---------- weekly note ----------
  const noteDlg = $("#note-modal"), noteForm = $("#note-form");
  const NOTE_FIELDS = ["report_date", "summary", "common", "detail", "next_visit_date", "next_visit_time", "next_plan"];
  let noteDirty = false;
  noteForm.addEventListener("input", (e) => { if (e.target.name !== "week") noteDirty = true; });
  guardDialog(noteDlg, () => noteDirty);

  async function loadNote() {
    const wk = noteForm.elements.week.value;
    showErrors(noteForm, {});
    $("#note-flash").textContent = "";
    for (const n of NOTE_FIELDS) noteForm.elements[n].value = "";
    try {
      const { note } = await api("GET", `/api/companies/${BOOT.companyId}/notes/${wk}`);
      if (note) fill(noteForm, note);
      else noteForm.elements.report_date.value = BOOT.today;
      $("#note-flash").textContent = note ? "" : "새 메모";
    } catch (e) { showErrors(noteForm, e.errors); }
    noteDirty = false;
  }
  noteForm.elements.week.addEventListener("change", async (e) => {
    if (noteDirty && !(await confirmBox("저장하지 않은 변경이 있습니다. 다른 주차로 바꿀까요?", "바꾸기"))) {
      e.target.value = e.target.dataset.prev; return;
    }
    e.target.dataset.prev = e.target.value;
    loadNote();
  });

  async function openNote() {
    weekOptions(noteForm.elements.week, BOOT.currentWeek);
    noteForm.elements.week.dataset.prev = BOOT.currentWeek;
    noteDlg.showModal();
    await loadNote();
  }

  noteForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("PUT", `/api/companies/${BOOT.companyId}/notes/${noteForm.elements.week.value}`, read(noteForm, NOTE_FIELDS));
      noteDirty = false;
      $("#note-flash").textContent = "저장되었습니다";
    } catch (err) { showErrors(noteForm, err.errors); }
  });

  // ---------- report ----------
  const reportDlg = $("#report-modal");
  guardDialog(reportDlg, () => false);
  async function checkReport() {
    const wk = $("#report-week").value, info = $("#report-info"), link = $("#report-download");
    info.textContent = "확인 중…";
    link.setAttribute("aria-disabled", "true");
    try {
      const r = await api("GET", `/api/companies/${BOOT.companyId}/report?week=${wk}`);
      const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
      info.innerHTML = `<div><strong>${esc(r.filename)}</strong></div>
        <div class="muted">시트 ${r.sheets.length}개: ${r.sheets.map(esc).join(", ")}</div>` +
        (r.warnings.length ? `<div class="text-warning">확인 필요 ${r.warnings.length}건</div><ul>${r.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : "");
      link.href = `/c/${BOOT.companyId}/report.xlsx?week=${wk}`;
      link.removeAttribute("aria-disabled");
    } catch (e) { info.textContent = e.errors._ || "보고서를 만들 수 없습니다"; }
  }
  $("#report-week").addEventListener("change", checkReport);
  function openReport() {
    weekOptions($("#report-week"), BOOT.currentWeek);
    reportDlg.showModal();
    checkReport();
  }

  // ---------- progress form download / upload ----------
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const formDlg = $("#form-modal");
  guardDialog(formDlg, () => false);
  function setFormLink() {
    $("#form-download").href = `/c/${BOOT.companyId}/progress-form.xlsx?week=${$("#form-week").value}`;
  }
  $("#form-week").addEventListener("change", setFormLink);
  function openForm() {
    weekOptions($("#form-week"), BOOT.currentWeek);
    setFormLink();
    formDlg.showModal();
  }

  const uploadDlg = $("#upload-modal"), uploadFile = $("#upload-file"), uploadApply = $("#upload-apply");
  let uploadBytes = null, uploadPending = false;
  guardDialog(uploadDlg, () => uploadPending);

  async function sendUpload(apply) {
    const res = await fetch(`/api/companies/${BOOT.companyId}/progress-upload?apply=${apply ? 1 : 0}`, {
      method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: uploadBytes,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data.errors && data.errors._) || `요청 실패 (${res.status})`);
    return data;
  }

  function renderPreview(r) {
    const wk = r.week.replace(/^\d+-W0?/, "");
    const rows = r.changes.map((c) => {
      const b = c.before || {};
      const cell = (k, suffix = "") => (c.before && b[k] !== c.after[k] && b[k] != null && b[k] !== ""
        ? `<span class="old-value">${esc(b[k])}${suffix}</span><br>` : "") + esc(c.after[k] ?? "") + (c.after[k] != null ? suffix : "");
      return `<tr><td>${c.no}</td><td>${esc(c.title)}</td><td>${c.kind === "new" ? "새 기록" : "수정"}</td>
        <td>${cell("progress", "%")}</td><td>${cell("content")}</td><td>${cell("issue")}</td></tr>`;
    }).join("");
    $("#upload-preview").innerHTML =
      `<div><strong>${wk}주차</strong> · 반영 ${r.changes.length}건 · 변화 없음 ${r.unchanged}건 · 빈 줄 ${r.blank}건` +
      (r.errors.length ? ` · <span class="text-danger">오류 ${r.errors.length}건</span>` : "") + `</div>` +
      (r.errors.length ? `<ul class="text-danger">${r.errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>
        <div class="text-danger">오류가 있으면 반영할 수 없습니다. 엑셀을 고친 뒤 다시 선택하세요.</div>` : "") +
      (r.changes.length ? `<table class="preview-table"><thead><tr><th>NO</th><th>과제명</th><th>구분</th>
        <th>진척률</th><th>진행 내용</th><th>이슈</th></tr></thead><tbody>${rows}</tbody></table>` : "<div class=\"muted\">반영할 변경이 없습니다.</div>");
    uploadPending = !r.errors.length && r.changes.length > 0;
    uploadApply.disabled = !uploadPending;
  }

  uploadFile.addEventListener("change", async () => {
    uploadApply.disabled = true;
    uploadPending = false;
    const file = uploadFile.files[0];
    if (!file) return;
    $("#upload-preview").textContent = "확인 중…";
    try {
      uploadBytes = await file.arrayBuffer();
      renderPreview(await sendUpload(false));
    } catch (err) {
      $("#upload-preview").innerHTML = `<div class="text-danger">${esc(err.message)}</div>`;
    }
  });

  uploadApply.addEventListener("click", async () => {
    uploadApply.disabled = true;
    try {
      const r = await sendUpload(true);
      uploadPending = false;
      const msg = [`진행사항 ${r.changes.length}건을 반영했습니다`, ...r.notices].join("\n");
      try { sessionStorage.setItem("toast", msg); } catch (e) { /* */ }
      location.reload();
    } catch (err) {
      $("#upload-preview").insertAdjacentHTML("afterbegin", `<div class="text-danger">${esc(err.message)}</div>`);
    }
  });

  function openUpload() {
    uploadFile.value = "";
    uploadBytes = null;
    uploadPending = false;
    uploadApply.disabled = true;
    $("#upload-preview").innerHTML = "";
    uploadDlg.showModal();
  }

  // ---------- actions ----------
  document.addEventListener("click", async (e) => {
    const el = e.target.closest("[data-action]");
    if (!el) return;
    const a = el.dataset.action;
    if (a === "theme") toggleTheme();
    else if (a === "company-new") openCompany(null);
    else if (a === "company-edit") openCompany(Number(el.dataset.id));
    else if (a === "task-open") openTask(Number(el.dataset.id));
    else if (a === "task-new") openTask(null);
    else if (a === "note-open") openNote();
    else if (a === "report-open") openReport();
    else if (a === "form-open") openForm();
    else if (a === "upload-open") openUpload();
    else if (a === "backup") {
      el.disabled = true;
      try {
        const r = await api("POST", "/api/backup");
        toast(r.ok ? `백업 완료 (무결성 ${r.integrity})\n${r.path}` : `[ERROR] 백업 검증 실패: ${r.integrity}`, !r.ok);
      } catch (err) { toast("백업 실패", true); }
      el.disabled = false;
    }
  });

  $$("[data-autosubmit]").forEach((s) => s.addEventListener("change", () => s.form.requestSubmit()));
  const filterForm = $("#filter-form");
  if (filterForm) {
    filterForm.addEventListener("submit", () => {
      $$("input, select", filterForm).forEach((el) => { if (!el.value) el.disabled = true; });  // keep URLs short
    });
  }

  // reopen the task modal after save / on reload (?task=)
  let flash = null, msg = null;
  try { flash = sessionStorage.getItem("flash"); sessionStorage.removeItem("flash");
        msg = sessionStorage.getItem("toast"); sessionStorage.removeItem("toast"); } catch (e) { /* */ }
  if (msg) toast(msg);
  const tid = new URL(location.href).searchParams.get("task");
  if (tid && BOOT.companyId) openTask(Number(tid), flash);
})();
