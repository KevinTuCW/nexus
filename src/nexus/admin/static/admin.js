"use strict";
/*
  The control plane's behaviour.

  No credential in this file and none in the URL. The session lives in an
  HttpOnly cookie that this script cannot read -- which is the point: a script
  injected here could not exfiltrate it either.

  Two structural rules the whole file follows:

  1. **Every operation opens from the row it acts on**, with the subject filled
     in and not editable. The forms that used to sit under each list are gone.
     Picking the row and then picking the subject again from a dropdown are two
     independent chances to act on the wrong business line, and both of them
     write to the effective policy.

  2. **A failed write renders inside its dialog**, next to the values that
     caused it, which stay in their boxes. The previous version raised a toast
     and reset the form, so reading the error meant retyping everything first.

  Handlers are attached by delegation on `data-act`, never by writing
  `onclick="fn('<%= value %>')"` into a template string. A tenant or label
  containing a quote used to be able to break out of that attribute.
*/

const NANO = 1e9;
const H = { "Content-Type": "application/json" };

let ME = { admin: "", role: "ro", limits: {} };
let DATA = {
  tenants: [], orphans: [], keys: [], actions: [], accounts: [],
  requests: [], overview: null,
};
let FILTER = { actor: "", tenant: "", action: "" };

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[<>&"']/g, (c) =>
  ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" }[c]));
const canWrite = () => ME.role === "rw";

/* Chinese prose written across several source lines.

   HTML collapses a newline plus its indentation into a single space. Between
   English words that is invisible; in the middle of a Chinese sentence it is
   a visible gap -- 「才让你 停止花钱」. Dropping the whole run is right here
   because every one of these line breaks falls between two Chinese
   characters. The few Latin words in this copy are never split across lines,
   so nothing gets glued together. */
const zh = (s) => String(s).replace(/\n\s*/g, "");

/* ── money ──
   The wire is nano-USD and stays that way; only this pair of functions knows.
   Operators type dollars because an input box that wants nine zeros typed
   correctly, at the moment somebody is capping a runaway spend, is a defect. */
function money(nano) {
  const v = Number(nano || 0) / NANO;
  if (v === 0) return "$0.00";
  if (Math.abs(v) >= 0.01) return "$" + v.toFixed(2);
  // Sub-cent amounts are real here -- a handful of calls costs fractions of a
  // cent, and rounding those to $0.00 makes a working meter look broken.
  return "$" + v.toFixed(9).replace(/0+$/, "");
}
function parseMoney(s) {
  const t = String(s ?? "").trim().replace(/^\$/, "").replace(/,/g, "");
  if (!/^\d+(\.\d+)?$/.test(t)) return null;
  return Math.round(parseFloat(t) * NANO);
}
const pct = (a, b) => (b > 0 ? Math.round((a / b) * 100) : 0);
const shortModel = (m) => String(m ?? "").split("/").pop();
const when = (ts) => (ts ? String(ts).slice(0, 19).replace("T", " ") : "—");
const day = (ts) => (ts ? String(ts).slice(0, 10) : "—");

/* ── transport ── */
async function api(path, opts = {}) {
  let r;
  try {
    r = await fetch(path, { headers: H, credentials: "same-origin", ...opts });
  } catch {
    throw new Error("网络不可达——控制面没有响应");
  }
  if (r.status === 401) {
    showLogin("会话已过期，请重新登录");
    throw new Error("会话已过期");
  }
  if (!r.ok) {
    const d = await r.json().then((b) => b.detail).catch(() => r.statusText);
    throw new Error(d || `请求失败（HTTP ${r.status}）`);
  }
  return r.json();
}
const post = (p, b) => api(p, { method: "POST", body: JSON.stringify(b || {}) });

function toast(msg, bad) {
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  el.textContent = msg;
  $("toast").appendChild(el);
  setTimeout(() => el.remove(), bad ? 7000 : 3800);
}

/* ─────────────────────────── dialog engine ───────────────────────────
   Native <dialog>. The browser already supplies a focus trap, ESC, the top
   layer, and inertness of the page behind; hand-rolling those is how a
   console ends up with a modal you can tab out of into a disabled form. */

const dlg = $("dlg");
let BUSY = false;

// While a write is in flight the dialog cannot be dismissed. Closing it
// mid-request would leave the operator with no idea whether the thing
// happened, which is the worst of the three possible outcomes.
dlg.addEventListener("cancel", (e) => { if (BUSY) e.preventDefault(); });

function closeDialog() { if (!BUSY) dlg.close(); }

function fieldHTML(f) {
  const id = "f-" + f.name;
  const req = f.required ? ' aria-required="true"' : "";
  let input;
  if (f.type === "select") {
    input = `<select id="${id}" name="${esc(f.name)}"${req}>${
      (f.options || []).map((o) =>
        `<option value="${esc(o.value)}"${o.value === f.value ? " selected" : ""}>${
          esc(o.label)}</option>`).join("")}</select>`;
  } else if (f.type === "textarea") {
    input = `<textarea id="${id}" name="${esc(f.name)}"${req}>${esc(f.value || "")}</textarea>`;
  } else if (f.type === "static") {
    input = `<div class="dlg-static">${f.html || esc(f.value)}</div>`;
  } else if (f.type === "check") {
    return `<div class="field check" data-field="${esc(f.name)}">
      <input type="checkbox" id="${id}" name="${esc(f.name)}"${f.value ? " checked" : ""}>
      <label for="${id}">${esc(f.label)}</label></div>`;
  } else {
    const t = f.type === "password" ? "password" : "text";
    input = `<input type="${t}" id="${id}" name="${esc(f.name)}" value="${
      esc(f.value ?? "")}" placeholder="${esc(f.placeholder || "")}"${
      f.readonly ? " readonly" : ""}${req}
      ${f.type === "password" ? 'autocomplete="new-password"' : ""}>`;
  }
  return `<div class="field" data-field="${esc(f.name)}">
    <label for="${id}">${esc(f.label)}${f.required ? " *" : ""}</label>
    ${input}
    ${f.hint ? `<span class="hint">${f.hint}</span>` : ""}
  </div>`;
}

/**
 * A form in a dialog.
 *
 * `onSubmit(values, ctx)` may throw: the message lands in the dialog and the
 * typed values survive. `onChange(values, ctx)` runs on every keystroke, which
 * is what lets the budget dialog say "this is a 3.2x raise, it needs a second
 * administrator" before the operator finds out by being refused.
 */
function openForm(opts) {
  const fields = opts.fields.filter(Boolean);
  dlg.className = opts.wide ? "wide" : "";
  dlg.innerHTML = `
    <form method="dialog" id="dlg-form">
      <div class="dlg-head"><h2>${esc(opts.title)}</h2></div>
      <div class="dlg-body">
        ${opts.intro ? `<p class="dlg-intro">${zh(opts.intro)}</p>` : ""}
        <div class="dlg-err" id="dlg-err" hidden></div>
        <div class="dlg-live" id="dlg-live" hidden></div>
        ${fields.map(fieldHTML).join("")}
      </div>
      <div class="dlg-foot">
        ${opts.footNote ? `<span class="faint">${opts.footNote}</span>` : ""}
        <span class="spacer"></span>
        <button type="button" id="dlg-cancel">取消</button>
        <button type="submit" id="dlg-ok"${opts.danger ? ' class="danger"' : ""}>${
          esc(opts.submitLabel || "确定")}</button>
      </div>
    </form>`;

  const form = $("dlg-form");
  const errBox = $("dlg-err");
  const liveBox = $("dlg-live");
  const ok = $("dlg-ok");

  const values = () => {
    const v = {};
    for (const f of fields) {
      const el = form.elements[f.name];
      if (!el) continue;
      v[f.name] = f.type === "check" ? el.checked : el.value;
    }
    return v;
  };
  const ctx = {
    setError(msg) {
      errBox.hidden = !msg;
      errBox.textContent = msg || "";
      if (msg) errBox.scrollIntoView({ block: "nearest" });
    },
    setLive(html, tone) {
      liveBox.hidden = !html;
      liveBox.innerHTML = html || "";
      liveBox.className = "dlg-live" + (tone ? " " + tone : "");
    },
    show(name, on) {
      const el = form.querySelector(`[data-field="${name}"]`);
      if (el) el.hidden = !on;
    },
    setOptions(name, options, keep) {
      const el = form.elements[name];
      if (!el) return;
      const prev = keep ? el.value : null;
      el.innerHTML = options.map((o) =>
        `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("");
      if (prev && options.some((o) => o.value === prev)) el.value = prev;
    },
    disableOk(on) { ok.disabled = !!on; },
    close: closeDialog,
    form,
  };

  $("dlg-cancel").onclick = closeDialog;
  if (opts.onChange) {
    const run = () => opts.onChange(values(), ctx);
    form.addEventListener("input", run);
    form.addEventListener("change", run);
    run();
  }

  form.onsubmit = async (e) => {
    e.preventDefault();
    ctx.setError("");
    const v = values();
    for (const f of fields) {
      const el = form.querySelector(`[data-field="${f.name}"]`);
      if (!f.required || (el && el.hidden)) continue;
      const filled = f.type === "check" ? v[f.name] : String(v[f.name] || "").trim();
      if (!filled) {
        ctx.setError(`「${f.label}」是必填的`);
        form.elements[f.name]?.focus();
        return;
      }
    }
    BUSY = true;
    ok.disabled = true;
    const label = ok.textContent;
    ok.textContent = "处理中…";
    try {
      // `onSubmit` may return a function to run once this dialog is gone.
      // It has to be deferred rather than called inline: there is a single
      // <dialog> element, so a follow-up that opened itself during onSubmit
      // was being closed again by the `dlg.close()` below. That silently
      // destroyed the one screen a freshly issued key is ever shown on.
      const after = await opts.onSubmit(v, ctx);
      BUSY = false;
      dlg.close();
      if (typeof after === "function") after();
    } catch (err) {
      BUSY = false;
      ok.disabled = false;
      ok.textContent = label;
      ctx.setError(err.message);
    }
  };

  dlg.showModal();
  const first = form.querySelector("input:not([readonly]), select, textarea");
  first?.focus();
}

/** A destructive action. `requireTyping` makes the operator name the thing. */
function confirmDialog(o) {
  openForm({
    title: o.title,
    intro: o.intro,
    danger: true,
    fields: [
      o.reason && { name: "reason", label: "理由（会记进操作日志）",
                    required: true, hint: o.reasonHint },
      o.requireTyping && {
        name: "confirm", label: `请输入 ${o.requireTyping} 以确认`,
        required: true, placeholder: o.requireTyping,
      },
    ],
    submitLabel: o.confirmLabel || "确认",
    onSubmit: async (v, ctx) => {
      if (o.requireTyping && v.confirm.trim() !== o.requireTyping) {
        throw new Error(`输入的内容与 ${o.requireTyping} 不一致`);
      }
      await o.onConfirm(v, ctx);
    },
  });
}

/** Read-only content: the config to be landed, an audit entry, an alert list. */
function infoDialog(o) {
  dlg.className = o.wide ? "wide" : "";
  dlg.innerHTML = `
    <div class="dlg-head"><h2>${esc(o.title)}</h2></div>
    <div class="dlg-body">${o.html}</div>
    <div class="dlg-foot">
      ${o.copy ? '<button type="button" id="dlg-copy">复制配置</button>' : ""}
      ${o.goto ? `<button type="button" id="dlg-goto">前往${esc(o.gotoLabel)}</button>` : ""}
      <span class="spacer"></span>
      <button type="button" id="dlg-close">关闭</button>
    </div>`;
  $("dlg-close").onclick = closeDialog;
  if (o.copy) {
    $("dlg-copy").onclick = async () => {
      await navigator.clipboard.writeText(o.copy);
      toast("配置已复制，交给工程侧落到 policies/ 下");
    };
  }
  if (o.goto) {
    $("dlg-goto").onclick = () => { closeDialog(); go(o.goto); };
  }
  dlg.showModal();
}

/* ─────────────────────────── navigation ─────────────────────────── */

const VIEWS = {
  overview: ["总览", "全局水位与异常，先看这里"],
  tenants:  ["业务管理", "停用与调低额度立即生效；新增与大幅提额需要评审"],
  keys:     ["密钥管理", "密钥只显示一次，系统只保存它的指纹"],
  perms:    ["权限管理", "收回权限立即生效；放开权限只能提申请"],
  audit:    ["操作日志", "谁在什么时候改了什么"],
  admins:   ["账户管理", "首个账号只能由运维在服务器上创建"],
};

function go(view) {
  if (!VIEWS[view]) view = "overview";
  for (const k of Object.keys(VIEWS)) $("v-" + k).hidden = k !== view;
  document.querySelectorAll(".nav-item").forEach((b) =>
    b.setAttribute("aria-current", String(b.dataset.view === view)));
  $("view-title").textContent = VIEWS[view][0];
  $("view-hint").textContent = VIEWS[view][1];
  location.hash = view;
  window.scrollTo({ top: 0 });
}

/* ─────────────────────────── session ─────────────────────────── */

function showLogin(msg) {
  $("login").hidden = false;
  $("app").hidden = true;
  if (msg) $("login-err").textContent = msg;
}

$("login-form").onsubmit = async (e) => {
  e.preventDefault();
  $("login-err").textContent = "";
  try {
    await post("/admin/login", { username: $("u").value, password: $("p").value });
    $("p").value = "";
    await boot();
  } catch {
    // One message for every failure, matching the server: telling an attacker
    // which half was wrong hands out a username oracle.
    showLogin("用户名或密码不正确");
  }
};

$("btn-logout").onclick = async () => {
  await post("/admin/logout").catch(() => null);
  location.reload();
};
$("btn-refresh").onclick = () => refresh().then(() => toast("已刷新"));
$("btn-passwd").onclick = () => changePassword();
document.querySelectorAll(".nav-item").forEach((b) => {
  b.onclick = () => go(b.dataset.view);
});

async function boot() {
  ME = await api("/admin/whoami");
  $("login").hidden = true;
  $("app").hidden = false;
  $("who-name").textContent = ME.admin;
  $("who-role").textContent = T.of("role", ME.role);
  $("who-role").className = "role" + (ME.role === "rw" ? "" : " ro");
  go(location.hash.slice(1));
  await refresh();
}

/* ─────────────────────────── data ─────────────────────────── */

async function refresh() {
  const [t, k, a, ac, cr, ov] = await Promise.all([
    api("/admin/tenants").catch((e) => ({ error: e })),
    api("/admin/keys").catch((e) => ({ error: e })),
    api("/admin/actions").catch((e) => ({ error: e })),
    api("/admin/accounts").catch((e) => ({ error: e })),
    api("/admin/change-requests").catch((e) => ({ error: e })),
    api("/admin/overview").catch((e) => ({ error: e })),
  ]);
  if (t.error) return toast("业务线数据读取失败：" + t.error.message, true);
  DATA = {
    tenants: t.tenants, orphans: t.orphans,
    keys: k.keys || [], actions: a.actions || [], accounts: ac.accounts || [],
    requests: cr.requests || [], overview: ov.error ? null : ov,
  };
  renderAll();
}

const tenant = (name) => DATA.tenants.find((x) => x.tenant === name);
const activeWriters = () => DATA.accounts
  .filter((a) => a.state === "active" && a.role === "rw" && a.username !== ME.admin);

/* ─────────────────────────── rendering ─────────────────────────── */

function renderAll() {
  const o = DATA.overview;
  const alerts = o ? o.alerts.filter((x) => x.count) : [];

  $("n-tenants").textContent = DATA.tenants.length;
  $("n-keys").textContent = DATA.keys.filter((x) => x.state === "active").length;
  $("n-admins").textContent = DATA.accounts.filter((x) => x.state === "active").length;
  $("n-perms").textContent = DATA.tenants.reduce(
    (n, t) => n + t.overrides_in_force.length, 0);
  $("n-alerts").textContent = alerts.length || "0";
  $("n-alerts").className = "nav-count" + (alerts.length ? " alert" : "");

  if (o) {
    const bar = $("upstream-bar");
    const real = o.upstream !== "fake";
    bar.hidden = false;
    bar.className = "upstream-bar " + (real ? "real" : "fake");
    bar.innerHTML = `<b>当前上游</b> ${esc(T.of("upstream", o.upstream, o.upstream))}`;
  }

  renderOverview();
  renderTenants();
  renderKeys();
  renderPerms();
  renderAudit();
  renderAdmins();
}

/* ── 01 总览 ── */

function bars(rows, valueOf, labelOf) {
  if (!rows.length) return '<div class="empty">今日还没有调用</div>';
  const max = Math.max(...rows.map(valueOf), 1);
  return `<div class="bars">${rows.map((r) => `
    <div class="bar-row">
      <span class="name" title="${esc(r.name)}">${esc(shortModel(r.name))}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${
        Math.max(2, Math.round((valueOf(r) / max) * 100))}%"></span></span>
      <span class="val">${labelOf(r)}</span>
    </div>`).join("")}</div>`;
}

function renderOverview() {
  const o = DATA.overview;
  if (!o) {
    $("v-overview").innerHTML =
      '<div class="note bad"><b>总览数据读取失败。</b>其余页面仍然可用。</div>';
    return;
  }
  const t = o.totals;
  const water = pct(t.spend_today_nanousd, t.budget_total_nanousd);

  const stats = [
    ["今日调用", String(t.calls_today),
     t.has_ledger_evidence ? `花费 ${money(t.spend_today_nanousd)}` : "账目里还没有调用", ""],
    ["额度水位", t.budget_total_nanousd ? water + "%" : "—",
     `${money(t.spend_today_nanousd)} / ${money(t.budget_total_nanousd)}`,
     water >= 90 ? "bad" : water >= 70 ? "amber" : ""],
    ["业务线", String(t.tenants_online),
     t.tenants_off ? `在线，另有 ${t.tenants_off} 个已停用` : "全部在线",
     t.tenants_off ? "amber" : ""],
    ["有效密钥", String(t.keys_active), `历史共 ${t.keys_total} 把`, ""],
  ];

  $("v-overview").innerHTML = `
    <div class="stat-grid">${stats.map(([l, v, s, c]) => `
      <div class="stat"><div class="l">${l}</div>
        <div class="v ${c}">${esc(v)}</div><div class="s">${esc(s)}</div></div>`).join("")}
    </div>

    <div class="panel-cols">
      <div class="panel">
        <div class="panel-head"><h2>今日花费 · 业务线 TOP 5</h2></div>
        <div class="panel-body">${bars(o.top_tenants, (r) => r.spend,
          (r) => `${money(r.spend)} <small>· ${r.calls} 次</small>`)}</div>
      </div>
      <div class="panel">
        <div class="panel-head"><h2>今日调用 · 模型 TOP 5</h2></div>
        <div class="panel-body">${bars(o.top_models, (r) => r.calls,
          (r) => `${r.calls} 次 <small>· ${money(r.spend)}</small>`)}</div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head"><h2>异常告警</h2><span class="spacer"></span>
        <span class="faint">点任意一行看明细</span></div>
      <div class="alert-list">${o.alerts.map(alertRow).join("")}</div>
    </div>

    <div class="panel">
      <div class="panel-head"><h2>最近操作</h2><span class="spacer"></span>
        <button class="tiny" data-act="goto" data-view="audit">查看全部</button></div>
      <table><tbody>${o.recent_actions.map((a) => `<tr>
        <td class="faint">${esc(when(a.ts))}</td>
        <td>${esc(a.actor)}</td>
        <td class="dim">${esc(T.of("action", a.action))}</td>
        <td>${esc(a.target || "")}</td></tr>`).join("")
        || '<tr><td class="empty">还没有任何控制面操作</td></tr>'}</tbody></table>
    </div>`;
}

function alertRow(a) {
  // Three states, not two. `count: null` means nothing was measured, which is
  // not the same as measured-and-zero -- rendering the first as a calm zero is
  // exactly the lie the three-state gate matrix exists to prevent.
  const unknown = a.count === null;
  const quiet = a.count === 0;
  const sev = unknown ? "暂无数据" : quiet ? "正常"
            : a.level === "bad" ? "严重" : a.level === "warn" ? "注意" : "提示";
  const n = unknown ? "暂无数据可判" : quiet ? "无" : String(a.count);
  const of = a.key === "failed" && !unknown && a.of
    ? `<span class="faint"> / ${a.of}</span>` : "";
  return `<button class="alert-row ${unknown ? "unknown" : quiet ? "quiet" : a.level}"
      data-act="alert" data-key="${esc(a.key)}"${unknown || quiet ? " disabled" : ""}>
    <span class="sev">${sev}</span>
    <span class="body">
      <span class="t">${esc(a.title)}</span>
      <span class="d">${esc(a.detail)}</span>
    </span>
    <span class="n">${n}${of}</span>
    <span class="win">${esc(T.of("window", a.window))}</span>
  </button>`;
}

/* ── 02 业务管理 ── */

function pair(declared, effective) {
  const live = new Set(effective || []);
  const parts = (declared || []).map((v) => live.has(v)
    ? esc(v)
    : `<span class="struck" title="已被收回">${esc(v)}</span>`);
  return parts.join(" ") || '<span class="faint">—</span>';
}

function renderTenants() {
  const newTenantReqs = DATA.requests.filter((r) => r.kind === "new_tenant");
  $("v-tenants").innerHTML = `
    <div class="note">${zh(`<b>停用业务线</b>和<b>调低额度</b>点了就生效，因为它们只会让更多请求被拒绝。
      <b>新增业务线</b>和<b>大幅提额</b>要先走变更申请或第二人复核——放开权限的改动，得有人看过。`)}</div>
    <div class="panel">
      <div class="panel-head"><h2>业务线</h2><span class="spacer"></span>
        ${wbtn("+ 新增业务线", "new-tenant")}</div>
      <table>
        <thead><tr>
          <th>业务线</th><th>接入方式</th><th>状态</th><th class="num">日额度</th>
          <th class="num">今日已用</th><th>自动降级</th><th class="num">密钥</th><th></th>
        </tr></thead>
        <tbody>${DATA.tenants.map(tenantRow).join("")}</tbody>
      </table>
    </div>
    <div class="panel">
      <div class="panel-head"><h2>新增业务线的申请记录</h2></div>
      <table>
        <thead><tr><th>#</th><th>业务线</th><th>理由</th><th>申请人</th>
          <th>时间</th><th>状态</th><th></th></tr></thead>
        <tbody>${newTenantReqs.map(requestRow).join("")
          || '<tr><td colspan="7" class="empty">还没有提过新增业务线的申请</td></tr>'}</tbody>
      </table>
    </div>`;
}

function tenantRow(t) {
  const spend = spendOf(t.tenant);
  const budget = t.effective.budget_nanousd_per_day;
  const bChanged = budget !== t.declared.budget_nanousd_per_day;
  const fChanged = t.effective.allow_fallback !== t.declared.allow_fallback;
  const over = budget > 0 && spend !== null && spend >= budget;
  return `<tr>
    <td><b>${esc(t.tenant)}</b><div class="faint">${esc(t.gate_command)}</div></td>
    <td><span class="tag ${t.integration === "native" ? "info" : ""}">${
      esc(T.of("integration", t.integration))}</span></td>
    <td>${t.enabled ? '<span class="tag on">在线</span>'
                    : '<span class="tag amber">已停用</span>'}</td>
    <td class="num ${bChanged ? "narrowed" : ""}">${money(budget)}
      ${bChanged ? `<div class="faint">原定 ${money(t.declared.budget_nanousd_per_day)}</div>` : ""}
      ${budget === 0 ? '<div class="faint">额度 0 = 关停</div>' : ""}</td>
    <td class="num ${over ? "err" : ""}">${spend === null ? "—" : money(spend)}
      ${over ? '<div class="faint">已用满</div>' : ""}</td>
    <td>${t.effective.allow_fallback ? "开"
        : `<span class="${fChanged ? "narrowed" : "faint"}">关${
            fChanged ? "（已关闭）" : ""}</span>`}</td>
    <td class="num">${t.keys}</td>
    <td><div class="row-actions">
      ${wbtn("调额度", "budget", { tenant: t.tenant })}
      ${t.enabled ? wbtn("停用", "disable-tenant", { tenant: t.tenant }, true)
                  : wbtn("恢复", "enable-tenant", { tenant: t.tenant })}
      <button class="tiny" data-act="tenant-detail" data-tenant="${esc(t.tenant)}">详情</button>
    </div></td></tr>
  <tr class="sub"><td colspan="8">
    <span class="faint">${T.field.cross_tenant_read}</span>
    ${pair(t.declared.cross_tenant_read, t.effective.cross_tenant_read)}
    &nbsp;&nbsp;<span class="faint">${T.field.substitutable_to}</span>
    ${Object.keys(t.declared.models).map((m) =>
      `${esc(shortModel(m))}[${pair(t.declared.models[m], t.effective.models[m])}]`
    ).join(" ") || '<span class="faint">—</span>'}
  </td></tr>`;
}

function spendOf(name) {
  const o = DATA.overview;
  if (!o) return null;
  const row = o.top_tenants.find((r) => r.name === name);
  return row ? row.spend : 0;
}

function requestRow(r) {
  return `<tr>
    <td class="faint">${r.id}</td><td>${esc(r.tenant)}</td>
    <td class="dim">${esc(r.reason)}</td><td class="dim">${esc(r.requested_by)}</td>
    <td class="faint">${esc(day(r.requested_at))}</td>
    <td>${r.state === "shipped" ? '<span class="tag on">已生效</span>'
                                : '<span class="tag amber">待发布</span>'}</td>
    <td><div class="row-actions">
      <button class="tiny" data-act="show-config" data-id="${r.id}">待落地配置</button>
    </div></td></tr>`;
}

/* ── 03 密钥管理 ── */

function renderKeys() {
  $("v-keys").innerHTML = `
    <div class="note">${zh(`密钥只在发放那一次显示，之后谁也查不回来——系统只保存它的指纹。
      换密钥的做法是：<b>先发新的，等业务线切过去，再吊销旧的</b>，全程不断服务。`)}</div>
    <div class="panel">
      <div class="panel-head"><h2>已发放的密钥</h2><span class="spacer"></span>
        ${wbtn("+ 发放密钥", "issue-key")}</div>
      <table>
        <thead><tr><th>密钥</th><th>业务线</th><th>用途</th><th>发放人</th>
          <th>发放时间</th><th>状态</th><th></th></tr></thead>
        <tbody>${DATA.keys.map((k) => `<tr>
          <td><code class="mono-key">${esc(k.key_prefix)}…</code></td>
          <td>${esc(k.tenant)}</td><td>${esc(k.label)}</td>
          <td class="dim">${esc(k.issued_by)}</td>
          <td class="faint">${esc(when(k.issued_at))}</td>
          <td>${k.state === "active" ? '<span class="tag on">有效</span>'
                                     : '<span class="tag off">已吊销</span>'}</td>
          <td><div class="row-actions">${k.state === "active"
            ? wbtn("吊销", "revoke-key",
                   { id: k.key_id, prefix: k.key_prefix, tenant: k.tenant }, true)
            : ""}</div></td></tr>`).join("")
          || '<tr><td colspan="7" class="empty">还没有发放过密钥</td></tr>'}</tbody>
      </table>
    </div>`;
}

/* ── 04 权限管理 ── */

function renderPerms() {
  const inForce = DATA.tenants.flatMap((t) => t.overrides_in_force);
  const widenReqs = DATA.requests.filter((r) => r.kind === "widen");
  $("v-perms").innerHTML = `
    <div class="note">${zh(`这里<b>只能收回权限，不能放开</b>。系统在数据结构上就没有「授予」这个动作，
      所以放权无法从这个页面发生——它必须走<b>变更申请</b>，由工程侧评审后随发布上线。`)}</div>
    ${DATA.orphans.length ? `<div class="note bad">${zh(`<b>已失效的管控 ${DATA.orphans.length} 条。</b>策略文件已经变了，这些收回不再起作用，
      但仍显示为「生效中」。<b>系统不会自动清理</b>——静默清理会让「配置改了但没生效」和
      「谁的收回被悄悄删了」两件事都在屏幕上无声发生。<br>
      ${DATA.orphans.map((o) => `#${o.id} ${esc(o.tenant)} / ${
        esc(T.of("field", o.field))} — ${esc(o.why)}`).join("<br>")}`)}</div>` : ""}

    <div class="panel">
      <div class="panel-head"><h2>各业务线的权限现状</h2><span class="spacer"></span>
        <span class="faint">划掉的是已被收回的</span></div>
      <table>
        <thead><tr><th>业务线</th><th>${T.field.cross_tenant_read}</th>
          <th>${T.field.substitutable_to}</th><th>${T.field.allow_fallback}</th>
          <th></th></tr></thead>
        <tbody>${DATA.tenants.map((t) => `<tr>
          <td><b>${esc(t.tenant)}</b></td>
          <td>${pair(t.declared.cross_tenant_read, t.effective.cross_tenant_read)}</td>
          <td>${Object.keys(t.declared.models).map((m) =>
            `${esc(shortModel(m))}[${pair(t.declared.models[m], t.effective.models[m])}]`
          ).join("<br>") || '<span class="faint">—</span>'}</td>
          <td>${t.effective.allow_fallback ? "开"
              : `<span class="${t.declared.allow_fallback ? "narrowed" : "faint"}">关</span>`}</td>
          <td><div class="row-actions">
            ${wbtn("收回权限", "revoke-perm", { tenant: t.tenant })}
            <button class="tiny" data-act="request-widen"
              data-tenant="${esc(t.tenant)}">申请放开</button>
          </div></td></tr>`).join("")}</tbody>
      </table>
    </div>

    <div class="panel">
      <div class="panel-head"><h2>生效中的权限收回</h2><span class="spacer"></span>
        <span class="faint">立即生效，可撤销</span></div>
      <table>
        <thead><tr><th>#</th><th>业务线</th><th>收回了什么</th><th>理由</th>
          <th>操作人</th><th></th></tr></thead>
        <tbody>${inForce.map((o) => `<tr>
          <td class="faint">${o.id}</td><td>${esc(o.tenant)}</td>
          <td><span class="narrowed">${esc(T.of("field", o.field))}${
            o.model ? " / " + esc(shortModel(o.model)) : ""}${
            o.field === "enabled" || o.field === "allow_fallback"
              ? "" : " = " + esc(o.removed_value)}</span></td>
          <td class="dim">${esc(o.reason)}</td><td class="dim">${esc(o.applied_by)}</td>
          <td><div class="row-actions">${wbtn("撤销", "lift", { id: o.id })}</div></td>
          </tr>`).join("")
          || '<tr><td colspan="6" class="empty">没有生效中的收回——各项权限与策略文件一致</td></tr>'}
        </tbody>
      </table>
    </div>

    <div class="panel">
      <div class="panel-head"><h2>放开权限的申请记录</h2><span class="spacer"></span>
        <span class="faint">状态由策略文件回读，不是人点出来的</span></div>
      <table>
        <thead><tr><th>#</th><th>业务线</th><th>申请内容</th><th>理由</th>
          <th>申请人</th><th>时间</th><th>状态</th><th></th></tr></thead>
        <tbody>${widenReqs.map((r) => `<tr>
          <td class="faint">${r.id}</td><td>${esc(r.tenant)}</td>
          <td>${esc(T.of("widenField", r.field))}${
            r.model ? " / " + esc(shortModel(r.model)) : ""}
            ${r.value ? ` → <b>${esc(r.value)}</b>` : ""}</td>
          <td class="dim">${esc(r.reason)}</td><td class="dim">${esc(r.requested_by)}</td>
          <td class="faint">${esc(day(r.requested_at))}</td>
          <td>${r.state === "shipped" ? '<span class="tag on">已生效</span>'
                                      : '<span class="tag amber">待发布</span>'}</td>
          <td><div class="row-actions">
            <button class="tiny" data-act="show-config" data-id="${r.id}">待落地配置</button>
          </div></td></tr>`).join("")
          || '<tr><td colspan="8" class="empty">还没有提过放开权限的申请</td></tr>'}
        </tbody>
      </table>
    </div>`;
}

/* ── 05 操作日志 ── */

function renderAudit() {
  const actors = [...new Set(DATA.actions.map((a) => a.actor))].sort();
  const targets = [...new Set(DATA.actions.map((a) => a.target).filter(Boolean))].sort();
  const kinds = [...new Set(DATA.actions.map((a) => a.action))].sort();
  const rows = DATA.actions.filter((a) =>
    (!FILTER.actor || a.actor === FILTER.actor) &&
    (!FILTER.tenant || a.target === FILTER.tenant) &&
    (!FILTER.action || a.action === FILTER.action));

  const opts = (list, cur, table) =>
    `<option value="">全部</option>` + list.map((v) =>
      `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${
        esc(table ? T.of(table, v) : v)}</option>`).join("");

  $("v-audit").innerHTML = `
    <div class="note">${zh(`每一行都记着<b>是谁</b>操作的，不是一个共用账号。出了事要复盘时，
      说不出人的日志等于没有日志。`)}</div>
    <div class="panel">
      <div class="panel-head"><h2>筛选</h2></div>
      <div class="panel-body">
        <div class="filters">
          <div class="field"><label>操作人</label>
            <select data-filter="actor">${opts(actors, FILTER.actor)}</select></div>
          <div class="field"><label>对象</label>
            <select data-filter="tenant">${opts(targets, FILTER.tenant)}</select></div>
          <div class="field"><label>动作</label>
            <select data-filter="action">${opts(kinds, FILTER.action, "action")}</select></div>
          <div><button data-act="clear-filter">清除</button></div>
        </div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-head"><h2>操作记录</h2><span class="spacer"></span>
        <span class="faint">${rows.length} / ${DATA.actions.length} 条，点行看明细</span></div>
      <table>
        <thead><tr><th>时间</th><th>操作人</th><th>动作</th><th>对象</th>
          <th>改动摘要</th></tr></thead>
        <tbody>${rows.map((a, i) => `<tr class="clickable"
            data-act="audit-detail" data-i="${DATA.actions.indexOf(a)}">
          <td class="faint">${esc(when(a.ts))}</td>
          <td><b>${esc(a.actor)}</b></td>
          <td class="dim">${esc(T.of("action", a.action))}</td>
          <td>${esc(a.target || "")}</td>
          <td class="faint">${esc(changedFields(a).map((f) =>
            T.of("field", f)).join("、") || "—")}</td></tr>`).join("")
          || '<tr><td colspan="5" class="empty">没有符合条件的记录</td></tr>'}</tbody>
      </table>
    </div>`;
}

function changedFields(a) {
  if (!a.before || !a.after) return [];
  const keys = new Set([...Object.keys(a.before), ...Object.keys(a.after)]);
  return [...keys].filter((k) =>
    JSON.stringify(a.before[k]) !== JSON.stringify(a.after[k]));
}

/* ── 06 账户管理 ── */

function renderAdmins() {
  $("v-admins").innerHTML = `
    <div class="note">${zh(`<b>第一个</b>账号只能由运维在服务器上创建。一个能自己在网页上开出第一个管理员的系统，
      就有一段谁都能把自己变成管理员的时间。后续账号没有这个问题——已经登录的人在为他背书。`)}</div>
    <div class="note info">${zh(`这里<b>不能重置别人的密码</b>。能把另一个管理员的密码改成自己知道的值，
      就能以对方的身份登录，大幅提额的「第二个人签字」也就形同虚设。
      忘了密码由运维在服务器上重设：<code>make admin-passwd USER=&lt;name&gt;</code>`)}</div>
    <div class="panel">
      <div class="panel-head"><h2>账号</h2><span class="spacer"></span>
        ${wbtn("+ 新增账号", "new-account")}</div>
      <table>
        <thead><tr><th>用户名</th><th>权限</th><th>状态</th><th>最后登录</th>
          <th class="num">登录失败</th><th></th></tr></thead>
        <tbody>${DATA.accounts.map((a) => {
          const me = a.username === ME.admin;
          return `<tr>
          <td><b>${esc(a.username)}</b>${me ? ' <span class="faint">（你）</span>' : ""}</td>
          <td><span class="tag ${a.role === "rw" ? "amber" : "info"}">${
            esc(T.of("role", a.role))}</span></td>
          <td>${a.state === "active" ? '<span class="tag on">正常</span>'
              : a.state === "locked" ? '<span class="tag bad">已冻结</span>'
              : '<span class="tag off">已停用</span>'}</td>
          <td class="dim">${a.last_login_at ? esc(when(a.last_login_at)) : "从未登录"}</td>
          <td class="num ${a.failed_attempts ? "err" : "faint"}">${a.failed_attempts}</td>
          <td><div class="row-actions">
            ${a.state === "locked"
              ? wbtn("解冻", "unlock-account", { user: a.username }) : ""}
            ${a.state === "disabled"
              ? wbtn("恢复", "enable-account", { user: a.username })
              : me ? "" : wbtn("停用", "disable-account", { user: a.username }, true)}
          </div></td></tr>`;
        }).join("")}</tbody>
      </table>
    </div>`;
}

/* A write button, disabled with a stated reason for read-only administrators.
   Refusing at the click rather than after the form is filled in: being told
   "you may not do this" only once you have typed a justification is a way of
   wasting somebody's time and teaching them not to trust the buttons. */
function wbtn(label, act, data = {}, danger) {
  const attrs = Object.entries(data)
    .map(([k, v]) => ` data-${k}="${esc(v)}"`).join("");
  if (!canWrite()) {
    return `<button class="tiny" disabled title="你的账号是只读权限，不能执行这个操作"
      >${esc(label)}</button>`;
  }
  return `<button class="tiny${danger ? " danger" : ""}" data-act="${esc(act)}"${
    attrs}>${esc(label)}</button>`;
}

/* ─────────────────────────── actions ─────────────────────────── */

async function done(msg) {
  await refresh();
  toast(msg);
}

const ACTIONS = {
  goto: (d) => go(d.view),
  "clear-filter": () => { FILTER = { actor: "", tenant: "", action: "" }; renderAudit(); },

  alert: (d) => {
    const a = DATA.overview.alerts.find((x) => x.key === d.key);
    if (!a) return;
    infoDialog({
      title: a.title,
      wide: true,
      html: `<p class="dlg-intro">${esc(a.detail)}
        <br><b>统计口径</b>：${esc(T.of("window", a.window))}${
          a.window === "since_boot"
            ? "（这份记录保存在网关进程内存里，重启后清空）" : ""}</p>
        ${alertBody(a)}`,
      goto: a.view, gotoLabel: VIEWS[a.view] ? VIEWS[a.view][0] : "",
    });
  },

  "tenant-detail": (d) => {
    const t = tenant(d.tenant);
    infoDialog({
      title: `业务线 ${t.tenant}`,
      wide: true,
      html: `<dl class="kv">
        <dt>${T.field.integration}</dt><dd>${esc(T.of("integration", t.integration))}</dd>
        <dt>${T.field.gate_command}</dt><dd><code>${esc(t.gate_command)}</code></dd>
        <dt>${T.field.enabled}</dt><dd>${t.enabled ? "在线" : "已停用"}</dd>
        <dt>${T.field.budget_nanousd_per_day}</dt>
        <dd>${money(t.effective.budget_nanousd_per_day)}${
          t.effective.budget_nanousd_per_day !== t.declared.budget_nanousd_per_day
            ? ` <span class="faint">（策略文件里是 ${
                money(t.declared.budget_nanousd_per_day)}）</span>` : ""}</dd>
        <dt>今日已用</dt><dd>${money(spendOf(t.tenant) || 0)}</dd>
        <dt>${T.field.allow_fallback}</dt>
        <dd>${t.effective.allow_fallback ? "开" : "关"}</dd>
        <dt>${T.field.cross_tenant_read}</dt>
        <dd>${pair(t.declared.cross_tenant_read, t.effective.cross_tenant_read)}</dd>
        <dt>${T.field.substitutable_to}</dt>
        <dd>${Object.keys(t.declared.models).map((m) =>
          `${esc(m)}<br>&nbsp;&nbsp;${pair(t.declared.models[m], t.effective.models[m])}`
        ).join("<br>") || "—"}</dd>
        <dt>有效密钥</dt><dd>${t.keys} 把</dd>
      </dl>`,
    });
  },

  "audit-detail": (d) => {
    const a = DATA.actions[Number(d.i)];
    const keys = a.before && a.after
      ? [...new Set([...Object.keys(a.before), ...Object.keys(a.after)])]
      : Object.keys(a.after || a.before || {});
    const fmt = (k, v) => {
      if (v === undefined) return '<span class="faint">（无）</span>';
      if (k === "budget_nanousd_per_day") return money(v);
      if (Array.isArray(v)) return v.length ? esc(v.join(" ")) : "—";
      if (v && typeof v === "object") {
        return Object.entries(v).map(([m, list]) =>
          `${esc(shortModel(m))}[${esc((list || []).join(" ")) || "—"}]`).join(" ") || "—";
      }
      if (typeof v === "boolean") return v ? "开" : "关";
      return esc(String(v));
    };
    infoDialog({
      title: T.of("action", a.action),
      wide: true,
      html: `<dl class="kv">
        <dt>时间</dt><dd>${esc(when(a.ts))}</dd>
        <dt>操作人</dt><dd>${esc(a.actor)}</dd>
        <dt>对象</dt><dd>${esc(a.target || "—")}</dd>
      </dl>
      ${keys.length ? `<p class="dlg-intro" style="margin:18px 0 10px"><b>改动明细</b></p>
      <dl class="kv">${keys.map((k) => {
        const b = a.before ? a.before[k] : undefined;
        const af = a.after ? a.after[k] : undefined;
        const changed = JSON.stringify(b) !== JSON.stringify(af);
        if (!changed) return "";
        return `<dt>${esc(T.of("field", k))}</dt><dd>
          ${a.before ? `<span class="was">${fmt(k, b)}</span> → ` : ""}${fmt(k, af)}</dd>`;
      }).join("")}</dl>` : '<p class="dlg-intro">这个动作没有记录字段级改动。</p>'}`,
    });
  },

  "show-config": (d) => {
    const r = DATA.requests.find((x) => x.id === Number(d.id));
    const shipped = r.state === "shipped";
    infoDialog({
      title: `变更申请单 #${r.id}`,
      wide: true,
      copy: r.payload,
      html: `<p class="dlg-intro">
        <b>${esc(T.of("requestKind", r.kind))}</b> · ${esc(r.tenant)}
        · 由 ${esc(r.requested_by)} 于 ${esc(day(r.requested_at))} 提出<br>
        理由：${esc(r.reason)}<br>
        状态：<b>${shipped ? "已生效" : "待发布"}</b>——${shipped
          ? "这一项已经在策略文件里了。"
          : "策略文件里还没有这一项。"}状态是每次回头看策略文件推导出来的，不是谁点出来的。
        </p>
        <p class="dlg-intro" style="margin-bottom:8px"><b>待落地配置</b>
        <span class="faint">（保留原始字段名，交给工程侧落到 policies/${
          esc(r.tenant)}.yaml，评审合入后随发布上线）</span></p>
        <pre>${colorConfig(r.payload)}</pre>`,
    });
  },

  budget: (d) => {
    const t = tenant(d.tenant);
    const cur = t.effective.budget_nanousd_per_day;
    const writers = activeWriters();
    openForm({
      title: `调整日额度 · ${t.tenant}`,
      intro: `当前日额度 <b>${money(cur)}</b>，今日已用 <b>${money(spendOf(t.tenant) || 0)}</b>。
        调低永远立即生效且不需要复核，<b>包括直接调到 $0 止血</b>——一个要等人点头才让你
        停止花钱的控制面，会在最该用它的那一刻被绕过。`,
      fields: [
        { name: "amount", label: "新的日额度（美元）", required: true,
          value: (cur / NANO).toFixed(2), placeholder: "10.00",
          hint: "填美元，例如 10.00。额度 0 表示关停，不表示不限量。" },
        { name: "reason", label: "理由", required: true,
          hint: "会记进操作日志。" },
        { name: "approver", label: "复核人", type: "select",
          options: [{ value: "", label: "（不需要复核）" }].concat(
            writers.map((a) => ({ value: a.username, label: a.username })))},
      ],
      submitLabel: "写入",
      onChange: (v, ctx) => {
        const next = parseMoney(v.amount);
        if (next === null) {
          ctx.setLive("金额格式不对，填数字即可，例如 <b>10.00</b>。", "warn");
          ctx.show("approver", false);
          return;
        }
        const f = ME.limits.budget_raise_factor_without_approval;
        const ceil = ME.limits.budget_ceiling_without_approval;
        const needs = next > cur && (next > cur * f || next > ceil);
        ctx.show("approver", needs);
        ctx.setOptions("approver", needs
          ? [{ value: "", label: "（请选择复核人）" }].concat(
              writers.map((a) => ({ value: a.username, label: a.username })))
          : [{ value: "", label: "（不需要复核）" }].concat(
              writers.map((a) => ({ value: a.username, label: a.username }))), true);
        if (next < cur) {
          ctx.setLive(`${money(cur)} → <b>${money(next)}</b>，这是<b>调低</b>，
            点了立即生效，不需要复核。`);
        } else if (next === cur) {
          ctx.setLive("和当前额度相同。");
        } else if (needs) {
          const times = cur > 0 ? (next / cur).toFixed(1) + " 倍" : "从 0 起步";
          ctx.setLive(`${money(cur)} → <b>${money(next)}</b>，提额 ${times}，
            超过免复核阈值（${f} 倍或 ${money(ceil)}），<b>需要另一位管理员复核</b>。${
            writers.length ? "" : "<br>目前没有其他可操作的管理员，这笔提额无法完成。"}`,
            "warn");
        } else {
          ctx.setLive(`${money(cur)} → <b>${money(next)}</b>，在免复核范围内。`);
        }
      },
      onSubmit: async (v) => {
        const next = parseMoney(v.amount);
        if (next === null) throw new Error("金额格式不对，填数字即可，例如 10.00");
        const f = ME.limits.budget_raise_factor_without_approval;
        const ceil = ME.limits.budget_ceiling_without_approval;
        if (next > cur && (next > cur * f || next > ceil) && !v.approver) {
          throw new Error(writers.length
            ? "这笔提额超过免复核阈值，必须选一位复核人"
            : "这笔提额需要第二位管理员复核，但目前没有其他可操作的账号");
        }
        await post("/admin/budget", {
          tenant: t.tenant,
          budget_nanousd_per_day: next,
          reason: v.reason,
          approved_by: v.approver || null,
          version: t.version,
        });
        await done(`${t.tenant} 的日额度已改为 ${money(next)}`);
      },
    });
  },

  "disable-tenant": (d) => confirmDialog({
    title: `停用业务线 ${d.tenant}`,
    intro: `停用后这条业务线的<b>密钥立即失效</b>，请求一律被拒，历史账目保留。
      这是一次收紧，点了就生效，随时可以恢复。`,
    reason: true,
    reasonHint: "为什么现在要停它？没有理由的收紧，之后没人敢撤。",
    confirmLabel: "停用",
    onConfirm: async (v) => {
      await post("/admin/overrides", {
        tenant: d.tenant, field: "enabled", removed_value: "true",
        reason: v.reason, version: tenant(d.tenant).version,
      });
      await done(`${d.tenant} 已停用——密钥立即失效，历史账目保留`);
    },
  }),

  "enable-tenant": async (d) => {
    const o = tenant(d.tenant).overrides_in_force.find((x) => x.field === "enabled");
    if (!o) return toast("找不到对应的停用记录", true);
    try {
      await post(`/admin/overrides/${o.id}/lift`);
      await done(`${d.tenant} 已恢复`);
    } catch (e) { toast(e.message, true); }
  },

  lift: async (d) => {
    try {
      await post(`/admin/overrides/${d.id}/lift`);
      await done("已撤销，这一项恢复到策略文件里的样子");
    } catch (e) { toast(e.message, true); }
  },

  "issue-key": () => openForm({
    title: "发放密钥",
    intro: `密钥<b>只在这一次显示</b>，之后谁也查不回来——系统只保存它的指纹。
      发放后立即可用，不用重启网关。`,
    fields: [
      { name: "tenant", label: "业务线", type: "select", required: true,
        options: DATA.tenants.map((t) => ({ value: t.tenant, label: t.tenant })) },
      { name: "label", label: "用途", required: true,
        placeholder: "生产 / 轮换 2026-08",
        hint: "两把没有用途说明的密钥，在列表里分不出谁是谁。" },
    ],
    submitLabel: "发放",
    onSubmit: async (v) => {
      const r = await post("/admin/keys", { tenant: v.tenant, label: v.label });
      await refresh();
      return () => showSecret(r);
    },
  }),

  "revoke-key": (d) => confirmDialog({
    title: `吊销密钥 ${d.prefix}…`,
    intro: `这把密钥属于 <b>${esc(d.tenant)}</b>。吊销<b>没有逆操作</b>——密钥不能复活，
      需要的话只能重新发一把。如果对方还在用它，服务会立刻中断。`,
    reason: false,
    requireTyping: d.prefix,
    confirmLabel: "吊销",
    onConfirm: async () => {
      await post(`/admin/keys/${d.id}/revoke`);
      await done("已吊销——密钥不能复活，需要的话重新发一把");
    },
  }),

  "revoke-perm": (d) => {
    const t = tenant(d.tenant);
    const optsFor = (field, model) => {
      if (field === "cross_tenant_read") {
        return t.effective.cross_tenant_read.map((v) => ({ value: v, label: v }));
      }
      if (field === "substitutable_to") {
        return (t.effective.models[model] || []).map((v) => ({ value: v, label: v }));
      }
      return [];
    };
    openForm({
      title: `收回权限 · ${t.tenant}`,
      intro: `收回<b>立即生效</b>，因为它只会让更多请求被拒绝。想反过来放开权限，
        请用「申请放开」——这个页面在数据结构上就没有「授予」这个动作。`,
      fields: [
        { name: "field", label: "要收回什么", type: "select", required: true,
          options: [
            { value: "enabled", label: "停用该业务线" },
            { value: "allow_fallback", label: "关闭自动降级" },
            { value: "cross_tenant_read", label: "收回跨业务线查账" },
            { value: "substitutable_to", label: "收窄可替代模型" },
          ]},
        { name: "model", label: "哪个模型", type: "select",
          options: Object.keys(t.effective.models).map((m) => ({ value: m, label: m })) },
        { name: "value", label: "移除哪一项", type: "select", options: [] },
        { name: "reason", label: "理由", required: true,
          hint: "没有理由的收紧，之后没人敢撤。" },
      ],
      submitLabel: "收回",
      danger: true,
      onChange: (v, ctx) => {
        const isSub = v.field === "substitutable_to";
        const isList = isSub || v.field === "cross_tenant_read";
        ctx.show("model", isSub);
        ctx.show("value", isList);
        if (isList) {
          const opts = optsFor(v.field, v.model);
          ctx.setOptions("value", opts.length ? opts
            : [{ value: "", label: "（没有可移除的项）" }], true);
          ctx.disableOk(!opts.length);
          ctx.setLive(opts.length ? "" :
            "这条业务线在这一项上已经没有可以收回的内容了。", "warn");
        } else {
          ctx.disableOk(false);
          ctx.setLive(v.field === "enabled"
            ? "停用后该业务线的密钥立即失效，请求一律被拒。" : "");
        }
      },
      onSubmit: async (v) => {
        const isSub = v.field === "substitutable_to";
        const isList = isSub || v.field === "cross_tenant_read";
        await post("/admin/overrides", {
          tenant: t.tenant,
          field: v.field,
          model: isSub ? v.model : null,
          removed_value: isList ? v.value : "true",
          reason: v.reason,
          version: t.version,
        });
        await done("已收回——立即生效");
      },
    });
  },

  "request-widen": (d) => {
    const t = tenant(d.tenant);
    openForm({
      title: `申请放开权限 · ${t.tenant}`,
      intro: `放开权限<b>不能在这里直接生效</b>——它要改的是网关的策略文件，
        由工程侧评审后随发布上线。这个页面负责的是：把申请说清楚、算出它的影响、
        留下谁在什么时候为什么提的，并把要落的配置交给工程侧。`,
      fields: [
        { name: "field", label: "申请什么", type: "select", required: true,
          options: [
            { value: "cross_tenant_read", label: "跨业务线查账" },
            { value: "substitutable_to", label: "放宽可替代模型" },
            { value: "allow_fallback", label: "开启自动降级" },
          ]},
        { name: "model", label: "哪个模型", type: "select",
          options: Object.keys(t.declared.models).map((m) => ({ value: m, label: m })) },
        { name: "value", label: "申请的值", placeholder: "",
          hint: "" },
        { name: "reason", label: "理由", required: true,
          hint: "会连同申请单一起留档，工程侧评审时看的就是这句。" },
      ],
      submitLabel: "提交申请",
      onChange: (v, ctx) => {
        ctx.show("model", v.field === "substitutable_to");
        ctx.show("value", v.field !== "allow_fallback");
        const hint = ctx.form.querySelector('[data-field="value"] .hint');
        if (hint) {
          hint.textContent = v.field === "cross_tenant_read"
            ? "填另一条业务线的名字，表示允许本业务线查它的账。"
            : "填一个模型族名，表示允许替换到它。";
        }
      },
      onSubmit: async (v) => {
        const r = await post("/admin/change-requests", {
          tenant: t.tenant, field: v.field,
          model: v.field === "substitutable_to" ? v.model : null,
          value: v.field === "allow_fallback" ? "true" : v.value,
          reason: v.reason,
        });
        await refresh();
        return () => showRequestResult(r);
      },
    });
  },

  "new-tenant": () => openForm({
    title: "新增业务线",
    intro: `新增一条业务线等于凭空多一个能花钱的主体，而它到底接没接通，
      只有接入校验说了算——所以这里<b>只出申请，不直接建</b>。
      新业务线一切默认关闭：不能替换模型、不能查别人的账、不自动降级。`,
    fields: [
      { name: "tenant", label: "业务线名", required: true, placeholder: "newline",
        hint: "小写英文，会成为策略文件名 policies/<名字>.yaml。" },
      { name: "integration", label: "接入方式", type: "select", options: [
        { value: "zero_touch", label: "零侵入接入（不改对方代码）" },
        { value: "native", label: "原生接入（本身就在中台里）" },
      ]},
      { name: "gate_command", label: "质量门禁命令", value: "make eval",
        hint: "这条业务线自己的验收命令。" },
      { name: "budget", label: "日额度（美元）", required: true, placeholder: "10.00",
        hint: "填美元。0 表示关停，不表示不限量。" },
      { name: "reason", label: "理由", required: true },
    ],
    submitLabel: "提交申请",
    onSubmit: async (v) => {
      const budget = parseMoney(v.budget);
      if (budget === null) throw new Error("日额度格式不对，填数字即可，例如 10.00");
      const r = await post("/admin/change-requests/tenant", {
        tenant: v.tenant.trim(), integration: v.integration,
        gate_command: v.gate_command || "make eval",
        budget_nanousd_per_day: budget, reason: v.reason,
      });
      await refresh();
      return () => showRequestResult(r);
    },
  }),

  "new-account": () => openForm({
    title: "新增账号",
    intro: `你已经登录，所以你在为这个账号背书——这就是为什么<b>后续</b>账号可以从
      网页上建，而<b>第一个</b>不行。`,
    fields: [
      { name: "username", label: "用户名", required: true },
      { name: "password", label: "密码", type: "password", required: true,
        hint: "至少 12 位。这个账号能停用业务线、能发密钥，一个午饭时间能猜出来的密码，是上面所有东西里最弱的一环。" },
      { name: "password2", label: "再输一次", type: "password", required: true },
      { name: "role", label: "权限", type: "select", options: [
        { value: "rw", label: "可操作" }, { value: "ro", label: "只读" },
      ]},
    ],
    submitLabel: "创建",
    onSubmit: async (v) => {
      if (v.password !== v.password2) throw new Error("两次输入的密码不一致");
      await post("/admin/accounts", {
        username: v.username.trim(), password: v.password, role: v.role,
      });
      await done(`账号 ${v.username.trim()} 已创建`);
    },
  }),

  "disable-account": (d) => confirmDialog({
    title: `停用账号 ${d.user}`,
    intro: `停用后 <b>${esc(d.user)}</b> 无法再登录，<b>它当前在线的会话会同时结束</b>。
      之后可以恢复，但恢复不会让旧的会话复活。`,
    confirmLabel: "停用",
    onConfirm: async () => {
      await post(`/admin/accounts/${encodeURIComponent(d.user)}/disable`);
      await done(`${d.user} 已停用，在线会话已结束`);
    },
  }),

  "enable-account": async (d) => {
    try {
      await post(`/admin/accounts/${encodeURIComponent(d.user)}/enable`);
      await done(`${d.user} 已恢复，可以重新登录`);
    } catch (e) { toast(e.message, true); }
  },

  "unlock-account": async (d) => {
    try {
      await post(`/admin/accounts/${encodeURIComponent(d.user)}/unlock`);
      await done(`${d.user} 已解冻`);
    } catch (e) { toast(e.message, true); }
  },
};

function changePassword() {
  openForm({
    title: "修改我的密码",
    intro: `改完之后，<b>你在其他浏览器上的登录会全部失效</b>，当前这个窗口不受影响。
      在密码可能已经泄露时改密码，如果不把别人踢下线，等于什么也没做。`,
    fields: [
      { name: "current_password", label: "当前密码", type: "password", required: true },
      { name: "new_password", label: "新密码", type: "password", required: true,
        hint: "至少 12 位。" },
      { name: "confirm", label: "再输一次", type: "password", required: true },
    ],
    submitLabel: "修改",
    onSubmit: async (v) => {
      if (v.new_password !== v.confirm) throw new Error("两次输入的新密码不一致");
      const r = await post("/admin/password", {
        current_password: v.current_password, new_password: v.new_password,
      });
      toast(r.sessions_ended
        ? `密码已修改，同时结束了 ${r.sessions_ended} 个其他会话`
        : "密码已修改");
      await refresh();
    },
  });
}

/* ── shared dialog bodies ── */

function alertBody(a) {
  if (a.key === "failed") {
    return `<dl class="kv">
      <dt>失败或中断</dt><dd class="err">${a.count} 次</dd>
      <dt>今日全部调用</dt><dd>${a.of} 次</dd>
      <dt>占比</dt><dd>${pct(a.count, a.of)}%</dd></dl>`;
  }
  if (!a.items || !a.items.length) {
    return '<div class="empty">没有可列出的明细</div>';
  }
  if (typeof a.items[0] === "string") {
    return `<dl class="kv"><dt>涉及业务线</dt><dd>${a.items.map(esc).join("、")}</dd></dl>`;
  }
  const cols = Object.keys(a.items[0]);
  return `<table><thead><tr>${cols.map((c) =>
    `<th>${esc(T.of("field", c, c))}</th>`).join("")}</tr></thead>
    <tbody>${a.items.map((it) => `<tr>${cols.map((c) =>
      `<td>${esc(c === "ts" ? when(it[c]) : it[c])}</td>`).join("")}</tr>`).join("")}
    </tbody></table>`;
}

function colorConfig(text) {
  return esc(text).split("\n").map((l) =>
    l.startsWith("+") ? `<span class="add">${l}</span>` :
    l.startsWith("-") ? `<span class="del">${l}</span>` :
    l.startsWith("@") ? `<span class="hd">${l}</span>` : l).join("\n");
}

function showSecret(r) {
  dlg.className = "wide";
  dlg.innerHTML = `
    <div class="dlg-head"><h2>密钥已发放</h2></div>
    <div class="dlg-body">
      <p class="dlg-intro"><b>${esc(r.warning)}</b>
        关掉这个窗口之后，列表里只会留下它的前缀。</p>
      <div class="secret">
        <code id="secret-val">${esc(r.api_key)}</code>
        <button type="button" id="secret-copy">复制</button>
      </div>
      <div class="field check">
        <input type="checkbox" id="secret-ack">
        <label for="secret-ack">我已经把这把密钥保存到安全的地方了</label>
      </div>
    </div>
    <div class="dlg-foot"><span class="spacer"></span>
      <button type="button" id="secret-done" disabled>完成</button></div>`;
  $("secret-copy").onclick = async () => {
    await navigator.clipboard.writeText(r.api_key);
    toast("密钥已复制到剪贴板");
  };
  // The acknowledgement is not ceremony. This value exists in exactly one
  // place -- this DOM node -- and the database has only its hash. Closing the
  // window without copying it means reissuing.
  $("secret-ack").onchange = (e) => { $("secret-done").disabled = !e.target.checked; };
  $("secret-done").onclick = closeDialog;
  dlg.showModal();
}

function showRequestResult(r) {
  const v = r.gates.verdict;
  infoDialog({
    title: `申请已提交${r.id ? " · 单号 #" + r.id : ""}`,
    wide: true,
    copy: r.config,
    html: `<p class="dlg-intro">
        <b>影响评估：${esc(T.of("verdict", v))}</b><br>${esc(r.gates.detail)}
      </p>
      ${(r.gates.g1 || []).length || (r.gates.g4 || []).length ? `
      <div class="dlg-err">${[
        ...(r.gates.g1 || []).map((x) => `${T.gate.g1}：${x}`),
        ...(r.gates.g4 || []).map((x) => `${T.gate.g4}：${x}`),
      ].map((s) => esc(String(s))).join("<br>")}</div>` : ""}
      <p class="dlg-intro" style="margin-bottom:8px"><b>待落地配置</b>
        <span class="faint">（保留原始字段名，交给工程侧）</span></p>
      <pre>${colorConfig(r.config)}</pre>
      <p class="dlg-intro" style="margin:14px 0 0">${esc(r.how_to_apply)}</p>`,
  });
}

/* ─────────────────────────── wiring ─────────────────────────── */

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-act]");
  if (!b || b.disabled) return;
  const fn = ACTIONS[b.dataset.act];
  if (!fn) return;
  e.preventDefault();
  fn(b.dataset, b);
});

document.addEventListener("change", (e) => {
  const s = e.target.closest("[data-filter]");
  if (!s) return;
  FILTER[s.dataset.filter] = s.value;
  renderAudit();
});

window.addEventListener("hashchange", () => go(location.hash.slice(1)));

/* ── boot ── */
boot().catch(() => showLogin(""));
