"use strict";
/*
  The terminology dictionary.

  Everything the control plane shows an operator passes through here. The
  reason it is one file rather than strings scattered through the render
  functions is that the mapping is the deliverable: it is the difference
  between a console a business owner reads and one they have to ask an
  engineer to interpret. Kept in one table, it can be reviewed as a table.

  The rule the whole file follows: the surface says what a change *means*,
  never what it is called in the schema. `cross_tenant_read` describes a
  column; "跨业务线查账" describes the thing the person is about to grant.

  ONE exception, and it is deliberate: the 「待落地配置」 text box shows the
  raw YAML with its original English keys. That string is not being read, it
  is being handed to an engineer to land in `policies/<tenant>.yaml`.
  Translating it would produce a configuration that does not parse.
*/

const T = {
  /* Policy fields, as they appear in tables, dialogs and audit summaries. */
  field: {
    tenant: "业务线",
    enabled: "启用状态",
    allow_fallback: "自动降级",
    budget_nanousd_per_day: "日额度",
    cross_tenant_read: "跨业务线查账",
    substitutable_to: "可替代模型",
    models: "模型",
    integration: "接入方式",
    gate_command: "质量门禁",
    api_key_env: "密钥环境变量",
    repo_path: "代码仓路径",
    key_id: "密钥编号",
    label: "用途",
    role: "权限",
    state: "状态",
    kind: "类型",
    sessions_ended: "同时结束的会话数",
  },

  /* Audit action names. Every `cp.record(...)` in admin/api.py must have an
     entry here; tests/test_admin_terms.py fails the build if one does not. */
  action: {
    "override.add.enabled": "停用业务线",
    "override.add.allow_fallback": "关闭自动降级",
    "override.add.cross_tenant_read": "收回跨业务线查账",
    "override.add.substitutable_to": "收窄可替代模型",
    "override.lift": "撤销权限收回",
    "budget.set": "调整日额度",
    "key.issue": "发放密钥",
    "key.revoke": "吊销密钥",
    "account.create": "新增账号",
    "account.disable": "停用账号",
    "account.enable": "恢复账号",
    "account.unlock": "解冻账号",
    "password.change": "修改密码",
    "change_request.open": "提交变更申请",
  },

  /* Entity states. Each is rendered with its own word, never a bare colour:
     a red dot and a green dot are the same dot to a colourblind reader. */
  state: {
    active: "有效",
    revoked: "已吊销",
    locked: "已冻结",
    disabled: "已停用",
    pending: "待发布",
    shipped: "已生效",
    ok: "成功",
    failed: "失败",
    aborted: "已中断",
  },

  /* Account state needs its own word for `active`: an account is 正常,
     a credential is 有效. */
  accountState: { active: "正常", locked: "已冻结", disabled: "已停用" },

  role: { rw: "可操作", ro: "只读" },

  integration: { zero_touch: "零侵入接入", native: "原生接入" },

  /* The four gates, named by what they protect rather than by their number.
     "G1" tells an operator nothing; "多样性红线" tells them what breaks. */
  gate: {
    g1: "多样性红线",
    g4: "降级透明红线",
  },

  /* What the impact assessment concluded. `no_evidence` is deliberately not
     a pass -- an unmeasured thing rendered as green is the failure the
     three-state gate matrix exists to prevent. */
  verdict: {
    clean: "通过",
    would_violate: "会触碰红线",
    no_evidence: "暂无数据可判",
    not_applicable: "不适用",
  },
  verdictTone: {
    clean: "ok",
    would_violate: "bad",
    no_evidence: "warn",
    not_applicable: "warn",
  },

  /* Which clock a number was measured on. Ledger-derived figures use the
     same day boundary the gateway enforces 429s on; routing vetoes come from
     a bounded in-process log that a restart empties. Labelling both "今日"
     would make a restart look like the anomalies went away. */
  window: {
    today: "今日",
    since_boot: "本次启动以来",
    now: "当前",
  },

  /* What kind of change was requested. */
  requestKind: { widen: "放开权限", new_tenant: "新增业务线" },

  /* The permission a widening request asks for. */
  widenField: {
    cross_tenant_read: "跨业务线查账",
    substitutable_to: "放宽可替代模型",
    allow_fallback: "开启自动降级",
  },

  upstream: {
    fake: "内置假上游（数字是模拟的，不产生费用）",
    litellm: "真实供应商（会产生真实费用）",
  },
};

/* Look up `key` in `table`, falling back to the raw value.
   Falling back rather than throwing is on purpose: a term nobody translated
   yet should show up on screen looking untranslated, which is how it gets
   noticed and fixed. A blank cell hides it. */
T.of = (table, key, fallback) =>
  (T[table] && T[table][key]) || fallback || String(key ?? "");

window.T = T;
