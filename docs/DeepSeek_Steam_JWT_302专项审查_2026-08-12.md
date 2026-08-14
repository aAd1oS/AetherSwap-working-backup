# DeepSeek 专项审查报告：Steam JWT 302 循环

- 审查日期：2026-08-12
- 审查范围：只读；仅新增/修改本报告文件。未修改任何业务代码、配置、数据库。
- 证据来源：`app/services/steam_auth.py`、`app/routes/auth.py`、`steam/session.py`、`app/config_loader.py`、`app/accounts.py`、`app/services/account_region.py`、`app/gift_engine.py`、`app/account_scope.py`、`utils/proxy_manager.py`、`diagnostics/steam_auth_flow_probe.py`、三个测试文件、两份探针日志、`git diff`（5 个指定文件 + accounts/config_loader/gift_engine 的 diff stat）。
- 术语约定：**已证实**＝有代码/日志直接证据；**高概率推断**＝证据链闭合但缺一个环节；**待验证**＝无证据、需最小实验。

---

## 1. 结论摘要

| 排名 | 根因 | 置信度 |
|---|---|---|
| 1 | **已保存的凭证字符串结构性缺失 `steamRefresh_steam`（login.steampowered.com 域刷新令牌）**。steampy 登录路径根本不采集 login 域 cookie（`steam_auth.py:438-440` 只取 steamcommunity.com 与 store.steampowered.com 两域）；Playwright 路径捕获时机不保证刷新令牌已落 jar 且不校验（`auth.py:270-283`）。于是 `/jwt/refresh` 请求携带 **零个** login 域 Cookie，Steam 返回 302 且**不签发任何 Set-Cookie** → 循环。两份日志中 `/jwt/refresh` 响应均为 `Response Set-Cookie: <none>`，与"缺刷新令牌"完全吻合。 | **高（~60%）** |
| 2 | **底层会话在 Steam 侧处于"需要 Web 刷新"状态（jar 中 `steamDidLoginRefresh=1`），且真实浏览器内的刷新舞蹈可能也无法完成**（新建 Playwright 资料登录后仍循环）。若浏览器舞蹈能完成，`goto(..., wait_until="commit")` 之后捕获必然已含刷新令牌——其缺失说明浏览器侧舞蹈失败，即账号/会话/设备在 Steam 侧被拒绝完成 JWT 刷新（令牌已轮换/吊销、风控、需重新验证等）。 | **中（~25%）** |
| 3 | 8/11–8/12 JWT 排查改动把验证从 store JSON API 改为 community 优先 + 有界手动 302 + 域作用域 Cookie 装载，使上述缺陷**显形并放大**：旧验证对循环返回 `True`（旧代码 `except → return True`）掩盖问题；新代码走到 market 兜底，而 market 同样 302。属"暴露问题的改动"，非根因本身。 | **中低（~10%）** |
| 4 | 网络/代理出口差异（H4）、Steam 全局行为变更（H6）——两种代理链路（WattToolkit / Clash）行为完全一致、两域 Server 头一致（`nginx` / `nginx, WattToolkit`），无证据支持。 | 低（~5%） |

> 关键判断：**这不是"Cookie 过期"**。社区 302 响应中 `steamCountry`/`steamDidLoginRefresh` 的 Set-Cookie 被正常处理入库，说明 CookieJar 工作正常、会话令牌本身存在；缺的是**登录域刷新令牌**。

---

## 2. 已证实事实

（均有文件/行号或日志行号）

1. 保存的凭证只有 `.steamcommunity.com` 域 cookie：`browserid, sessionid, steamCountry, steamDidLoginRefresh, steamLoginSecure, timezoneOffset`；**没有任何 `steamRefresh_*`**（`log/steam_auth_flow_probe_20260812_002901.txt:6`；`..._20260811_234707.txt:6`）。
2. `/jwt/refresh`（login.steampowered.com）302 响应的 Set-Cookie 均为 `<none>`（两份日志 step 2/4：`Response Set-Cookie names/domains: <none>`），且 session jar 也无新增 → 服务端确实未签发（或值非法，见 §4 H1）。
3. 社区 302 响应的 Set-Cookie（`steamCountry`、`steamDidLoginRefresh`，host-only `@steamcommunity.com`）被正常写入两个 jar（`..._002901.txt:11-12,25-26`）→ Requests CookieJar 对 Steam 的 Set-Cookie 处理正常，无系统性拒收。
4. 循环序列固定：`/my/profile` 302 → `/jwt/refresh` 302 → `/my/profile` …；`/market/` 同样 302 到 `/jwt/refresh`（`..._002901.txt:8-41`）。
5. steampy 登录后合并 cookie 只取两个域：`client._session.cookies.get_dict(domain='steamcommunity.com')` 与 `domain='store.steampowered.com'`（`app/services/steam_auth.py:438-440`）。requests 的 `get_dict(domain=...)` 对 host-only `login.steampowered.com` cookie（如 `steamRefresh_steam`）两个调用都不匹配 → **结构性丢失**。
6. `_load_steam_auth_cookies` 按名称前缀重建域：仅 `steamrefresh*` → `.steampowered.com`，其余全部 → `.steamcommunity.com`（`steam_auth.py:46-54`）。凭证字符串中没有 steamrefresh 名称时，发往 `login.steampowered.com` 的请求 Cookie 头为空。
7. Playwright 捕获：用户点击"完成"后 `page.goto("https://steamcommunity.com/my/", wait_until="commit", timeout=15000)`，随后**立即** `context.cookies()`，无等待刷新令牌、无事后校验（`app/routes/auth.py:270-283`）。
8. `_relogin_worker` 对 Steam 打开的是 `store.steampowered.com/login/`（`auth.py:253`）——完成登录后社区 SSO 舞蹈由 worker 自己的 `goto(my/)` 触发，捕获时机与舞蹈完成状态无任何同步。
9. 8/11–8/12 diff 确认的改动（`git diff` 5 个文件）：`_check_steam_cookies` 重写（community 优先、有界刷新、market 兜底）、`_load_steam_auth_cookies` 域作用域装载、`_select_steam_browser_cookies` 按名去重、Playwright 捕获改 commit 语义、浏览器代理参数、按账号保存（`auth.py:296-301`，`update_steam_creds(..., account_id=...)` 经 `account_scope.update_account_steam_credentials` 落库，`app/account_scope.py:115-133`）。
10. 探针脚本的 `safe_target` 刻意不打印 query（`diagnostics/steam_auth_flow_probe.py:30-32`）；代码中 `urljoin(response.url, location)` 完整保留 Location（含 query）并原样发送（`steam_auth.py:120,135-139`）→ **query 未被代码删除**，日志不显示是安全设计。
11. gift_engine 与 `create_market_session` 使用 `requests.get(..., cookies=dict)` 或 `session.cookies.update(dict)`（hostless，`gift_engine.py:13-18,283-287`；`steam/session.py:62`）——hostless cookie 会发给**所有**主机，这正是旧购买链路（含 checkout `sessionid`）能工作的原因。

---

## 3. 发现项（P0 / P1 / P2）

### P0-1　steampy 登录产物永远缺失 login.steampowered.com 刷新令牌
- 文件/行号：`app/services/steam_auth.py:438-440`
- 触发条件：任何走 `_do_steampy_login` 的自动登录/重登（`_try_steam_auto_relogin_impl`、`verify_steam_auto_login`）。
- 后果：新登录保存的凭证同样无 `steamRefresh_steam`；凡社区会话带 `steamDidLoginRefresh=1`（steampy 客户端式登录必然产生），后续验证必进入 jwt 刷新循环。**每次重登都无法自愈**。
- 证据：日志初始 jar 无任何 steamrefresh 名称（两份日志 line 6）；`get_dict` 只覆盖两域；requests `get_dict(domain=)` 的域后缀匹配对 host-only `login.steampowered.com` 返回空。
- 分类：已证实（代码结构性缺失）。

### P1-1　Playwright 捕获时机与 SSO/刷新舞蹈不同步，残缺凭证被当成功保存
- 文件/行号：`app/routes/auth.py:272-283`（`goto(my/, wait_until="commit")` → 立即 `context.cookies()` → 保存），`:253`（登录页为 store）。
- 触发条件：用户登录后点击"完成"，worker 的 `goto` 触发社区 SSO 舞蹈；`commit` 语义只保证最终响应开始到达，**不保证 login 域刷新令牌已写入 jar**；15s 超时后 `except: pass` 照样捕获保存（`auth.py:275`）。
- 后果：新 Playwright 资料登录仍保存残缺凭证 → 后续验证循环（与"新建资料仍相同结果"一致）。
- 证据：`auth.py:270-283` 无任何"等待 `steamRefresh_steam` 出现"或"捕获后校验"逻辑；`_select_steam_browser_cookies`（`auth.py:90-107`）对 `steamRefresh_steam` 不会误删（测试 `test_auth_relogin_state.py:67-98` 已证实会保留）→ 缺失只能来自"浏览器 jar 里没有"或"时机早于写入"。
- 分类：已证实（代码无同步/校验）＋"浏览器内舞蹈是否完成"待验证（见 R2）。

### P1-2　验证代码把"刷新响应无 Set-Cookie"与"缺刷新令牌"混为一谈，无前置检查
- 文件/行号：`app/services/steam_auth.py:135-160`（刷新请求 → 302 即进入下一跳判断 → 循环才走 market 兜底 `:157-160`；market 也 302 → `unavailable`）。
- 触发条件：jar 中无任何 `steamrefresh*` 时仍发 4 步循环请求。
- 后果：诊断信息只报"出现循环"，无法区分"缺令牌"与"令牌失效"；且消耗社区请求配额，放大 429 风险（`_STEAM_RATE_LIMITED_MESSAGE` 的保护被自身请求量消耗）。
- 证据：`_load_steam_auth_cookies` 装载时即可得知是否有 steamrefresh 名称，但 `_check_steam_cookies` 未利用。
- 分类：已证实（代码可判）。

### P1-3　探针无法证明"服务端无 Set-Cookie"：未读原始头，未记录 query 参数名
- 文件/行号：`diagnostics/steam_auth_flow_probe.py:40-42`（`response_cookie_inventory` 只读 `response.cookies`）、`:97-102`（`safe_target` 不打印 query 参数名）、`:30-32`。
- 触发条件：任何一次探针运行。
- 后果：无法区分"无 Set-Cookie 头"与"有头但被 CookieJar 拒收"；无法确认 jwt/refresh 的 Location 是否真的携带 `redir` 等参数（代码未删 query 已证实，但线上 Location 内容未证实）。
- 证据：日志只有 jar 层面结果；`response.raw.headers.get_all('Set-Cookie')` 未使用。
- 分类：已证实（诊断缺口）。

### P2-1　扁平 Cookie 字符串 + 名称前缀域重建，破坏多域同名语义
- 文件/行号：`auth.py:80-87`（`_cookie_header_from_browser` 扁平化，丢弃 domain/path/expires/Secure/HttpOnly/SameSite）、`auth.py:90-107`（按名去重，社区域优先 → 丢弃 store/login 域同名 `sessionid`、store 变体 `steamLoginSecure`）、`steam/session.py:12-19`（`parse_cookies` 同名后者覆盖）、`steam_auth.py:46-54`（域重建启发式）。
- 触发条件：任何 Playwright 捕获保存 / 手动 cookie 输入 / 验证装载。
- 后果：`login.steampowered.com` 请求只带 `steamrefresh*`；`store.steampowered.com` 请求（经 `_configure_steam_session` 的会话）只带 `steamrefresh*`。注意：购买链路（gift_engine、`create_market_session`）走 hostless dict，不受影响——**不要"顺手修复"成域作用域**，否则会破坏 checkout 的 `sessionid`。
- 证据：测试 `test_auth_relogin_state.py:101-130` 明确断言"`steamLoginSecure` 不发给 login.steampowered.com"——这恰好把缺陷固化为预期。
- 分类：已证实。

### P2-2　手动 302 对 307/308 也以 GET 重发（本路径无影响，仅注明）
- 文件/行号：`steam_auth.py:135-139,166-170`。
- 触发条件：本验证流全为 GET，307/308 语义差异（应保留方法/body）不触发。若未来复用到 POST 流需修正。
- 分类：已证实，无后果。

### P2-3　无刷新令牌时验证仍消耗 4 步请求（同 P1-2 的自然推论）
- 略，合并到 P1-2。

---

## 4. 假设审计表（H1–H7）

| 假设 | 支持证据 | 反对证据 | 仍缺什么 | 最小验证 |
|---|---|---|---|---|
| **H1** Steam 返回了 Set-Cookie 但被 Requests 拒收 | 无直接支持 | ① 社区 302 的 Set-Cookie 两份日志均正常入库（jar 未被拒收）；② `/jwt/refresh` 响应 `response.cookies` 与 `session.cookies` 同时为空，两个 jar 用同一 policy 对同一请求 URL 判定；③ 若真有 `steamLoginSecure` Set-Cookie（Domain 匹配 login 域），必然进入至少一个 jar | 原始头层面：多值 Set-Cookie 折叠、值含非法字符被静默丢弃（对 JWT/base64url 值概率极低） | 探针读 `response.raw.headers.get_all('Set-Cookie')`，只记名称与 Domain 属性并对比 jar 计数 |
| **H2** 扁平字符串破坏多域同名语义 | `auth.py:80-107` + `steam_auth.py:46-54` 代码路径明确：同名去重、域按名称前缀重建；`test_auth_relogin_state.py:101-130` 把"login 请求无 steamLoginSecure"固化为预期 | 它是循环的**放大因素**而非主因：即使保留所有域，`steamRefresh_steam` 本身缺失仍是循环根因 | 无 | 真实 CookieJar 断言：无 steamrefresh 时 login 请求 Cookie 头为空（见 §6 测试 1） |
| **H3** 手动 302 缺导航头（Referer/Sec-Fetch-*） | `steam_auth.py:103-107` 只设 UA/Accept/Accept-Language，无 Referer；requests 不自动生成导航头 | 两种代理链路、含真实浏览器捕获后的验证，行为完全一致；`/jwt/refresh` 的 302 无 Set-Cookie 是"令牌缺失"的直接结果，与请求头无关的概率高；302 的 GET 语义与浏览器一致，CookieJar 逐跳更新也与浏览器一致 | 无法在纯 requests 下排除 Steam 对缺 Referer 的刷新请求不签发 | Playwright 侧复现同一会话对比（并作 §5 诊断增强） |
| **H4** Community 与 Login 出口/规则不同 | 有项目代理时 `session.proxies` 同一 URL 但 Clash 按目标域分流，理论上两域出口可不同 | ① 两份日志两域 `Server` 头一致（`nginx` / `nginx, WattToolkit`）；② 两种链路（WattToolkit 系统级、Clash 系统代理）行为完全一致；③ `trust_env` 只影响"是否继承环境代理"，对两域无差异（`steam/session.py:34-51`） | 出口 IP 级证据（不建议主动测 IP） | 低优先级；可在探针中记录两域的 TLS 会话/服务器头一致性即可 |
| **H5** 保存时机过早（Store 已登录、Community SSO 未完成） | `auth.py:270-283`：commit + 立即捕获 + 无校验；15s 超时照存；`goto(my/)` 才触发舞蹈 | 若浏览器舞蹈能完成，`commit` 语义下刷新令牌应在捕获时已落 jar；新资料仍缺失 → 更可能是浏览器侧舞蹈本身失败（→H6/R2） | 浏览器 jar 在"登录后、捕获前"的实时内容 | 改造 worker 或独立脚本：轮询 `context.cookies()` 等待 `steamRefresh_steam`（≤20s），记录是否出现（§6 测试 3） |
| **H6** Steam 近期改变 SSO/JWT 行为 | 无仓库内证据；新资料登录仍循环与"浏览器侧舞蹈失败"兼容 | 纯推断 | 无法从仓库验证 Steam 侧行为；真实平台测试超出审查边界 | 用户下次手动操作时观察浏览器是否自行循环（不自动重试） |
| **H7** 近期代码回归 | diff 证实 8/11-12 改动：验证端点从 store JSON API（旧代码对循环 `except→return True` 掩盖）改为 community 优先+有界刷新+market 兜底；`_load_steam_auth_cookies` 域作用域替代 hostless `cookies.update`；捕获改 commit。这些让循环"显形"并放大（P1-1/P1-2/P2-1） | 8/10 多账号改动（accounts/config_loader/gift_engine/account_scope 未提交 diff）不改变 cookie 域语义——gift_engine 全程 hostless dict，与循环无直接关系；账号目录/账号路由正确（`auth.py:296-301`、`account_scope.py:115-133`） | — | 已通过 diff 完成函数级对比；无独立备份可对比，但 diff 即工作树 vs HEAD 的完整增量 |

---

## 5. 推荐修复顺序

### 第一步：诊断增强（改动最小、只读、可立即上线）
1. `diagnostics/steam_auth_flow_probe.py`：
   - 增加原始 Set-Cookie 检查：`response.raw.headers.get_all('Set-Cookie')` 只记录**名称 + Domain 属性 + 数量**（不记值），并对比 `response.cookies`/`session.cookies` 的计数 → 直接裁定 H1。
   - 在每次 `session.get(...)` 前记录**即将发出的 Cookie 名称清单**（特别是 login.steampowered.com 请求）→ 直接裁定"刷新请求零 cookie"。
   - `safe_target` 增加"query 参数名清单"（不记值）。
2. 规模：约 30 行；风险：无（纯只读日志）。

### 第二步：最小修复（三处，合计 <40 行）
1. **steampy 路径采集 login 域**（`steam_auth.py:438-440`）：合并时加入 `client._session.cookies.get_dict(domain='login.steampowered.com')`。`_load_steam_auth_cookies` 已把 `steamrefresh*` 路由到 `.steampowered.com`，无需改装载逻辑。
2. **Playwright 捕获加等待与校验**（`auth.py:270-283`）：
   - `goto("https://steamcommunity.com/my/profile", wait_until="commit")` 后，轮询 `context.cookies()` 至 `steamRefresh_steam` 出现（≤20s）再捕获；
   - 超时/缺失 → 设 `_relogin_error`（"未检测到 Steam 刷新令牌 steamRefresh_steam，社区 SSO 可能未完成或账号需重新验证"），**拒绝保存残缺凭证**并关闭浏览器。
3. **验证前置检查**（`steam_auth.py:135` 前）：jar 中无任何 `steamrefresh*` 时直接返回 `("unavailable", "缺少 Steam 刷新令牌 steamRefresh_steam…")`，不再消耗 4 步请求。
4. 规模：各 10–20 行；风险：低；**不回滚**已验证正确的有界循环与 429 保护。

### 第三步：回归测试（见 §6）
- 规模：2 个新测试 + 1 个修改；风险：无。

### 回滚建议
- **无合法代码回滚点**：旧验证（store JSON API + `except→return True`）会把循环误报为有效，是"掩盖故障"——禁止回滚到该行为。
- 若第二步 2 实施后仍循环（浏览器侧舞蹈确实失败，R2 证实），正确的"回滚"是用户侧动作：在真实浏览器重新完成登录并确认社区主页显示已登录后再捕获；代码层面仅保留"拒绝保存残缺凭证"。

---

## 6. 建议新增测试（及现有测试为何覆盖不到）

现有测试的盲区（`tests/test_auth_relogin_state.py`）：
1. 绝大多数流程测试用 `FakeSession`，其 `cookies` 是**普通 dict**（如 `test_auth_relogin_state.py:263-271,319-327,355-361,431-436,480-486,524-530`）→ **Requests CookieJar 的域匹配/拒收语义完全不执行**，"哪个 cookie 会发给 login.steampowered.com"这类问题永远测不出来。
2. 唯一用真实 `requests.Session` 的测试（`test_auth_relogin_state.py:101-130`）只断言"`steamLoginSecure` 不发给 login 域"——**把缺陷固化为预期**，未断言"无 steamrefresh 时 login 请求 Cookie 头为空"。
3. FakeSession 的 `/jwt/refresh` 分支总是返回 302→profile（`test_auth_relogin_state.py:417-465`），**从未模拟真实观测到的"302 且无 Set-Cookie → 回 my/profile"**；`FakeResponse.headers` 是 dict，丢失多 Set-Cookie 与原始头语义。
4. 无任何测试覆盖 Playwright 捕获时机/等待逻辑（`_relogin_worker` 无测试）。

新增建议：
1. **真实 CookieJar 域路由测试**：用 `requests.Session` + `_load_steam_auth_cookies` 装载无 `steamRefresh_*` 的凭证，断言 `prepare_request` 到 `login.steampowered.com/jwt/refresh` 的 `Cookie` 头为空；装载含 `steamRefresh_steam` 后断言其存在且 `steamLoginSecure` 仍不发送（修正现有 101-130 测试的断言方向）。
2. **"302 无 Set-Cookie 循环"行为测试**：模拟 jwt/refresh → 302（无 Set-Cookie）→ my/profile → 302 → jwt/refresh，断言返回 `unavailable` 且市场兜底被调用（真实验证语义，非 dict-cookie fake）。
3. **捕获等待逻辑测试**（fake `context.cookies()` 分阶段返回：先无 `steamRefresh_steam`、后有）：断言等待循环、超时拒绝保存、错误信息。
4. 现有 `FakeSession` 测试补断言：`trust_env`/`proxies` 之外，验证"无 steamrefresh 时 jwt 分支在第一次刷新请求前即短路"（配合第二步修复 3）。

---

## 7. 禁止方案

1. **把 302 当成功**：任何"忽略 302、继续交易"的做法都会在失效会话上执行售卖/赠送。
2. **无限跟随重定向**：循环会放大账号风控与请求风暴。
3. **跳过身份验证直接执行交易**：gift/checkout 必须以可验证的登录态为前提。
4. **回滚到旧 store JSON API 验证（`except → return True`）**：对循环误报"有效"，掩盖故障。
5. **自动重试登录/刷新令牌、多账号轮换试探**：涉及真实账号操作与风控，超出本审查边界；一切登录动作由用户手动在浏览器完成。
6. **把 gift_engine / `create_market_session` 的 hostless cookie 语义"修复"为域作用域**：购买链路（store cart JWT、checkout `sessionid`）依赖 hostless 通配发送，改了反而破坏正常交易。

---

## 8. 给 Codex 的实施清单（未执行，仅建议）

| 文件 | 函数 | 改动 |
|---|---|---|
| `diagnostics/steam_auth_flow_probe.py` | `response_cookie_inventory` / `main` | 记录 `response.raw.headers.get_all('Set-Cookie')` 的名称+Domain+数量；记录每步请求发出 Cookie 名称清单；`safe_target` 增加 query 参数名（不记值） |
| `app/services/steam_auth.py` | `_do_steampy_login` | `merged` 增加 `client._session.cookies.get_dict(domain='login.steampowered.com')`（`438-440`） |
| `app/services/steam_auth.py` | `_check_steam_cookies` | 进入 jwt/refresh 分支前检查 jar 是否含 `steamrefresh*`，无则直接返回 `("unavailable", "缺少 Steam 刷新令牌 steamRefresh_steam…")`（`135` 前） |
| `app/routes/auth.py` | `_relogin_worker` | `goto` 目标改 `/my/profile`；捕获前轮询等待 `steamRefresh_steam`（≤20s）；缺失则 `_relogin_error` 并拒绝保存（`270-296`） |
| `tests/test_auth_relogin_state.py` | 新增 3 个测试（§6 1/2/3），修正 `101-130` 断言方向 | 见 §6 |
| 不动 | `steam/session.py`、`gift_engine.py`、`account_region.py`、`app/accounts.py`、`app/account_scope.py` | hostless 语义是购买链路正常工作的依赖，保持原状 |

**下一步验证闭环**：实施第一步诊断 → 重跑探针 → 若原始头证实"login 请求零 cookie"，实施第二步 → 用 Playwright 重登并确认浏览器 jar 中 `steamRefresh_steam` 是否存在（该结果同时裁定 R1 与 R2）→ 全部通过后再考虑任何更深的改动。