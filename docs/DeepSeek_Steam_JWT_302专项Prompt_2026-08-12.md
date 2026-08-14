# DeepSeek 专项审查 Prompt：Steam JWT 302 循环

你现在位于项目根目录 `D:\Vibe Coding\steam`。请进行一次**只读、证据驱动**的 Steam 登录态专项审查，并把最终报告写入：

`docs/DeepSeek_Steam_JWT_302专项审查_2026-08-12.md`

## 权限边界

1. 只允许读取项目代码、测试、Git差异、日志和文档。
2. 只允许在`docs`目录新建或修改本次报告。
3. 禁止修改任何Python、JavaScript、配置、数据库、Cookie、浏览器资料或启动脚本。
4. 禁止发起任何Steam、BUFF、GitHub或其他外部网络请求。
5. 禁止登录、验证账号、刷新Cookie、启动购买/出售任务或运行可能访问真实平台的测试。
6. 不得读取、打印、复制或推断Cookie/JWT/密码/令牌的具体值。日志中已有脱敏信息足够分析。

## 当前事实

- AetherSwap此前可以实际运行并完成购买；本次问题是账号验证持续进入JWT刷新循环。
- 最新诊断日志：`log/steam_auth_flow_probe_20260812_002901.txt`。
- 前一份WattToolkit链路日志：`log/steam_auth_flow_probe_20260811_234707.txt`。
- 最新日志中`trust_env=True`且Server不再显示WattToolkit，说明已切换到Clash链路。
- 两种链路都得到同一序列：
  1. `steamcommunity.com/my/profile` -> HTTP 302 -> `login.steampowered.com/jwt/refresh`
  2. `/jwt/refresh` -> HTTP 302 -> `steamcommunity.com/my/profile`
  3. 重复上述循环
  4. `steamcommunity.com/market/`同样HTTP 302到`/jwt/refresh`
- 诊断中`response.cookies`没有看到JWT响应写入新Cookie；但当前脚本尚未区分“服务端没有原始Set-Cookie头”和“有Set-Cookie但被Requests CookieJar拒收”。
- 当前保存的Cookie名称只有：`browserid`、`timezoneOffset`、`sessionid`、`steamCountry`、`steamDidLoginRefresh`、`steamLoginSecure`；没有独立`steamRefresh_*`。
- 新建Playwright资料并重新登录后仍是相同结果，因此不要仅用“Cookie过期”作为结论。
- 当前代码已做过以下保护：禁止无限自动跳转、有界JWT刷新、市场页兜底、按域装载认证Cookie、验证错误延长显示。不要默认这些修改一定正确，也不要因为测试通过就忽略真实HTTP语义。

## 必须审查的文件

- `app/services/steam_auth.py`
- `app/routes/auth.py`
- `steam/session.py`
- `app/config_loader.py`
- `app/accounts.py`
- `app/services/account_region.py`
- `app/gift_engine.py`
- `diagnostics/steam_auth_flow_probe.py`
- `tests/test_auth_relogin_state.py`
- `tests/test_steam_session_routing.py`
- `tests/test_account_region_sync.py`
- 上述两份`steam_auth_flow_probe`日志
- `git diff -- app/services/steam_auth.py app/routes/auth.py steam/session.py diagnostics/steam_auth_flow_probe.py tests/test_auth_relogin_state.py`

## 核心问题

请逐项追踪并用文件/行号作证：

1. Playwright的`context.cookies()`原始域、路径、Secure、HttpOnly、SameSite和重复同名Cookie信息，在转换为扁平`Cookie`字符串时丢失了什么？
2. `_select_steam_browser_cookies`、`_cookie_header_from_browser`、`parse_cookies`和`_load_steam_auth_cookies`组合后，是否可能把Cookie装到错误域、错误路径，或丢失同名但不同域的合法Cookie？
3. `requests`手动跟随302时，与浏览器/Requests自动重定向相比，是否缺失Referer、Origin、Host、Sec-Fetch-*、请求方法处理或CookieJar更新语义？哪些差异真正可能影响Steam JWT刷新，哪些只是猜测？
4. `response.cookies`为空是否足以证明服务器没有Set-Cookie？如何在不记录值的前提下检查原始Set-Cookie头及CookieJar拒收原因？
5. JWT刷新URL中的查询参数是否被完整保留并实际发送？诊断脚本只是不打印query，还是代码误删了query？
6. Community与Login请求是否可能走不同代理、DNS、IP出口或TLS会话？`trust_env`、项目代理池与本地反向代理逻辑分别如何影响两个域名？
7. 新Playwright资料登录后，保存流程是否可能抓取过早、抓错账号目录、抓到Store已登录但Community尚未完成SSO的Cookie？
8. 当前`/my/profile`验证是否选择了不合适的端点？若改用Market、inventory、mylistings或浏览器验证，会不会只是掩盖后续真实市场请求同样302的问题？
9. 结合“AetherSwap此前可以真实运行”的事实，哪些近期改动可能改变Cookie域语义、会话路由或验证流程？请区分仓库旧逻辑、8月10日多账号改动、8月11日至12日JWT排查改动。
10. 给出最小修复、临时诊断增强和必要回滚建议；不得用“强制把302当成功”“无限跟随”“跳过身份验证”作为方案。

## 重点候选假设

请证实或证伪，而不是直接采纳：

- H1：Steam确实返回Set-Cookie，但Requests因Domain/Path/Secure规则拒收，当前诊断误报为“没有写入”。
- H2：扁平Cookie字符串破坏了浏览器的多域同名Cookie语义，手工恢复域名仍不等价于原CookieJar。
- H3：手动302缺少浏览器导航头或重定向上下文，导致`/jwt/refresh`只回跳而不签发新Community Cookie。
- H4：Community与Login虽然都经Clash，但实际出口或规则仍不同，JWT中的短期授权与来源不一致。
- H5：项目浏览器保存时机过早，Store登录成功但Community SSO未完成。
- H6：Steam近期改变了SSO/JWT行为，纯Requests登录态刷新已不可靠，需要Playwright完成刷新后再把完整域CookieJar交给后端。
- H7：近期代码修改引入回归；应从2026-08-10备份做精确函数级对比，而不是凭时间印象判断。

## 报告格式

1. **结论摘要**：最可能根因前三名，分别给出置信度。
2. **已证实事实**：只写有代码或日志证据的内容。
3. **发现项**：按P0/P1/P2排序，每项必须包含文件、行号、触发条件、后果和证据。
4. **假设审计表**：H1-H7逐项写“支持/反对证据、仍缺什么证据、如何最小验证”。
5. **推荐修复顺序**：先诊断增强，再最小修复，再回归测试；标明修改规模和风险。
6. **建议新增测试**：必须说明现有FakeSession测试为何可能无法覆盖真实CookieJar/302语义。
7. **禁止方案**：列出会掩盖故障或增加账号风险的做法。
8. **给Codex的实施清单**：列出建议改动文件和函数，但不要自行修改。

请直接开始审查，不要向用户提问。报告必须明确区分“已证实”“高概率推断”“待验证”。
