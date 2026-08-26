<div align="center">

# 🛡️ nexus

**万尔玛集团 AI 中台** —— 零侵入接入四个存量业务系统（租户仓改动 0 行）· 路由不塌多样性(硬门) · 归因 0 误差(硬门) · 租户门禁不回退(硬门) · 降级不静默(硬门) · 每道门都配一个能跑的失败演示 · 纳美元整数账本 · 零构建 FinOps 控制台

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](#-许可证)
[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![LiteLLM](https://img.shields.io/badge/LiteLLM-provider%20adapter-4B32C3.svg)](https://github.com/BerriAI/litellm)
[![Postgres](https://img.shields.io/badge/Postgres-ledger%20BIGINT-336791.svg?logo=postgresql&logoColor=white)](db/schema.sql)
[![Langfuse](https://img.shields.io/badge/Langfuse-tracing-fbbf24.svg)](https://langfuse.com/)
[![CI](https://img.shields.io/badge/CI-offline%20tests-2088FF.svg?logo=githubactions&logoColor=white)](.github/workflows/ci.yml)
[![tests](https://img.shields.io/badge/tests-272%20passed-brightgreen.svg)](#-评测)
[![tenant edits](https://img.shields.io/badge/tenant%20repo%20edits-0%20lines-brightgreen.svg)](#-零侵入契约)
[![gates](https://img.shields.io/badge/gates-G1%E2%80%93G4%20exit%202-brightgreen.svg)](#-评测)

「武道AI / AI Engineering Dojo」**以阵制胜**系列 · 阵 06 · 收官之作 —— 中台的价值不在统一，在统一之后还守得住的边界

</div>

---

> **万尔玛（Wanmart）是虚构的零售集团**，量级对标沃尔玛，与任何真实公司无关。
> 五个租户里有四个是这个系列前四阵的**真实代码仓**，它们在 nexus 存在之前就已定稿——本仓库对它们**只读**。

一个 OpenAI 兼容的数据面网关，替万尔玛集团接管五条业务线的模型调用：统一入口、成本归因到每一次调用、路由受租户声明的约束、降级留痕、并在每次交付时**由平台自己举证**「接入没有让任何租户变差」。

**这个仓库最值钱的部分不是那四盏绿灯，是 [🔍 诚实的留白](#-诚实的留白) 和 [⚔️ 一次真实对抗](#️-一次真实对抗g1-拦下的东西长什么样)。** `python -m nexus.eval` 现在是 **exit 0**，四道门全绿。而同一套代码、一行不改，只放开 `policies/shopscout.yaml` 里三个 `pin`，就能让一个跨三家实验室的模型陪审塌成三份同一个模型、账单降 **91%**、**全部 HTTP 200、零报错、没有任何字段记录这件事发生过**——G1 是唯一会为此变红的东西。

## 📑 目录

- [✨ 特性](#-特性)
- [🏗️ 架构](#️-架构)
- [🤝 零侵入契约](#-零侵入契约)
- [🧱 技术栈](#-技术栈)
- [🚀 快速开始](#-快速开始)
- [💬 使用示例](#-使用示例)
- [📊 评测](#-评测)
- [⚔️ 一次真实对抗](#️-一次真实对抗g1-拦下的东西长什么样)
- [💰 接入与复用成本](#-接入与复用成本三个数不能相加)
- [🔍 诚实的留白](#-诚实的留白)
- [🔒 安全](#-安全)
- [🔭 可观测](#-可观测)
- [📁 项目结构](#-项目结构)
- [🧩 配置](#-配置)
- [🗺️ 路线图](#️-路线图)
- [📄 许可证](#-许可证)

## ✨ 特性

- 🧬 **按权重族而非按供应商判同（招牌）** —— G1 判定的地基是「两个模型算不算同一个」，而按供应商分族是**错的**：三家平台托管同一份开源权重是**一个**家族不是三个。按供应商去重的网关，会让 shopscout 那个刻意跨三家实验室的陪审退化成三份同一个模型，**而账面上完全合规**——三个供应商、三笔账单、看板上三个绿点。`registry/families.py` 因此写在代码里而不是 YAML 里，每条记录强制带 `basis` 说明判定依据，未登记的模型返回 `unknown` 而**不猜**。
- ⚖️ **路由提议，守卫否决** —— `policy/routing.py` 只知道怎么挑便宜的，它不知道、也**不该被信任知道**「便宜是否被允许」；那个判断归 `policy/diversity.py`，它有权驳回路由的任何决定。两者刻意不共用代码：G1 的证伪演练是「把路由换成贪心最便宜，确认门仍然抓得住」，**只有守卫独立于路由，那次演练才有东西可撞**。守卫比对的是模型本身而非路由自填的 `substituted` 字段——**被守的一方有权把守卫关掉，那就不是守卫**。
- 🤝 **零侵入接入（验收标准，不是加分项）** —— 接入一个租户**不需要改它的代码**，由 `scripts/verify_tenant.py` 在跑之前和跑之后各验一次。四个存量仓在整个开发过程中始终 `git status --porcelain` 为空——这不是自觉，是被断言强制的。第一次真接时四条链路一条都没通，见 [零侵入契约](#-零侵入契约)。
- 🪙 **纳美元整数账本** —— 金额单位不能是分：一个 $0.60/1M 的模型单 token 成本是 **0.00006 分**，整数分下每笔都是 0，账本会完美自洽地测不到任何东西。全系统用 int 纳美元（1e-9 USD），价格从**字符串**经 `Decimal` 转换，比一纳美元更细的价格**直接报错而不四舍五入**。Postgres 列是 `BIGINT`，测试断言 `2**53 + 1` 能原样往返。
- 🧾 **两家 cache 语义互不兼容，归一化在一处收敛** —— OpenAI 式 `prompt_tokens` 是**总数**、`cached_tokens` 是它的子集；Anthropic 式 `input_tokens` **不含**缓存部分、`cache_read` 与它并列。用错适配器会双记或漏记，而**两者都不报错、账本照样自洽**。防线是一条断言「两种约定归一化之后必须相等」的收敛测试。
- ⏹️ **结算写在 `finally`** —— 客户端中途断开时上游照收已生成的 token；只在成功路径结算等于中台无声吃下这笔成本，而缺口随流量增长，**看起来像定价错误而不是少写了一个分支**。`aborted`（产出过 token）与 `failed`（什么都没产出）分开记，合并就分不清「漏收」和「连不上」。
- 🔗 **账本记三段模型链** —— `requested → routed → served`，G1 判第一跳、G4 判第二跳。补上这两个字段之前，两道门在事后账本上**根本无从判定**：**一道门的判定能力，上限是被记下来的证据**。
- 🚦 **每道硬门都有一个能跑的失败演示** —— `--fail-demo g1|g2|g3|g4` 注入那道门存在的理由，然后你亲眼看它变红。四个演示各自**只**触发自己那道门（单独被测）：一个能同时触发三道门的演示，证明不了任何一道门在工作。
- 🧪 **跑批器把三种结局分开** —— `failed`（跑了说不行）/ `unverifiable`（什么都没跑，**这不是通过**）/ `metrics_unavailable`（跑了但输出格式变了，那是排版不是质量）。合并任意两种都会产出一个会撒谎的跑批器。
- 🔌 **离线优先** —— 默认上游是确定性假件，真实供应商靠 `UPSTREAM=litellm` 显式打开：**一次 `git clone` 加 `make test` 不该给任何人产生账单**。而控制台顶部因此常驻一条不可折叠的横幅声明当前跑的是不是真上游——对着假上游的控制台和对着真供应商的长得一模一样。
- 🖥️ **零构建控制台** —— 单文件 HTML，五块面板（成本归因 / 路由与被否决的替换 / 门禁矩阵 / 降级记录 / 配额），全部要鉴权，门禁面板是**三态**：把 `unverifiable` 画成绿灯的控制台比没有控制台更危险。

## 🏗️ 架构

```text
helpmate    shopscout   wealthwise   aura(云侧)   wuwork
 零侵入       零侵入        零侵入       零侵入       原生
   └───────────┴────────────┴────────────┴───────────┘
                          │  OpenAI 兼容 HTTP
                          ▼
┌─────────────────────── nexus ───────────────────────┐
│ ingress   鉴权（一租户一 key）→ 模型别名解析          │
│           （往下所有层只见规范 id，G1 判定不受影响）  │
│              │                                       │
│              ▼                                       │
│ policy    routing 提议 ─┬─▶ diversity 否决 [G1]      │
│           （只管挑便宜）│  （两者刻意不共用代码）      │
│                         ▼                            │
│           fallback 降级链 [G4]（pinned 无处可降）     │
│           quota 配额（预算 0 = 关停，不是无限）        │
│              │                                       │
│              ▼                                       │
│ ledger    usage 归一化 → meter 会话（finally 结算）   │
│           → book 落账（纳美元整数 · 三段模型链）      │
│           → reconcile 对账 [G2 的定义]                │
│              │                                       │
│ assurance isolation 只读校验 / conformance 跑批       │
│           / baseline 比对 [G3]                        │
│                                                       │
│ console   五块面板 + UPSTREAM 横幅                    │
└──────────────────────────┬───────────────────────────┘
                           ▼
            z.ai(GLM) · SiliconFlow(Qwen3/DeepSeek) · DashScope(Qwen3)
            ↑ 后两家托管同一份 Qwen3 权重 = 一个家族，不是两个

eval  ──▶  G1 · G2 · G3 · G4  ──▶  任一违规 exit 2
           每道门配一个 --fail-demo
```

- **`ingress`**（`ingress/`）—— OpenAI 兼容端点 + 一租户一 key 的鉴权 + **模型别名解析** + 非标端点白名单代理。别名在最外层解析是刻意的：往下所有层只见规范 id，所以权重族判定不会被租户的命名习惯影响。
- **`policy`**（`policy/`）—— 提议与否决分家。`fallback` 遵守同一条约束：**失败不豁免多样性**，pinned 模型无处可降，链为空而不是「降到一个凑合的」。`quota` 里预算 0 表示**关停**而不是无限，因为默认值最容易被读成后者。
- **`ledger`**（`ledger/`）—— 归一化 → 计量 → 落账 → 对账。只有**叶子调用**计费，父 span 不重复计。`reconcile()` 不是碰巧好用的 helper，**它就是 G2 的定义**，包含「`aborted` 行按下界判」那条规则；重写一遍不会更独立，只会多一份可以自由漂移的拷贝。
- **`assurance`**（`assurance/`）—— 唯一会去碰租户仓的东西，也因此是全仓最保守的一块：跑批前仓已脏就**拒绝开跑**（分不清是谁改的，事后还原会毁掉别人的工作），跑完还原跟踪文件、**列出但不删**新增的未跟踪文件，还原不回 CLEAN 就返回 `unverifiable`。
- **`console`**（`console/`）—— 它的存在理由是让 G1 拦下来的东西**被人看见**。路由日志为「被否决的替换」和「正常路由」维护**两条独立队列**，因为否决更早、更稀有，共用一条队列必然被冲掉。

## 🤝 零侵入契约

接入一个租户**不需要改它的代码**：base URL 从环境变量来，模型名由网关解析而不是强加给调用方。这就是这条契约的全部内容，由 `scripts/verify_tenant.py` 检查。

它**不**声称「跑租户自己的门禁没有副作用」——那对任何测试套件都不成立。评测会写报告文件，helpmate 的门禁每跑一次就重写一遍 `eval/report.md`。README 早先的版本声称租户 checkout 在跑批前后字节相同，**那是错的，而 G3 正是证伪它的东西**（详见 [诚实的留白](#g3-证伪了这份-readme-自己)）。

### 第一次真接：四条链路一条都没通

| helpmate 实际发出 | nexus 返回 | 根因 |
| --- | --- | --- |
| `model: "glm-4.7"` | **400** no price on file | nexus 只认 `zai/glm-4.7` |
| `model: "Qwen/Qwen3-8B"` | **400** no price on file | nexus 只认 `siliconflow/Qwen/Qwen3-8B` |
| `POST /v1/embeddings` | **404** | 没实现 |
| `POST /v1/rerank` | **404** | 实现在 `/rerank` |

最后那条最容易被「读代码而不是跑起来」漏掉：helpmate 把重排 URL 拼成 `{embed_base_url} + "/rerank"`，而那个 base 本身以 `/v1` 结尾。转发逻辑完全正确，错的是路径前缀。

模型命名那两条才是要害。**一旦要求租户把 `glm-4.7` 改成 `zai/glm-4.7`，「接入不改一行代码」当场作废**——而这个缺陷是在**全部单元测试保持绿色**的情况下存在的，因为套件里没有任何一条会发租户形状的模型名。

> **零侵入意味着网关说租户的方言**，而不是租户学网关的普通话。修法：`providers.ALIASES` 在最外层解析租户命名；`/v1/rerank` 与 `/rerank` 同时挂载；`/v1/embeddings` 实现并**计费**。

嵌入计费而重排**不**计费，这个不对称有理由：嵌入按 token 定价且供应商报了 token 数，有真数字可记；重排不按 token 定价，塞进 token 账本等于编一个数字，**而对账随后会认可这个编造**。缺口写进 [诚实的留白](#-诚实的留白) 而不是抹平。

第二次探针：四条全通。接入的全部内容是两个环境变量，**helpmate 仓内代码改动 0 行**。逐条实测见 `docs/integration-helpmate.md` 与 `docs/integration-shopscout.md`。

## 🧱 技术栈

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 网关 | **FastAPI** + uvicorn | 数据面反向代理；OpenAI 兼容端点 |
| 上游适配 | **LiteLLM**（可选 extra） | 只当 SDK 用，**策略层全自研**——适配层解决协议差异，不解决治理 |
| 策略 | 自研 `policy/` | 路由 / 多样性 / 降级 / 配额，四个模块彼此不共用判断逻辑 |
| 账本 | 内存 / **Postgres 16** | `cost_nanousd` 是 `BIGINT`，**不是** NUMERIC/DOUBLE |
| 配置 | **pydantic-settings** | **变量名无前缀**，与四个存量租户仓的约定一致 |
| 策略文件 | **YAML**（`policies/*.yaml`） | 租户声明约束；**权重族表刻意不在这里**，见下 |
| 可观测 | **Langfuse**（可选） | 一次网关调用 = 一条 trace，只带元数据不带内容 |
| 门禁 | 自研 `nexus.eval` | G1–G4 接 `exit 2`，各配 `--fail-demo` |
| 容器 | **Docker 两阶段** | 测试阶段有 `git`/`make`，运行时两个都没有 |
| 运行时 | Python 3.12 | `httpx[socks]` 而非裸 httpx——本机与租户仓都走 SOCKS 代理 |

> **权重族表在代码里，不在 Settings 也不在 YAML。** 两份拷贝必然拼写漂移，而漂移的表现是 **G1 静默失效**——这条和 medscope 把 `CRITICAL_LABELS` 挡在 config 外面是同一个理由。

## 🚀 快速开始

```bash
# 1. 建虚拟环境并安装
python3.12 -m venv .venv
make install                            # = pip install -e '.[dev]'，离线跑测试够了
# 要接真供应商或 Postgres，再补 extras：
# .venv/bin/pip install -e '.[dev,llm,pg]'

# 2. 跑测试（离线、hermetic、零 key）
make test                               # 272 passed, 8 skipped

# 3. 跑四道硬门
.venv/bin/python -m nexus.eval          # 任一违规 exit 2
.venv/bin/python -m nexus.eval --fail-demo g1   # ……以及它真能红的证明

# 4. 启动网关 + 控制台
make run                                # → http://localhost:8000/console?key=dev-wuwork
```

接真实供应商（**显式 opt-in**）：

```bash
cp .env.example .env
# 填 GLM_API_KEY / SILICONFLOW_API_KEY / DASHSCOPE_API_KEY
# 设 UPSTREAM=litellm
# 每个租户配一把 key：NEXUS_KEY_HELPMATE=... 等
make test-live      # 把 .env 载进 shell，额外跑 live 标记的用例
```

> `make test` 与 `make test-live` 是两条命令而不是一个开关：`tests/conftest.py` 在单测期间**禁用 `.env`**，所以躺在那儿的一个值不会漏进单元测试。**打到真供应商应该是你敲出来的，不是你继承来的。**

接 Postgres 账本：

```bash
docker compose up --build               # db + nexus，DATABASE_URL 已接线，UPSTREAM 仍是 fake
# 或本机 Postgres（与其余五个项目同结构同端口）：
createdb nexus && psql nexus -f db/schema.sql
export DATABASE_URL=postgresql://nexus:nexus@localhost:5432/nexus
make test-live                          # 277 passed, 3 skipped
```

镜像在构建时跑自己的整套测试。让测试阶段成为**门**而不是旁支的是最后那一行 `COPY --from=test /build/.tests-passed`——Docker 只构建被依赖的阶段，没有这个 COPY，测试可以整个被跳过而镜像照样打出 tag。**那个标记文件只在 pytest 退出 0 时存在。**

## 💬 使用示例

```bash
# 任何 OpenAI 客户端都能直接打；租户身份来自 Authorization
curl -s localhost:8000/v1/chat/completions \
  -H 'Authorization: Bearer dev-wuwork' -H 'Content-Type: application/json' \
  -d '{"model":"siliconflow/Qwen/Qwen3-8B","messages":[{"role":"user","content":"年假怎么休"}]}'

# 租户写自己的模型名也行——别名在最外层解析（这就是「零侵入」的实现）
curl -s localhost:8000/v1/chat/completions \
  -H 'Authorization: Bearer dev-helpmate' -H 'Content-Type: application/json' \
  -d '{"model":"glm-4.7","messages":[{"role":"user","content":"ping"}]}'

# 嵌入（计费）与重排（转发，不计费）
curl -s localhost:8000/v1/embeddings -H 'Authorization: Bearer dev-helpmate' \
  -H 'Content-Type: application/json' -d '{"model":"BAAI/bge-m3","input":["hello"]}'

# 跨租户读用量：默认拒绝，wuwork 是唯一被授权的一个，且拒绝也留痕
curl -s 'localhost:8000/v1/usage?tenants=helpmate,shopscout' -H 'Authorization: Bearer dev-wuwork'

# 控制台五块面板（全部要鉴权）
curl -s localhost:8000/console/costs     -H 'Authorization: Bearer dev-wuwork'
curl -s localhost:8000/console/routing   -H 'Authorization: Bearer dev-wuwork'   # 含被否决的替换
curl -s localhost:8000/console/gates     -H 'Authorization: Bearer dev-wuwork'   # 三态
curl -s localhost:8000/console/mode      -H 'Authorization: Bearer dev-wuwork'   # 真上游还是假件
```

> 本机若有全局 SOCKS 代理，打 localhost 要加 `--noproxy '*'`，否则会收到一个来自代理的 503，很容易被误读成网关挂了。

原生租户 wuwork（集团职能助手：短问答 / 会议纪要 / 跨业务线运营日报）：

```bash
make wuwork-eval
# GATE PASSED over 12 cases
# {"retrieval_accuracy": 1.0, "refusal_correctness": 1.0, "n_cases": 12}
```

## 📊 评测

```bash
.venv/bin/python -m nexus.eval                  # 四道门；任一违规 exit 2
.venv/bin/python -m nexus.eval --fail-demo g2   # 注入 G2 要抓的那种失败
```

| 门 | 它防的那件事 | 判据 | 当前 |
| --- | --- | --- | --- |
| **G1** 异质性约束 | 路由优化把租户声明的模型多样性收敛掉 | 请求路径守卫 + **事后账本独立复核**（从租户策略重新推导，不比家族） | ✅ passed |
| **G2** 归因 0 误差 | 账本与上游实际计费对不上 | `reconcile()`；`aborted` 行按**下界**判 | ✅ passed |
| **G3** 门禁不劣化 | 接入之后租户自己的指标掉了 | 跑租户门禁 + 与接入前 baseline 比对 | ⚠️ 见下 |
| **G4** 降级不静默 | 故障切换到弱模型而调用方不知情 | `fallback_from` 做**一致性校验**而非打勾 | ✅ passed |

单测：**272 passed, 8 skipped**（离线）／**277 passed, 3 skipped**（接 Postgres）。44 个测试模块、3553 行源码。

### 一条原则

> **任何能让交付失败的门，都必须有一个已被演示过的失败方式。**

在这个仓里它有了可执行形式：`--fail-demo <gate>` 注入那道门存在的理由，然后你亲眼看它变红。「这道门能失败」从此是一条谁都能跑的命令，不是 commit message 里的一句声称。

```text
$ python -m nexus.eval --fail-demo g1
G1: FAILED (1)
  G1 call demo: tenant 'shopscout' asked for zai/glm-4.6, routing chose
  siliconflow/Qwen/Qwen3-8B (family 'qwen3'), which the policy does not permit
G2: passed
G4: passed
exit=2
```

几条容易被绕开的判定，都是刻意堵死的：

- 账本里出现一个**没有策略的租户名**算违规不算跳过——否则「把策略文件删掉」就成了通过 G1 的办法。
- **无 baseline 的租户算违规不算通过**——让 G3 变绿最便宜的办法，就是不再产出那个数字。
- **有 baseline 却没跑**也算违规——一道停跑的门并没有开始通过。
- `distinct_families()` 把 `unknown` **排除在计数之外**而不是当成独立家族——两个不认识的模型 id 不是「两个家族」的证据，反过来算会让一个失修的注册表凭空满足多样性要求。

### G3 有两条臂，证明的不是一回事

**离线臂**跑 wuwork 的门禁，而 wuwork 的门禁完全离线、**一次都不经过网关**：它能证明「wuwork 自己仍然正常」，**不是**「接入 nexus 之后没变差」。**live 臂**把真实租户的门禁指向 nexus 再跑一遍，与接入前采的 baseline 比——那才是 G3 的本意，需要真 key 与真时间，走 `make test-live`，缺凭据就跳过。

只做离线臂而不说明，就是给一道够不着目标的检查挂上目标的名字。

### 跑批器指向四个存量仓的实测

| 租户 | 命令 | exit | 指标 | 结论 |
| --- | --- | --- | --- | --- |
| shopscout | `make eval` | 0 | 11 项 | 完整 baseline 已采 |
| wealthwise | `make eval` | 0 | 36 项 | 完整 baseline 已采 |
| aura | `make test` | 0 | **0 项** | 只有通过与否，无评测可报（这不是缺陷） |
| helpmate | `make gate` | — | — | 53 例真实检索与生成，**超出 200 秒跑批预算**，归 live 臂 |

三条发现，没有一条来自读代码：跑批器**必须激活租户的 venv**（helpmate 的 Makefile 写的是裸 `python -m ...`，不激活就会把它报成「门禁失败」，而 helpmate 什么问题都没有）；指标解析器**只认紧凑 JSON**（wealthwise 是缩进输出，于是一个正常吐指标的租户被报成「无指标」，而 baseline 比对随后会拿「空」去比）；**aura 没有指标可报，如实记录好过为了让表格整齐编一个出来**。

## ⚔️ 一次真实对抗：G1 拦下的东西长什么样

shopscout 的正确性建立在模型的**差异**上：三模型陪审刻意跨三家实验室，分歧就是它据以行动的信号。在接入 nexus 之前，这个差异是**物理保证的**——它自己的配置里有两个不同的 `base_url`，没有任何代码路径能意外让三个陪审员变成同一个。**把一切路由到一个网关，就把这个物理事实换成了一句承诺。**

攻击就是把 `policies/shopscout.yaml` 里三个 pin 放开——写一份「一个不知道陪审是干什么的平台工程师在做成本优化时会写的」配置。**代码一行未改，同一个二进制、同一个请求、同一个租户。**

| | 放开前 | 放开后 |
| --- | --- | --- |
| 陪审席 1 | `zai/glm-4.6` · glm · $0.000033 | `siliconflow/Qwen/Qwen3-8B` · qwen3 · $0.000002 |
| 陪审席 2 | `siliconflow/Qwen/Qwen3-235B-A22B` · qwen3 · $0.000020 | `siliconflow/Qwen/Qwen3-8B` · qwen3 · $0.000002 |
| 陪审席 3 | `siliconflow/deepseek-ai/DeepSeek-V3` · deepseek-v3 · $0.000016 | `siliconflow/Qwen/Qwen3-8B` · qwen3 · $0.000002 |
| **不同权重族** | **3** | **1** |
| **陪审总成本** | $0.000069 | $0.000006（**降 91%**） |
| **HTTP 状态** | 全部 200 | **全部 200** |
| **报错 / 告警** | 无 | **无** |

shopscout 的合规裁决所依赖的交叉验证在这一刻死了。**而它死掉的方式是：一切正常，且更便宜。**

这次对抗还顺手回答了「为什么分族要按 checkpoint」：收敛之后三个陪审员用的是同一个模型 id，按供应商分族也能看出问题；但如果放开的白名单让它们分别落到 SiliconFlow 与 DashScope 托管的**同一份 Qwen3 权重**上，按供应商分族会看到「两个不同的家族」——**而那是两份相同的权重**。

演示后策略文件即刻还原，`git diff policies/` 为空。完整记录见 `docs/integration-shopscout.md`。

## 💰 接入与复用成本：三个数，不能相加

| | 行数 | 它回答的问题 |
| --- | --- | --- |
| 接入 | **57** | 一条新业务线连上网关本身要付的（`client.py` + `config.py` 的接入部分） |
| 复用 · 租户侧 | **73** | 机制已存在之后，*下一个*团队基于别的业务线的数据做东西要付的（`digest.py` 55 + `get_usage` 18） |
| 复用 · 平台侧 | **121** | 一次性付清（审计 52 + `/v1/usage` 67 + 授权字段 2）。**第二个想跨线的租户一分不付** |

加起来是 251，而 251 不回答上面三个问题里的**任何一个**。这三个数分别属于三个不同的决策者：要不要接、要不要复用、要不要建这个机制。

平台侧那 121 行还有第二重含义——它是**让复用变安全而不只是变方便**的诚实标价。没有授权字段和审计留痕，跨租户读数据对租户来说成本是**零**，代价是集团失去隔离。

## 🔍 诚实的留白

这一节记录**已知不成立或够不着的部分**。它不是待办清单的委婉说法，而是判断本项目结论可信到什么程度的**唯一依据**。上面那些绿灯必须连同这一节一起读。

### G3 证伪了这份 README 自己

跑完 helpmate 的 `make gate` 之后，它的 `eval/report.md` 被改写了——那是个 **git 跟踪的**文件，内容是一次真实的重新生成（样本 50→53、新增 `tenant_isolation` 指标、阈值 0.7→0.88）。而 README 当时写着「租户 checkout 在跑批前后 `git status --porcelain` 必须为空」。**两句话不能同时为真。**

这不是断言失灵——`assurance/isolation.py` 完全正常，它就是用来抓这个的；**问题是 G3 亲手制造了它自己要检测的违规**。真正该守、也真正有价值的断言是「接入不需要改租户代码」，当初顺手扩成了「跑租户的门禁没有副作用」，而后者对任何测试套件都不成立。

契约因此收窄成现在这句，跑批器改成：已脏则拒绝开跑 → 跑完还原跟踪文件、列出但不删未跟踪文件 → 还原不回 CLEAN 就报 `unverifiable`。**一个要求「门必须有被演示过的失败方式」的项目，它的门证伪的第一个东西是它自己的文档。**

### 对账不独立

假上游是自己算自己收了多少，所以对账真的是「跟外部比」。换成真供应商后，唯一的数据来源就是响应里的 usage——**拿它对账等于账本跟自己对账**。

能保住的部分：`charges()` 从**归一化之前的原始载荷**计价，账本走归一化后计价，两条路径分开，所以归一化 bug（也就是上面那个 cache 语义混淆，最可能出现的真 bug）仍然会被抓住。抓不住的部分：供应商自己报错数。真正独立的信源是供应商的**账单 API**，本项目不接。

### 中断的流式只给下界

流正常结束时供应商回一个 usage 帧，中断时没有，nexus 只能数它见过的分片——**而分片不是 token**。所以 `status = 'aborted'` 的行是一个**下界**，对账断言从「相等」改成「账本 ≤ 上游」。

这不是放宽标准，是把标准说准；而且下界仍然是界——**aborted 行超额计费照样报错**，「我们做了近似」不是向上近似的许可证。

### aura 的端侧用量永远进不了账本

边缘侧**从不经过这个网关**，回填它需要改租户仓，而零侵入契约禁止这件事。所以 aura 在账本里只有云侧那一半，这是设计的直接后果而不是遗漏。

### 零侵入租户的归因粒度只到租户/工作负载

调用链级别的归因（`trace_root` / `parent_span_id`）只对**原生租户**成立。零侵入租户的仓没有改动，也就没有办法把 trace 根一路传下来——`db/schema.sql` 里 `trace_root` 因此可空，且注释写明「为什么可空」。

### 重排转发但不计费

重排不按 token 定价。把它塞进 token 账本等于**发明一个数字**，而对账随后会确认这个发明——**一个被对账确认过的编造，比一个公开的缺口危险得多**。缺口记在这里。

### 审计只活到下次重启

跨租户读取的审计目前在内存里。`cross_tenant_read_audit` 表在 `db/schema.sql` 里已经建好，但**还没有代码往里写**，所以「谁读了谁的用量」这份记录随进程一起消失。一份只能回答到下次重启的审计，值得说出来。

### helpmate 的门禁不在离线臂里

它跑 53 个 golden case、走真实检索与生成、连真数据库，比一次跑批该阻塞的时间长。它属于 G3 的 live 臂，**离线臂覆盖不到它**。

### 八次「测试为错误的理由通过」

开发全程抓到 **8 次**测试为与被测行为无关的理由变绿，**八次全部由「故意打断实现，看有没有测试变红」发现，没有一次是靠读代码看出来的**——因为它们读起来都是对的。

| # | 那条测试声称在测 | 它实际为什么绿 |
| --- | --- | --- |
| 1 | 配置默认值没被 `.env` 污染 | 断言写的是 `timeout > 0`，泄漏进来的 `999` 同样满足（现在断言 `== 60`） |
| 2 | 非 git 目录判定为 UNVERIFIABLE | 若 runner 的临时目录恰在某个 checkout 里，`git status` 会成功并报告**外层那个仓** |
| 3 | `allow_fallback=false` 使降级链为空 | 那个租户的两个模型本来就 pinned，**把 allow_fallback 检查整个删掉它照样绿** |
| 4 | 检索结果真的进了 prompt | 锚点词「报销」出现在**问题本身**里 |
| 5 | 跨租户读取默认为空 | 加载路径每次都显式传该字段，**类级默认是死代码** |
| 6 | 预算为 0 时配额面板不误报 | 断言写在 `if budget == 0` 里，而没有任何租户预算为 0，**循环体一次都没执行过** |
| 7 | 前端某处有解释性 tooltip | 只断言中文串在页面里；把 `title=` 改名成 `data-x=`，tooltip 没了、测试还绿 |
| 8 | 多样性守卫不信路由的自述 | 实现读的正是路由的自述；改回旧写法**只有一条测试变红，还是自审时补的那条** |

归成三类：**断言太宽**（#1 #4 #7）、**前提没被断言**（#2）、**被测路径根本没执行**（#3 #5 #6）。#8 单独一类，那是实现的毛病：文档说不信路由，代码却在读路由的自述。

顺带抓到一个真 bug：路由日志刻意拆成两条独立队列以防「被否决的替换」被正常路由冲掉，而 `events()` 结尾一句 `merged[-capacity:]` **把两条各自有界的队列合并后又截了一刀**，否决记录 **100% 被丢弃**——正是这个设计存在的全部理由。修法不只是删掉那行，还补了一条钉住「capacity 是每类上限而非总量」的测试。

## 🔒 安全

- **一租户一 key，从环境读**（`policies/<tenant>.yaml` 里的 `api_key_env` 指定变量名）。空 key **不建索引**——否则所有未配置的租户会共享同一个空凭据；重复 key **直接拒绝启动**，因为它会让两个租户互相冒充而不报错。
- **跨租户读用量默认拒绝**。`cross_tenant_read` 是显式白名单，五个租户里只有 `wuwork` 有（集团财务写运营日报），而且**部分授权整体拒绝**：请求里只要有一个未授权目标，整个请求拒绝而不是静默返回子集。**拒绝也要留痕。**
- **审计不记金额**，只记「谁在什么时候读了谁」。金额本身在账本里，审计表再存一份就是两个可以漂移的真源。
- **重排不转发租户的 key**：网关用自己的凭据打上游，租户的 key 只用于对网关鉴权。
- **默认不打真上游**。`UPSTREAM=fake` 是默认值，`make test` 离线且 hermetic，`tests/conftest.py` 在单测期间禁用 `.env`。
- **容器非 root**（`uid 10001`），运行时镜像里**没有** `git` 和 `make`——那两个只有构建期的测试阶段需要。
- **CI 里没有任何凭据**，需要凭据的测试按设计自行跳过。**刻意不跑 live 套件**：一个因为缺 secret 而红的 CI，一周之内就会被加上 `continue-on-error`，而一个没人信的 CI 比没有 CI 更坏。

## 🔭 可观测

- **Langfuse**（可选、非致命）—— 一次网关调用 = 一条 trace，**只带元数据不带内容**：租户、工作负载、模型链、token 数、纳美元成本、状态。两把 key 都配齐才启用，缺一个就不构造 client；接不上不影响请求，更不影响门禁离线跑。
- **控制台五块面板** —— 成本归因（按租户 × 模型）/ 路由与**被否决的替换** / 门禁矩阵（三态）/ 降级记录 / 配额。全部要鉴权。
- **UPSTREAM 横幅** —— 常驻顶部、不可折叠，声明当前跑的是真上游还是确定性假件。理由：对着假上游的控制台和对着真供应商的**长得一模一样**，成本在涨、路由在跑、门禁在绿，而数字全是假的。**这条信息的价值不在于它复杂，在于它必须在你不去找的时候就在那儿。**
- **Postgres 账本** —— `ledger_entry` 逐字段往返验证，`call_id` 唯一索引，`(tenant, ts)` 与 `trace_root` 各有索引。

## 📁 项目结构

```text
nexus/
├── src/nexus/
│   ├── money.py                # 纳美元整数金额（分做不到）
│   ├── providers.py            # 模型 id → 传输，单向；ALIASES 租户方言表
│   ├── upstream.py             # Upstream Protocol + FakeUpstream + 价目表
│   ├── upstream_litellm.py     # 真实适配；charges() 走**原始**载荷
│   ├── state.py                # 进程级装配，反转依赖解掉循环导入
│   ├── audit.py                # 谁读了谁的用量（不记金额）
│   ├── routing_log.py          # 否决与放行分两条队列，capacity 是每类上限
│   ├── obs.py                  # Langfuse，可选且非致命
│   ├── eval.py                 # ★ G1–G4，唯一能让交付失败的入口
│   ├── registry/
│   │   ├── families.py         # 权重族注册表（G1 判定核心，强制 basis 字段）
│   │   └── tenants.py          # 租户策略；替换默认拒绝，跨租户读默认为空
│   ├── policy/
│   │   ├── routing.py          # 提议：只管挑便宜
│   │   ├── diversity.py        # 否决：G1 本体，比对模型而非路由自述
│   │   ├── fallback.py         # G4：失败不豁免多样性，pinned 无处可降
│   │   └── quota.py            # 预算 0 = 关停，不是无限
│   ├── ledger/
│   │   ├── usage.py            # 两家 cache 语义归一化（收敛测试钉死）
│   │   ├── session.py          # 结算写在 finally，aborted / failed 分开
│   │   ├── book.py             # 账本 + reconcile（**G2 的定义**）
│   │   └── pg.py               # Postgres 实现，BIGINT 往返验证
│   ├── ingress/
│   │   ├── api.py              # /v1/chat/completions
│   │   ├── auth.py             # 一租户一 key；空 key 不建索引，重复 key 拒绝
│   │   ├── streaming.py        # 流式计量，走掉的客户端也结算
│   │   ├── passthrough.py      # /v1/embeddings（计费）· /rerank（转发不计费）
│   │   └── usage_api.py        # /v1/usage，部分授权整体拒绝
│   ├── assurance/
│   │   ├── isolation.py        # CLEAN / DIRTY / UNVERIFIABLE 三态
│   │   ├── conformance.py      # 跑批器：激活租户 venv，脏仓拒跑，跑完还原
│   │   └── baseline.py         # 软指标有容差，硬指标容差为 0
│   └── console/
│       ├── api.py              # 五块面板 + /console/mode
│       └── static/console.html # 零构建单文件前端
├── tenants/wuwork/             # 原生租户；AST 测试禁止它 import nexus.*
│   ├── client.py config.py     # 接入面（57 行的那部分）
│   ├── retrieve.py qa.py       # 离线哈希嵌入 + 词面覆盖不够就拒答
│   ├── minutes.py              # 会议纪要与行动项，三条"不编造"
│   ├── digest.py               # 跨业务线运营日报（复用面，读不到就说读不到）
│   ├── eval.py golden.json     # wuwork 自己的门禁（G3 离线臂）
│   └── corpus/ samples/
├── policies/*.yaml             # 五个租户的策略声明
├── baselines/*.json            # 接入前采的基线，带 captured_at
├── db/schema.sql               # ledger_entry(BIGINT) + cross_tenant_read_audit
├── docs/                       # integration-helpmate / -shopscout / wuwork
├── scripts/verify_tenant.py    # 零侵入校验：跑前跑后各验一次租户仓
├── tests/                      # 45 个文件，272 passed / 8 skipped
├── .github/workflows/ci.yml    # 只跑离线，刻意不跑 live
├── Dockerfile                  # 两阶段；COPY --from=test 让测试成为门
└── docker-compose.yml
```

> **`tenants/wuwork/` 不许 `import nexus.*`**，由一个走 AST 的测试强制。它住在同一个仓里只是为了方便，但它是**租户**。一旦出现一句 `from nexus.money import ...`，「新业务接入成本」这个数字就会悄悄从「一个外部团队接入要多久」变成「在同一个代码库里写代码有多快」——那是另一个问题，而且无趣得多。

## 🧩 配置

`.env`（见 `.env.example`）关键项 —— **变量名无前缀**，与四个存量租户仓的约定一致：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `UPSTREAM` | `fake` | `fake`（确定性假件）\| `litellm`（**打真供应商**）。默认值是 opt-in 的那一侧：一次 `clone` + `make test` 不该给谁产生账单 |
| `UPSTREAM_TIMEOUT_S` | `60` | 上游超时。测试断言的是**精确值 60** 而非 `> 0`——后者对一个泄漏进来的值同样绿 |
| `POLICIES_DIR` | `policies` | 租户策略目录 |
| `DATABASE_URL` | 空 | 空 = 内存账本。**pg 测试从 shell 读它，不从 `.env` 读**（conftest 在单测期间禁用 `.env`），用 `make test-live` |
| `NEXUS_KEY_<TENANT>` | 空 | 一租户一把；变量名由 `policies/<tenant>.yaml` 的 `api_key_env` 指定。空 key 不建索引，重复 key 拒绝启动 |
| `GLM_API_KEY` / `SILICONFLOW_API_KEY` / `DASHSCOPE_API_KEY` | 空 | 上游凭据，仅 `UPSTREAM=litellm` 时用到 |
| `RERANK_BASE_URL` | `https://api.siliconflow.com/v1` | 重排是供应商扩展而非 OpenAI 表面的一部分，所以自带 base 与 key 而不搭模型注册表的车 |
| `RERANK_API_KEY_ENV` | `SILICONFLOW_API_KEY` | 指定去哪个变量取重排凭据 |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | 空 | **两个都填**才启用 tracing，缺一个就不构造 client |
| `DEFAULT_CURRENCY_UNIT` | `nanousd` | 暴露成设置只是为了可发现，**不是为了可修改** |

租户约束写在 `policies/<tenant>.yaml`，不在 `.env` 里：

```yaml
tenant: shopscout
integration: zero_touch                 # zero_touch | native
repo_path: ~/ai_projects/shopscout      # 跑批器只读地指向它
gate_command: make eval                 # G3 要跑的那条命令，来自策略而非硬编码
api_key_env: NEXUS_KEY_SHOPSCOUT
allow_fallback: true
budget_nanousd_per_day: 2000000000      # $2.00/day
models:
  zai/glm-4.6: {}                       # 省略 substitutable_to 即 pin —— 默认拒绝
  siliconflow/Qwen/Qwen3-235B-A22B: {}
  siliconflow/deepseek-ai/DeepSeek-V3: {}
```

> **权重族表不在这里**——单一真源是 `registry/families.py`。放两处必然拼写漂移，而漂移的表现是 **G1 静默失效**。
> **`substitutable_to` 省略即 pin**：替换是默认拒绝的。反过来（默认允许、靠显式 pin 保护）意味着任何一个忘了写 pin 的租户都在裸奔。

## 🗺️ 路线图

- [x] **P1 地基** —— 纳美元整数金额 / 权重族注册表 / 租户策略 / 鉴权 / 用量归一化 / 计量会话 / 账本与对账 / 配额 / 零侵入断言
- [x] **P2a 策略层** —— 路由提议 / 多样性否决（G1 本体）/ 降级链（G4）/ 网关骨架 + 端到端落账 / 流式计量 / 非标端点白名单代理
- [x] **P2b 真实上游** —— LiteLLM 适配 / 真实流式 / `/rerank` 真转发 / Postgres 账本逐字段往返 / Langfuse trace（可选非致命）/ live 测试显式退出隔离
- [x] **P3a wuwork + 跑批器** —— 原生租户（短问答 / 会议纪要 / 运营日报）+ 自己的离线门禁；G3 跑批器与 baseline 比对；四个存量仓实测
- [x] **P3b 四道门接 exit 2** —— G1 事后账本独立复核 / G2 复用 `reconcile` 的定义 / G3 两条臂 / G4 三段模型链；每门一个 `--fail-demo`
- [x] **P3c 跨租户复用 + 控制台 + 交付物** —— 授权字段 + 审计 + `/v1/usage`（部分授权整体拒绝）/ 五块面板 + UPSTREAM 横幅 / 两阶段 Dockerfile / compose / CI
- [x] **零侵入实测** —— helpmate 与 shopscout 两条真实链路打通，**租户仓改动 0 行**；第一次探针四条全挂的记录原样留在 `docs/`
- [x] **对 G1 的一次真实攻击** —— 放开三个 pin：权重族 3→1、账单降 91%、全部 200、零报错
- [ ] **审计落库** —— 表已建、代码未写。**在此之前审计只活到下次重启**，见[诚实的留白](#审计只活到下次重启)
- [ ] **对账接供应商账单 API** —— 现在拿供应商自己的响应对账，等于账本跟自己对账。真正独立的信源是账单 API
- [ ] **G3 live 臂进 CI** —— 需要凭据与时间预算；直接塞进现有 CI 会得到一个「缺 secret 就红」的流水线，而那种流水线一周内就会被加 `continue-on-error`
- [ ] **aura 端侧用量** —— 在零侵入契约不变的前提下**无解**：边缘从不经过网关，回填要改租户仓
- [ ] **重排计费口径** —— 需要一个不是编出来的计价方式；在那之前它转发但不计费

## 📄 许可证

[MIT](LICENSE) © Kevin Tu · 武道AI 工程修炼系列。

nexus 是**教学 Demo**。万尔玛（Wanmart）是虚构集团，与任何真实公司无关；仓内涉及的四个租户是本系列前四阵的开源项目，本仓库对它们只读，从未修改。
