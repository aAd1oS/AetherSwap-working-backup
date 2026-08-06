
# AetherSwap 代码安全审查报告（复核修订版）

- 审查日期：2026-08-06
- 审查对象：D:\Vibe Coding\steam 当前工作树（含未提交修改与未跟踪文件，非仅 git HEAD）
- 初次审查方式：Claude 静态只读审查，未运行程序或真实网络请求。
- 复核方式：Codex 对照当前工作树、实际启动脚本和非敏感运行配置逐项核查；2026-08-06 完成小范围安全修复并运行完整测试。
- 审查范围：购买链（pipeline / pipeline_steps / buff）、接收链（receive_flow / inventory_cs2 / steam）、出售链（sell_pipeline / steam_listings / steam_delist / sync_sold）、DB/状态机（database / order_state / state / workers）、API/前端（api / routes / web）。

---

## 1. 总体结论

Claude 报告发现了多项有价值的风险线索，但原报告的严重度明显偏高，且混合了当前配置、未启用功能和服务器部署场景。复核后，**不认定当前桌面运行方式存在已经证实的 P0 级确定性资金损失或远程账号接管路径**。

复核后的关键结论：

1. **原 P0-1 不成立为已确认问题**：`sellitem.price` 的准确口径缺少官方接口文档与真实上架样例。当前代码很可能是在把买家显示总价反算为卖家到手价，直接按原报告改成 `yuan_to_cents` 可能反而提高挂单价。冷却结束后必须用低价值物品核对“买家支付/卖家收到”，验证前不修改手续费算法。
2. **原 P0-2 是潜在服务器部署缺陷，不是当前桌面风险**：运行时默认 server 模式会绑定 `0.0.0.0`，但项目提供的两个 Windows 启动脚本均明确绑定 `127.0.0.1`。如果将来真正部署到局域网或公网，必须先加认证或保持回环监听。
3. **普通扫码支付的未知锁单重试风险成立，但当前余额支付模式不走三次重试**：余额路径已经将结果未知记录为待核对并停止任务。扩大使用手动支付宝/微信回退前仍应修复普通路径。
4. **自动 Steam 确认逻辑确实会确认全部待确认项**：当前配置虽然开启，但因 `device_id` 未配置而实际跳过。不能把“补上 device_id”当作修复；必须先改成只确认本轮指定上架资产。
5. **当前最适合立即处理的是小范围、低回归风险的问题**：PushPlus HTTPS、无效卖单价格保护、订单条件更新和 SteamDT 成交量本地复核。这些已于 2026-08-06 修复，完整测试 242 项通过。

### 当前配置与触发条件

| 项目 | 当前状态 | 结论 |
|---|---|---|
| BUFF 支付 | `balance` | 普通扫码三次锁单和批量微信降级当前不触发 |
| Steam 自动确认 | 开启，但缺少 `device_id` | 当前实际跳过；不要直接补 `device_id` |
| 邮箱付款确认 | 未配置 | 伪造邮件、跨订单邮件串扰当前不触发 |
| 出售价偏移 | `0` | 负偏移挂到地板价当前不触发 |
| 代理池 | 关闭 | 本次修复未改任何 Steam/BUFF 代理或网络路由 |
| 启动监听 | `127.0.0.1` | 当前不是局域网裸开放 |

### 2026-08-06 已完成的小修

- PushPlus 请求由 HTTP 改为 HTTPS。
- BUFF 卖单价格缺失、无效或非正数时直接跳过，不再进入除法和锁单流程。
- 订单状态更新支持数据库原子条件判断；余额自动扣款中的订单禁止手动取消；`cancelled/refunded/failed` 不再被后台库存对账复活。
- SteamDT 候选在本地再次执行 `min_volume` 硬门槛，缺失或异常成交量按 0 拒绝。
- 新增回归测试；完整测试结果：`242 passed`。
- 未修改 Steam/BUFF 网络路由、代理、登录、价格换算或自动上架代码，也未发起真实付款和上架请求。

### 冷却结束后的出售复查计划

- **首件实际冷却结束日期以用户界面显示为准：2026-08-11。**
- 数据库中只有 2026-08-06 入库的 `Sawed-Off | Analog Input (Minimal Wear)` 写入了完整 `tradable_at`，因此程序查询得到 2026-08-13 19:00；更早入库的商品 `tradable_at` 均为空，不能据此判断“首件”日期。
- 计划在 **2026-08-11 19:30** 复查第一批真实可交易样例。届时先检查状态、价格口径、自动确认和上架结果核对，说明问题与预计修复规模；未经确认不自动改代码、不自动上架。

---

## 2. 逐项复核状态

| 原编号 | 复核结论 | 当前优先级 |
|---|---|---|
| P0-1 | 结论有争议，原修复方向可能相反；等待真实上架验证 | 验证前禁止修改 |
| P0-2 | 潜在服务器部署风险，当前启动脚本不触发 | 以后部署前处理 |
| P1-1 | 普通手动支付路径成立；余额路径已安全停机核对 | 使用手动回退前处理 |
| P1-2 | 停止语义描述过重；锁单后完成当前原子交易可能更安全 | 暂不改主流程 |
| P1-3 | 全局 monkey-patch 成立，但属于高回归网络改造 | 稳定运行期间暂缓 |
| P1-4 | 成立；配置 `device_id` 后才会真正触发 | 自动出售前必须修 |
| P1-5 / N2-3 | 订单状态竞争成立 | 2026-08-06 已完成小修 |
| P1-6 | 整表替换并发风险成立 | 数据层专项处理 |
| P1-7 / N2-1 | 设计缺陷成立，但邮箱当前未配置 | 低优先级 |
| P1-8 | 负偏移可触发地板价，当前偏移为 0 | 自动出售前加校验 |
| P1-9 | `gift.js` 未转义 `innerHTML` 成立 | 使用赠礼前处理 |
| P1-10 | 本地明文与 API 回显成立；当前回环监听且敏感文件被 git 忽略 | 安全专项处理 |
| P1-11 | 匹配不足成立，但来源是 BUFF 任务而非任意 Steam 报价，原后果偏重 | 接收链专项处理 |
| P2-3 | 无效价格可除零成立 | 2026-08-06 已修复 |
| P2-6 | 上架时追加 Sale 会污染记录；仪表盘统计并不直接累计 Sale 表 | 自动出售前处理 |
| N1-1 | 上架响应丢失后缺少在售核对成立 | 自动出售前处理 |
| N3-4 | PushPlus 使用 HTTP 成立 | 2026-08-06 已修复 |

---

## 3. 原始审查明细（保留作风险线索，不代表复核后的最终定级）

> 本节保留 Claude 的原始静态分析，便于后续逐项实现和测试。若与第1、2节冲突，以复核结论为准。

### 原报告 P0（复核后均不作为当前已确认 P0）

### P0-1（待真实样例验证）上架价格可能存在手续费口径争议

> **复核修订**：原报告把该问题写成“确定性损失”并建议直接改用 `yuan_to_cents`，证据不足，修复方向也可能相反。冷却结束后的低价值真实上架实验是修改前置条件。

- **严重程度**：P0（确定性资金损失，每笔上架发生）
- **置信度**：高（代码内部语义自相矛盾可证明；Steam sellitem price=买家实付价为公开语义）
- **文件与行号**：`app/sell_pipeline.py:355` → `utils/money.py:126-135` → `utils/money.py:58-78` → `steam/market.py:22-28`
- **函数/调用链**：`_build_listing_plan` → `compute_smart_list_price`（`steam/market_orders.py:808-854`，产出订单簿"买家实付价"）→ `list_price_display_to_cents(display_price, account_currency)` → `get_item_price_from_total(total_cents, wallet_info)` → `list_item(price=price_cents)` POST /market/sellitem
- **代码证据**：
  ```python
  # utils/money.py:126-135 —— 把 display_price 当作"含费总额"
  def list_price_display_to_cents(display_amount, account_currency="CNY"):
      total_cents = max(1, int(round(display_amount * 100)))
      ...
      return get_item_price_from_total(total_cents, wallet_info)
  # utils/money.py:65-78 —— 反推 base 使 base + 5% + 10% == total，即 base ≈ total/1.15
  n_initial_guess = math.floor(n_total / (1.0 + ppct + spct))
  ```
  而 `compute_smart_list_price` 的输入是 Steam 订单簿卖单图（买家实付价口径），策略 3 的利润护栏（`sell_pipeline.py:290-291`）也按"买家实付价"使用该值。
- **触发条件**：每一次自动上架（策略 1-4 全路径）。
- **实际影响**：POST 出去的 price ≈ 意图价 × 0.87，买家支付 0.87×意图价，卖家净得 ≈ 0.74×意图价。每件出售少卖约 13%。策略 3 的盈利比例护栏同口径失效。
- **现有保护为何没拦住**：币种/地区护栏只校验币种不校验手续费数学；`yuan_to_cents`（正确实现）存在但未被使用。
- **最小修复**：`price_cents = int(round(display_price * 100))`（即 `yuan_to_cents`），删除 `get_item_price_from_total` 反算；若产品意图为"净得=display_price"则改用 ×1/0.85；加注释钉住语义。
- **应补充测试**：钉住 sellitem price 语义的换算单测（display_price 300 元 → POST 30000 分而非 26087 分）；真实上架一次核对。

### P0-2（潜在部署风险）服务器模式默认监听 0.0.0.0 且 API 无认证

> **复核修订**：当前提供的 Windows 桌面和服务器启动脚本均显式绑定 `127.0.0.1`，所以当前运行方式不构成原报告描述的局域网暴露；默认 server 分支仍需在未来部署前修复。

- **严重程度**：P0（账号完全接管）
- **置信度**：高
- **文件与行号**：`app/runtime_env.py:127-132`；`app/api.py:48-77`（无任何认证中间件）；`app/routes/__init__.py:17-29`
- **函数/调用链**：headless/无桌面环境 → `get_bind_host` 返回 `"0.0.0.0"` → 局域网任意设备可调用 `POST /api/gift/send`（真实扣钱包）、`POST /api/pipeline/start`（自动购买扣款）、`GET /api/export_full`（全部 Cookie/密码/shared_secret/PushPlus token/邮箱授权码）、`POST /api/data/init`（清库）、`POST /api/system/shutdown`（杀进程）。
- **代码证据**：
  ```python
  # runtime_env.py:132
  return "0.0.0.0" if profile.mode == "server" else "127.0.0.1"
  # api.py:64-75 —— 无身份校验
  @app.post("/api/system/shutdown")
  def shutdown_system(...):
      ...; os.kill(os.getpid(), signal.SIGINT)
  ```
- **触发条件**：`AETHERSWAP_MODE=server` / headless / 无图形显示的 Linux 服务器部署。
- **实际影响**：账号完全接管（钱包可转走、赠送、买卖）、数据全毁、进程可控。
- **现有保护**：桌面模式默认 127.0.0.1 是唯一防线。
- **最小修复**：服务器模式启动时生成随机 API token（前端 fetchJson 统一注入 header）；或强制回环+SSH 隧道；至少校验 Host 头。
- **应补充测试**：`get_bind_host` 各模式枚举测试；server 模式无 token 的请求被拒的集成测试。

---

### P1

### P1-1 锁单"结果未知"被当"明确失败"，同一卖单重试 3 次 → 重复下单 / 悬挂订单

- **严重程度**：P1（可能重复付款或产生无人认领的待付款订单）
- **置信度**：高
- **文件与行号**：`buff/buyer.py:617-618`；`app/services/buff_client.py:73-81`；`app/pipeline_steps.py:1535-1547, 1608-1621`
- **函数/调用链**：`pipeline.py:371 lock_and_confirm_payment` → `pipeline_steps.py:1610 _try_single_buy()`（循环 3 次）→ `first_order_at_price(orders, lowest_price)`（每次返回同一卖单）→ `lock_and_get_pay_url` → POST /buy → 网络超时 → `buyer.py:617 except Exception: return {"success": False}` → 上层视为失败，5s 后重试同一卖单
- **代码证据**：
  ```python
  # buyer.py:617-618 —— 超时与明确拒绝走同一出口
  except Exception as e:
      return {"success": False, "code": "FAIL", "msg": str(e)}
  # pipeline_steps.py:1608-1621 —— 无条件重试 3 次同一卖单
  for attempt in range(3):
      paid = _try_single_buy()
  ```
  项目内有正确先例：余额路径 `lock_order_once` 抛 `BuffOrderOutcomeUnknown`（buyer.py:344-345,352），下游记录本地核对号并 `PAYMENT_REVIEW_REQUIRED` 停任务（pipeline_steps.py:1149-1162）。普通支付路径没有沿用。`buff_client.py:73` 的 `@with_retry` 因异常被吞而永不触发。
- **触发条件**：锁单请求已到达 BUFF 创建订单但响应丢失（10s 超时/断网），任何自动化购买脚本周期性出现。
- **实际影响**：BUFF 侧产生重复待付款订单（双倍付款风险）或悬挂订单（需人工取消）；本地无记录、无阻断。
- **最小修复**：`lock_and_get_pay_url` 网络异常改抛 `BuffOrderOutcomeUnknown`，调用侧记录未知订单并 `PAYMENT_REVIEW_REQUIRED`；重试仅对明确业务错误码生效。
- **应补充测试**：模拟 `_make_request` 抛 RequestException → 断言走 PAYMENT_REVIEW_REQUIRED 而非重试；断言同一 sell_order 只锁一次。

### P1-2 停止请求无法中止余额自动付款（真实扣款发生在停止之后）

- **严重程度**：P1（违背停止语义，用户明确停止后仍扣款）
- **置信度**：高
- **文件与行号**：`app/pipeline_steps.py:1084-1264`（`_execute_balance_purchase`）；`app/pipeline.py:382`
- **函数/调用链**：用户点停止 → `request_stop()` → `_execute_balance_purchase` 从 `lock_balance_order_once`（1142）到 `pay_bill_order_once`（1199，真实扣款）之间**无任何 `is_stop_requested()` 检查**；pipeline.py:382 的停止检查在整笔返回之后。
- **代码证据**：
  ```python
  # pipeline_steps.py:1196-1199 —— 无停止检查直接发起真实扣款
  page_pay = buff_client.pay_bill_order_once(order_id)
  ```
  对照：手动支付路径 `_do_wait_payment_and_append` 在追加前有检查（pipeline_steps.py:997）。
- **触发条件**：balance/balance_first 模式，用户点停止时正处于预览→锁单→扣款的数秒窗口。
- **实际影响**：明确停止后仍真实扣款（虽会记账，不重复扣，但违背停止语义）。
- **最小修复**：付款前（1191 行附近）与锁单前各加 `if is_stop_requested(): 记 payment_unconfirmed; return PAYMENT_REVIEW_REQUIRED`。
- **应补充测试**：模拟停止发生在锁单后、付款前 → 断言不调用 pay_bill_order_once。

### P1-3 steam_auth 全局 monkey-patch requests.Session.request：并发登录互相踩踏，全进程 TLS 校验/代理可能被永久改写

- **严重程度**：P1（全局 TLS 校验关闭 + BUFF 流量可能被路由到 Steam 代理池）
- **置信度**：高
- **文件与行号**：`app/services/steam_auth.py:249-254, 293-294`；入口 `try_steam_auto_relogin`（有锁）与 `verify_steam_auto_login`（**无锁**，steam_auth.py:475）
- **代码证据**：
  ```python
  # steam_auth.py:249-254 —— 进程级副作用
  _old_request = _req.Session.request
  def _bypass_ssl(self, method, url, **kwargs):
      kwargs['verify'] = False; kwargs['proxies'] = _steam_request_proxies()
      return _old_request(self, method, url, **kwargs)
  _req.Session.request = _bypass_ssl
  try: client.login()
  finally: _req.Session.request = _old_request   # 294
  ```
- **触发条件**：两个登录入口并发（web 触发验证 + 后台 keepalive worker），或登录窗口（10-60s+）与任意其他线程的 requests 请求并发。若线程 B 先恢复补丁，`_bypass_ssl` 永久残留。
- **实际影响**：进程内所有 requests 请求（含 BUFF 直连请求）永久 `verify=False` 且代理被替换为 Steam 代理池；2FA 码互相作废导致登录失败与限流。
- **最小修复**：删除 monkey-patch，改为登录前 `client._session.verify = False; client._session.proxies.update(...)`；两入口共用同一把锁。
- **应补充测试**：并发调用两个登录入口 → 断言 `requests.Session.request` 始终为原函数。

### P1-4 auto_confirm_once 无差别确认所有 Steam Guard 待确认项（含交易报价）

- **严重程度**：P1（用户未意图的交易被静默放行）
- **置信度**：高
- **文件与行号**：`app/steam_confirm.py:74-106`；调用点 `app/sell_pipeline.py:480-485`
- **代码证据**：
  ```python
  # steam_confirm.py:91-95 —— 不检查 conf 的 type 字段
  for c in conf_list:
      multipart.append(("cid[]", (None, str(c.get("id")))))
      multipart.append(("ck[]", (None, str(c.get("nonce")))))
  ```
  Steam 确认项 `type`：2=交易报价，3=市场上架等，代码忽略。
- **触发条件**：卖出后自动确认时，账号上恰有其他待确认动作（向好友/小号的交易报价、手动上架）。
- **实际影响**：未意图的交易被放行，物品可被无感知移走。
- **最小修复**：按 type 过滤（仅市场确认类），非上架类型跳过并告警。
- **应补充测试**：构造含 type=2 与 type=3 的 conf 列表 → 断言仅确认 type=3。

### P1-5（已完成第一阶段修复）订单取消与流水线付款确认竞争

> **修复记录（2026-08-06）**：数据库状态更新新增 `expected_statuses` 原子条件；余额自动扣款中的订单禁止取消；用户已确认付款的订单不能直接取消；终止状态不会被后台库存对账复活。剩余极端并发场景以后在完整订单状态机改造中继续处理。

- **严重程度**：P1（台账双记账/卡死，需手工清理）
- **置信度**：高
- **文件与行号**：`app/routes/transactions.py:223-237`（cancel）；`app/database.py:324-338`（`db_update_purchase_order` 盲目覆盖）；`app/pipeline_steps.py:877-881, 1015-1021`
- **函数/调用链**：流水线等待 `wait_payment_confirm()` → 用户取消该订单（置 cancelled）→ 邮箱线程或用户随后 `confirm_payment(True)` → `db_update_purchase_order(order_id, {"status": "user_confirmed"})` 无条件覆盖 cancelled → append_purchase → awaiting_ship。
- **代码证据**：
  ```python
  # database.py:330-335 —— 不看当前状态直接覆盖
  for key in ("status", ...):
      if key in data: setattr(row, key, data[key])
  ```
- **触发条件**：取消后用户又点"确认已付款"，或邮箱自动确认在取消后到达（`_email_waiter` 线程不受取消影响）。
- **实际影响**：已取消订单被推进到 awaiting_ship 并生成 Purchase；后续 replace-paid 因旧单不在阻断状态而抛错（database.py:371-374），用户卡死。
- **最小修复**：`db_update_purchase_order` 增加期望状态参数（CAS：仅当当前状态 ∈ {awaiting_payment, user_confirmed} 才更新）；cancel 端点通知流水线使 `wait_payment_confirm` 返回 False。
- **应补充测试**：取消后 confirm_payment(True) → 断言订单保持 cancelled、无 Purchase 生成。

### P1-6 整表替换接口（repair/sync_sold）与 worker 并发：丢数据 + rowid 复用写错行

- **严重程度**：P1（数据丢失 + 把 A 的字段写到 B 上）
- **置信度**：高
- **文件与行号**：`app/routes/transactions.py:533-553`；`app/repair_error_records.py:341-342, 390-397`；`app/database.py:489-498`（`db_replace_transactions`）；`app/database.py:25-27`（Purchase.id 无 AUTOINCREMENT）；`app/sync_sold.py:80-82`
- **函数/调用链**：repair 先快照 purchases → 数分钟网络拉取 → `replace_transactions`（DELETE 全部 + INSERT 快照）。期间 worker 持续写库被整表删除；SQLite 无 AUTOINCREMENT 时 DELETE 后 INSERT 复用 rowid，receive_worker 持有的旧 `_db_id` 经 `db_update_purchase_by_id` **静默更新到不相关的全新行**。
- **代码证据**：
  ```python
  # database.py:492-497
  session.exec(sql_delete(Purchase)); session.exec(sql_delete(Sale))
  for p in purchases: session.add(_purchase_from_dict(p))
  ```
- **触发条件**：流水线运行中或 worker 活跃时触发"紧急修复"或"同步售出"（端点无任何运行状态检查）。
- **最小修复**：端点入口加 `get_status().get("status") == "running"` 拒绝；`db_replace_transactions` 改为按主键 upsert 保留 id。
- **应补充测试**：worker 写入与 replace 并发 → 断言新写入不丢失、db_id 映射稳定。

### P1-7 邮箱付款确认可被伪造邮件触发

- **严重程度**：P1（未付款被记为已付款，资金账目污染）
- **置信度**：高
- **文件与行号**：`app/notify.py:89-179`；调用点 `app/pipeline_steps.py:866-872`
- **代码证据**：
  ```python
  # notify.py:144 —— target_sender 为空时任何发件人通过
  is_sender_ok = target_sender.lower() in full_sender.lower() if target_sender else True
  # notify.py:145-146 —— 子串匹配，可被 @example.com.evil.com 绕过
  allowed_sender.lower() in sender_addr.lower()
  # notify.py:152-158 —— 主题子串匹配，默认主题可猜
  ```
- **触发条件**：配置了邮箱等待，收件箱出现一封主题含"已确认成功付款"的邮件（垃圾/伪造/误转发）。
- **实际影响**：未付款被记为已付款：订单 user_confirmed、购买流水入账、预算被占用、receive 永远等不到货。
- **最小修复**：强制配置 `allowed_sender` 且整地址精确比较（`email.utils.parseaddr`）；主题精确/正则匹配；建议邮件内容带一次性 nonce。
- **应补充测试**：伪造发件人/主题子串 → 断言不判定成功。

### P1-8 上架价无下限校验：sell_price_offset 无界，可挂出 0.03 地板价

- **严重程度**：P1（数百元物品可能以 0.03 挂出成交）
- **置信度**：中高（需手误/恶意策略或卖单墙被砸穿）
- **文件与行号**：`app/strategy_engine.py:308-310`（schema 无 min/max）；`app/sell_pipeline.py:251`（唯一校验 `list_price <= 0`）；`steam/market_orders.py:853`（`max(min_floor_price, final_price + offset)`）
- **触发条件**：`sell_price_offset` 设为大的负数（策略 JSON 导入接受）；或卖单墙被 4+ 个低价订单砸穿（market_orders.py:821 只跳过 ≤3 件的最低档）。策略 1/2 无利润比例护栏。
- **实际影响**：任意价值物品以地板价挂出并瞬间成交。
- **最小修复**：schema 加 min/max；上架前校验 `list_price >= max(墙价×0.5, buy_price)` 否则跳过告警。
- **应补充测试**：offset=-100 的配置 → 断言不生成 to_list；墙被砸穿模拟。

### P1-9 gift.js 两处未转义 innerHTML：Steam 可控数据 → 存储型 XSS，同源窃取全部凭据

- **严重程度**：P1（同源可调所有资金接口）
- **置信度**：中（sink 未转义确定；Steam 昵称/商品名是否允许 < / " 待确认）
- **文件与行号**：`web/js/gift.js:109-125`（好友卡片）、`web/js/gift.js:216-226`（版本卡片）
- **代码证据**：
  ```javascript
  card.innerHTML = `...${friend.name}...`;   // friend.name 未 escapeHtml
  ```
  数据源：`gift_engine.py:60-64` BeautifulSoup `get_text()`（实体解码还原原始字符）。
- **实际影响**：注入脚本与工具同源，可 `fetch('/api/export_full')` 盗取全部凭据、`/api/gift/send` 花掉钱包。
- **最小修复**：所有插值过 `escapeHtml`（utils.js:135-139 已有工具）或改用 textContent/createElement。
- **应补充测试**：构造含 `<img onerror=...>` 的 name → 断言渲染后无元素注入（DOM 断言）。

### P1-10 凭据明文 API 返回 + 明文落盘

- **严重程度**：P1（与 P0-2/P1-9 叠加即完整账号接管）
- **置信度**：高
- **文件与行号**：`app/routes/config.py:154-190`（/api/export_full 返回全部 Cookie 与密码）；`app/routes/accounts.py:31-35`（/api/accounts 返回含 password 的完整对象）；`app/accounts.py:39-46`（明文存储）；`app/routes/config.py:24-26`（/api/config 返回 shared_secret/email_pass/webshare key）
- **最小修复**：/api/accounts 响应剥离 password；导出类接口加口令或本机物理交互确认。
- **应补充测试**：/api/accounts 响应断言不含 password 字段。

### P1-11 未匹配到任何本地购买记录也接受报价；accept 失败永久卡单

- **严重程度**：P1（账目错乱；已入库物品永远停留在待收货）
- **置信度**：中高
- **文件与行号**：`app/receive_flow.py:300-317`（无条件 accept）；`app/receive_flow.py:127-131, 316-318`（accept 响应丢失→重试被拒→永久失败）；`app/services/workers.py:242-249`（worker 从不调 reconcile，只有手动接口兜底 routes/inventory.py:165）
- **代码证据**：
  ```python
  # receive_flow.py:316-317 —— 即使 items 全部未匹配本地记录也执行
  if not accept_steam_trade_offer(str(offer_id), steam_cookies):
      continue
  ```
- **触发条件**：报价内物品匹配不到本地购买记录（重复报价、换单遗留）；或 accept 成功但响应丢失后 DB 未更新。
- **实际影响**：接受非本地订单报价无法归账；已入库物品卡在 awaiting_trade，后台无人救。
- **最小修复**：accept 前要求至少一条匹配；accept 失败时仍执行一次库存扫描+按名匹配；worker 内定期调 reconcile。
- **应补充测试**：无匹配 items 的报价 → 断言不调用 accept；accept 抛异常 → 断言后续 reconcile 能补录。

---

### P2

- **P2-1 冷却解析失败 fail-open**（`app/inventory_cs2.py:20-22, 77-78`；置信度高）：`_parse_cooldown` 解析失败返回 `(text, 0.0)`，`can_trade = tradable == 1 and (not cd_ts or ...)` 中 `not 0.0` 为 True → 冷却中物品被判 `can_sell=True` 送进出售流水线（sell_pipeline.py:517）。Steam 端会拒绝上架（无直接损失），但 UI 冷却状态与 order_status（receive_flow.py:374）全错，每轮重复上架尝试。现有测试只覆盖解析成功路径。修复：解析失败且 cooldown_text 非空按 fail-closed；`cd_ts` 用 None 与 0.0 区分。测试：构造 "trade-protected" 但日期格式变化 → 断言 can_sell=False。
- **P2-2 批量锁单创建结果未知时静默降级单买**（`buff/buyer.py:707-708`；`app/services/buff_client.py:141-165`；`app/pipeline_steps.py:1622-1630`；置信度高）：`batch_buy_create` 超时返回 None → 上层认为"批量不可用"→ 继续单买 → 冻结资金（frozen_amount）与已付款订单并存。修复：网络异常抛 BuffOrderOutcomeUnknown，禁止降级。
- **P2-3 卖单 price 缺失 → 除零崩溃（已修复）**：`count_lowest_price_orders` 现在忽略缺失、非数字和非正价格；没有有效价格时返回 `(0.0, 0)`，购买流水线在任何除法或锁单前明确跳过并记录警告。对应回归测试已通过。
- **P2-4 SQLite 无 WAL/busy_timeout**（`app/database.py:127-132`；置信度中）：多线程多连接并发写，默认 5s busy 后抛 "database is locked" → API 500 / worker 间歇失败。修复：engine connect 事件里 PRAGMA journal_mode=WAL + busy_timeout=15000。
- **P2-5 状态机运行中不复查阻断订单**（`app/pipeline.py:420-426, 457-466`；置信度高）：payment_unconfirmed 只在启动时阻断，运行中付款超时后继续锁下一单，待核对订单无限累积。修复：`_process_deals_for_target` 循环顶部每件前复查 get_blocking_payment_orders()。
- **P2-6 上架即记"售出"+ already have a listing 重复记账**（`app/sell_pipeline.py:87-100, 437-441`；置信度高）：上架成功/已存在在售均 `append_sale`，每轮补挂为同一资产追加 Sale 行，统计严重虚增。修复：`_build_listing_plan` 排除已在售 assetid；"already have a listing" 不 append_sale。
- **P2-7 fetch_my_listings 仅取 100 条**（`app/steam_listings.py:163`；置信度中高）：在售 >100 时同名上限失效、重复上架被计成功。修复：分页。
- **P2-8 delist 忽略响应体**（`app/steam_delist.py:285-308`；置信度中高）：下架失败返回 HTTP 200+{"success":false} 被当成功并清空本地 assetid，状态永久失同步。修复：解析响应体 success。
- **P2-9 售出统计 ×1.15 虚高**（`app/steam_listings.py:307`；`app/sync_sold.py:78`；`app/routes/transactions.py:337`；置信度中）：sold 金额 ×1.15 后再 ÷1.15 自相抵消，15% 手续费从未扣除，收益高估约 13%。口径需与 myhistory 金额语义对齐后修复。
- **P2-10 售出金额币种误判**（`app/steam_listings.py:242-258, 296-307`；置信度中高）："R$"/"AR$"/"CL$" 落进 USD、"¥" 认成 CNY、千分位逗号被替换成小数点（"1,234.56"→"1.234.56"→float 抛错静默跳过）。修复：逐币种符号映射 + 先清千分位。
- **P2-11 inventory 403 被当登录过期 → 自动重登风暴**（`steam/inventory.py:66-67`；置信度中高）：403 常见为 IP/地区风控，被误判为 Cookie 过期触发密码重登，可能被临时锁定。修复：403 时检查响应体登录页特征。
- **P2-12 风控中文文案未识别 → 重试放大封禁**（`buff/buyer.py:52-64`；置信度中）：标记表缺"风控/风险/交易异常"，锁单失败被当普通 FAIL 重试 3 次。修复：补充中文风控关键词。
- **P2-13 余额支付 TOCTOU**（`buff/buyer.py:182`；`app/pipeline_steps.py:1130-1148`；置信度中）：唯一余额≥金额校验在预览时点，锁单前无复核（依赖 BUFF 服务端兜底）。修复：锁单前进程内重比 preview_balance。
- **P2-14 fetch_history 302 不识别登录过期**（`steam/client.py:293-300`；置信度中）：过期 Cookie 下价格轮询持续空转拉高 429。修复：302/403 检测登录页特征置 auth_expired。
- **P2-15 accounts.json 无锁读写**（`app/accounts.py:6-25`；置信度中）：后台 update_account 与前端编辑并发 read-modify-write 可写坏凭据文件。修复：模块级 Lock。

### P3（摘要）

- 外部 CDN qrcode.min.js 无 SRI/无 CSP（`web/index.html:1827`）
- /api/steam_guard 明文返回 TOTP（`app/routes/auth.py:440-451`）
- /api/config 回显 webshare API key（`app/routes/proxy.py:24-27`）
- 全局关闭 InsecureRequestWarning（`buff/buyer.py:30`）
- 金额 float 运算（`buff/buyer.py:689` frozen_amount 精度噪声）
- 硬编码示例钱包数据 g_rgWalletInfo（`utils/money.py:12-29`，低价品统一按 0.07 挂出）
- 全局节流锁内 sleep（`utils/throttle.py:13-14`）
- `_history_cache` 无锁共享字典（`app/services/steam_client.py:11-13`）
- sync_sold 同名盲匹配 assetid（`app/sync_sold.py:3-26`）
- GET /api/orders 读接口带写副作用（`app/routes/transactions.py:211`）
- email 模式 `wait_payment_confirm()` 无超时（`app/pipeline_steps.py:872`，线程异常死亡则无限等待）
- migrate_from_json 边缘复活已清空数据（`app/database.py:249-279`）

---

## 4. 需要进一步确认的风险

1. **Steam sellitem `price` 参数语义**（买家支付价 vs 卖家到手价）——决定 P0-1 的精确修复方向。按公开语义为买家支付价判定为 bug；一次真实上架实验可确认。
2. **BUFF 对已被锁定的 sell_order 重复 POST /buy 的行为**（拒绝 vs 放行二次下单）——决定 P1-1 的最坏后果（双订单双付款 vs 悬挂订单）。
3. **BUFF 风控实际返回形态**（200+错误码文案 / 403）——决定 P2-12 命中率。
4. **Steam 当前 inventory owner_descriptions 冷却文案是否仍含字面 "trade-protected"**——决定 P2-1 当前是否生效。
5. **CS2 下架后 assetid 是否必然变化**——决定 P2-8 的 `len(new_ids)==0` 分支是否每次命中。
6. **myhistory 售出行金额口径**（买家价 vs 到手价）——决定 P2-9 的 ×1.15 是修正还是引入误差。
7. **BUFF 余额预览 pay_method 通道编号会话内是否恒定**——影响 P2-13。
8. **BUFF 余额锁单时服务端是否强校验余额**——决定 P2-13 的实际缓解程度。
9. **batch 冻结资金自动解冻时限**——决定 P2-2 的损失窗口。
10. **gift.js XSS 可利用性**：Steam 好友昵称/商品名是否实际允许 < / " 字符（实体解码后经 innerHTML 重新解析）。
11. **/api/log 及导出日志是否可能夹带 Cookie/凭据片段**（错误信息里含请求头时）。
12. **`get_and_buy`/`_execute_post_buy`（buff/buyer.py:449-543）**：与 P1-1 同类缺陷，全仓库无调用方（死代码），确认无其他入口引用后可删。

---

## 5. 测试缺口

现有测试（如 test_purchase_unit_price_cap.py、test_order_replacement.py、test_buff_balance_payment.py）只覆盖余额支付模式的单价上限与订单替换事务，最重要的失败路径全部缺失：

1. **锁单网络超时/结果未知**：P1-1 无测试（模拟 RequestException → 应走 PAYMENT_REVIEW_REQUIRED 而非重试）。
2. **冷却解析失败 fail-open**：P2-1 无测试（现有 test_inventory_cooldown.py 只测成功路径，test 3 只测前端函数存在）。
3. **receive_worker 与手动同步并发**：无并发测试（双线程 try_receive_once）。
4. **重复上架/重复记账**：P2-6 无测试（already have a listing 不应 append_sale）。
5. **邮件伪造确认**：P1-7 无测试（伪造发件人/主题子串）。
6. **停止语义（余额路径）**：P1-2 无测试（停止后不应调 pay_bill_order_once）。
7. **取消/确认竞争**：P1-5 无测试（cancelled 后 confirm 不应复活订单）。
8. **上架价格换算语义**：P0-1 无测试钉住 price 参数语义。
9. **整表替换并发**：P1-6 无测试（worker 写入 vs replace）。
10. **SQLite 并发写**：P2-4 无测试（busy_timeout/WAL 行为）。
11. **风控/403/429 文案识别**：P2-11/P2-12 无测试。
12. **汇率/币种解析**：P2-10 无测试（千分位、R$/ARS 符号）。

---

## 6. 审查盲区

- **未联网验证**：BUFF/Steam 服务端行为（重复锁单、风控形态、冷却文案、sellitem 语义、冻结解冻）全部依赖真实网络交互，本次未发起任何请求。
- **未运行任何代码/测试**：所有结论基于静态阅读；并发缺陷（P1-6、P2-4、P2-15）的实际触发频率无运行时证据。
- **未通读的文件**：`app/strategy_engine.py`（仅核查模块执行策略，未逐行）、`app/steam_listings.py`、`app/gift_engine.py`、`web/index.html` 全文、`app/config_loader.py`、`app/notify.py` 全文、`app/services/steam_auth.py` 全文、`utils/trend.py`。这些文件内的低危问题可能未被发现。
- **未读取的敏感文件**（按要求跳过）：config/credentials.json、accounts.json、app_config.json、app.db、.maFile、log/、debug.log——cookie 明文落盘与死 cookie 判定（P3）仅基于代码推断，未核对实际文件。
- **未验证**：requirements 依赖漏洞（未跑 pip-audit）、SQLite 文件与凭据文件 ACL、Windows 防火墙实际暴露、多进程部署（uvicorn workers>1 时 `_sell_phase_lock` 失效）。
- **前端渲染口径**：get_sales() 在 UI 上的具体展示（交易列表如何渲染"售出"行）未逐行确认，P2-6 的影响面基于 transactions.py:195-199 的推断。

---

*报告完。全文为只读审查结果，未修改除本报告外的任何文件。*

---

---

# 附录 A：第二轮对抗性复查（2026-08-06）

## A.0 复查方法

本轮不重复第一轮结论，专门做三件事：
1. **尝试推翻第一轮每个 P0/P1 判断**（自我反证）；
2. 沿三个方向找跨文件漏洞：**网络请求已成功但本地认为失败**、**两个 worker 同时处理同一订单**、**金额/币种在不同模块含义不一致**；
3. 亲验 subagent 引用过但第一轮未逐行核对的关键行号（steam_auth.py monkey-patch、steam_confirm.py、gift.js、strategy_engine.py、transactions.py cancel 端点）。

结论：**第一轮 2 个 P0 全部无法推翻；12 个 P1 中 11 个维持、1 个（P1-2）影响措辞微调；新增 3 个 P1、4 个 P2、2 个 P3 级跨文件发现；排除了 2 个怀疑（汇率方向、余额缓存并发覆盖）。**

---

## A.1 尝试推翻第一轮判断的结果

| 第一轮结论 | 反证尝试 | 结果 |
|---|---|---|
| P0-1 上架双重扣费 | 若 sellitem price=卖家净得价则反推正确 | **无法推翻**。代码内部自相矛盾（策略3 把 list_price/1.15 当买家价，list_price_display_to_cents 又当含费总额反推），任何语义下都低挂。缓和：低价物品受 wallet_fee_minimum=7 影响，低价区间实际偏差略小于 13%，方向不变 |
| P1-1 锁单未知当失败 | 若 BUFF 对同一卖单幂等返回同一订单号则降级 | **无法推翻**。即使幂等，响应丢失产生的悬挂订单仍无法本地识别 |
| P0-2 0.0.0.0 零认证 | 寻找 api.py 认证中间件 | **无法推翻**。api.py:48-77 亲验无任何中间件/依赖注入 |
| P1-2 停止后仍扣款 | balance 是用户已授权自动扣款，扣款有记录 | **维持 P1 但影响微调**：不产生"无记录资金外流"（订单会被记账），但用户明确停止后仍扣款违背停止语义，且与 N2-3 叠加后取消也无效 |
| P2-1 冷却 fail-open | 若 Steam 冷却期 tradable=0 则 can_trade 恒 False | **有条件缓解**：代码专门解析 "trade-protected" 说明作者遇到 tradable=1+冷却描述的组合，风险成立；最终依赖 Steam 当前字段形态（待确认项） |
| P1-3/P1-4/P1-8/P1-9（subagent 引用行号） | 亲验源码 | **全部确认**，无 agent 幻觉（见 A.4） |

---

## A.2 方向一：网络请求已成功但本地认为失败（新发现）

### N1-1（P1）上架请求成功但响应丢失 → 物品脱离监控 → 售出收益永久丢失

- **置信度**：中高
- **文件与行号**：`app/sell_pipeline.py:426-430`（`out = _do_list()` 返回 None 时不重试不标记）→ `steam/market.py:33-34`（`except Exception: return None` 吞掉超时）→ `app/services/workers.py:266`（listing_check_worker 只追踪 `listing=True`）
- **调用链**：`_submit_listings` → `list_item` POST sellitem → Steam 已创建 listing 但响应超时 → 返回 None → `if not out: 警告; continue` → **本地 listing 永不置 True** → listing_check_worker 永不追踪该 assetid → 物品售出后 `sale_price` 永不写入 → 收益丢失且不会自动补挂。
- **与第一轮 P2-6 的区别**：P2-6 讲"already have a listing 重复记账"（响应成功但语义重复）；本条讲**响应丢失 → 完全不记录**（另一条路）。若后续 sell phase 再次触发（手动刷新），会因 "already have a listing" 修正标记；但 listing_check_worker 的补挂触发链依赖 `listing=True` 的记录，未标记物品售出后没有任何触发点。
- **最小修复**：`list_item` 异常时返回"结果未知"标志而非 None；`_submit_listings` 对未知结果查询一次 `fetch_my_listings` 确认是否已上架，已上架则正常标记。
- **应补充测试**：模拟 list_item 抛异常 → 断言后续 fetch_my_listings 确认分支被调用。

### N1-2（P2）batch_buy_finalize 核销成功但响应丢失 → 部分订单未记账

- **置信度**：中
- **文件与行号**：`app/services/buff_client.py:167-192`（`batch_buy_find_and_finalize` 中 `batch_buy_finalize` 异常返回 None → 该订单不算 matched）→ `buff/buyer.py:754-763`（`except Exception: return None`）
- **实际影响**：批量核销中某单 finalize 请求已到达 BUFF（冻结资金已转成订单）但响应丢失 → 本地只记录部分订单 → 台账数量/金额与 BUFF 侧不一致；物品到货后靠 receive 按名称补录，但 order 表 total_price 与 Purchase 之和永久错位。
- **最小修复**：与 P1-1 同族——网络异常抛 `BuffOrderOutcomeUnknown`，调用方记录 unknown 台账。

### N1-3（P2）accept 重试间隔 1s 无退避（补充第一轮 P1-11 的根因细节）

- **文件与行号**：`app/receive_flow.py:8-18`（`steam_request` 重试 `jittered_sleep(1)`）；两个并发入口（N2-2 场景）可叠加为同一报价最多 6 次 POST。

---

## A.3 方向二：两个 worker 同时处理同一订单（新发现）

### N2-1（P1）邮件确认线程跨订单串扰：A 订单的迟到确认命中 B 订单（第一轮遗漏）

- **置信度**：高
- **文件与行号**：`app/notify.py:109-176`（`wait_email_command` 循环到 timeout 才返回）；`app/pipeline_steps.py:866-874`（`_email_waiter` 线程启动后**无生命周期管理**，`wait_payment_confirm()` 返回后线程仍存活）；`app/state.py:179-182`（`confirm_payment` 无订单标识）
- **调用链**：订单 A 等待 → 用户手动点"已付款"（confirm_payment(True)）→ A 完成、进入订单 B 的等待 → **A 的 email 线程仍在轮询 IMAP** → 300s 超时后 `confirm_payment(False)` → 命中 B 的等待 → B 被误判"未确认付款" → payment_unconfirmed 阻断后续全部锁单（需人工核对）。
- **触发条件**：配置了邮箱通道（email_user/email_pass）+ 用户手动确认过一个订单 + 该订单的 email 线程超时晚于下一订单开始等待。
- **现有保护为何没拦住**：`set_pending_payment(None)` 只清 UI 状态，不清 email 线程；`confirm_payment` 写入全局 `_user_confirmed` 无当前等待者校验。
- **最小修复**：`confirm_payment` 增加"当前等待订单纪元"（等待入口生成、确认时比对，不匹配丢弃）；email 线程在订单完成后收到取消信号。
- **应补充测试**：A 完成进入 B 后，A 的迟到 confirm_payment(False) → 断言 B 不受影响。

### N2-2（P2）并发 assetid 重复分配 → 同一物品记两次售出收益

- **置信度**：中
- **文件与行号**：`app/receive_flow.py:254-384`（`try_receive_once` 内 `already_used`/`assigned_db_ids` 为线程局部）与 `app/routes/inventory.py:142-181`（手动 sync 同一函数）并发；后果链 `app/services/workers.py:287-312`（listing_check_worker 按 assetid 更新**所有**匹配记录）
- **调用链**：双线程同时 scan_inventory → 各分配同一 assetid 到不同 purchase → 售出后 listing_check_worker 对两条记录都写 sale_price → **同一物品记两次收益**（sold_updates 无跨记录去重，workers.py:288-291 的 seen_aids 只防同轮重复 aid，不防两轮）。
- **最小修复**：db 层给 Purchase.assetid 加唯一约束（或 receive 模块级 `threading.Lock`）。

### N2-3（已完成第一阶段修复）cancel 与余额自动支付竞争

> **修复记录（2026-08-06）**：余额订单处于 `awaiting_payment` 自动扣款阶段时取消接口会拒绝操作；后续状态写入使用原子条件；终止状态不会被库存对账复活。

- **置信度**：高
- **文件与行号**：`app/routes/transactions.py:223-237`（cancel 置 cancelled，不通知流水线）→ `app/pipeline_steps.py:1142-1253`（balance 路径锁单后**无任何订单状态复查**直接 `pay_bill_order_once` 扣款）→ `app/database.py:324-338`（`db_update_purchase_order` 无条件覆盖回 awaiting_ship）
- **触发条件**：balance 模式，锁单后、扣款前的数秒窗口内用户点击取消。
- **实际影响**：用户明确取消的订单仍被自动扣款，且 cancelled 被覆盖为 awaiting_ship（与第一轮 P1-5 同根：`db_update_purchase_order` 无条件覆盖 + 无取消信号）。
- **最小修复**：与 P1-2 合并修复——balance 路径付款前复查订单状态；`db_update_purchase_order` 增加期望状态 CAS。

---

## A.4 方向三：金额/币种含义不一致（新发现）

### N3-1（P2）硬编码汇率 7.2 vs 实时汇率文件 + 未知币种静默当 CNY

- **置信度**：中
- **文件与行号**：`app/sell_pipeline.py:59`（`_steam_latest_price_and_trend` 用 `USD_TO_CNY_DEFAULT` 而非 exchange_rate.json）；`utils/money.py:118-121`（`apply_currency` 未知币种 `return prices, code`，调用方忽略 code）
- **实际影响**：外币账号的趋势判断/利润比例护栏使用硬编码 7.2（与实时汇率偏差可达 10%+）；未知币种价格原样当 CNY 参与计算。
- **修复**：统一走 exchange_rate.json；未知币种显式跳过并告警（对齐 sell_pipeline.py:262-269 上架侧的 fail-safe 行为）。

### N3-2（P2）myhistory 只取前 100 条 → 售出收益记录遗漏

- **置信度**：中
- **文件与行号**：`app/steam_listings.py:206`（`fetch_my_history_sold` 参数 `count=100, start=0`，无分页）
- **实际影响**：高频账号售出记录超过 100 条时，最早售出的物品 `sale_price` 永不写入（listing_check_worker 判定"不在 sold_map"→ 标记 error 而非售出）→ 收益丢失。与第一轮 P2-7（mylistings 100 条）同族。
- **修复**：分页拉全（或至少覆盖在售数量）。

### N3-3（P3）1.15 因子三处含义不一致

- 买入链 `STEAM_FEE_FACTOR=1.15`（`app/pipeline_steps.py:20`，乘法，估价用）；
- 卖出同步链 `×1.15`（`app/steam_listings.py:307`，第一轮 P2-9，高估）；
- 报表链 `÷1.15`（`app/notify.py:205`，方向正确）。
- **影响**：P0-1 修复上架链时，三处 1.15 必须统一语义（买家价/净得价），否则统计口径再次分裂。

### N3-4（已修复）PushPlus 明文 HTTP

> **修复记录（2026-08-06）**：发送端点已改为 `https://www.pushplus.plus/send`，并新增协议回归测试；未发送真实测试通知。

- `app/notify.py:13-17`：`requests.post("http://www.pushplus.plus/send", ...)`，token 与内容（含付款链接）明文传输。改 HTTPS。

---

## A.5 已排除的怀疑（对抗性审查的负结果）

1. **fetch_my_history_sold 汇率方向怀疑**：验证 exchange_rate.json 语义（`_fetch_exchange_rates` 存 1/rate，即"1 外币 = X CNY"），`cny_raw = raw * rate` **乘法方向正确**，不成立。
2. **buff_balance 缓存并发覆盖怀疑**：`record_buff_balance_preview`（预览同步执行）必然先于 `record_confirmed_buff_spend`（付款后），顺序有保证，不成立。
3. **sell phase 双触发并发**：`_sell_phase_lock`（`app/sell_pipeline.py:605-612`）进程内互斥（blocking=False 拒绝），不成立（多进程部署除外，见盲区）。

---

## A.6 原报告优先级建议（已由第2节复核表取代）

在保持第一轮修复清单的基础上，本轮新增按优先级：

1. **N2-1（P1）email 线程跨订单串扰**——与第一轮 P1-4（付款确认无关联）合并修复（confirm_payment 加订单纪元），是当前付款确认链最危险的竞态。
2. **N2-3（P1）balance 路径取消无效**——与 P1-2/P1-5 合并（付款前复查状态 + CAS 更新）。
3. **N1-1（P1）上架响应丢失脱离监控**——与 P0-1 修复同批处理（list_item 结果三态化）。
4. N1-2、N2-2、N3-1、N3-2 按 P2 处理。

---

## A.7 本轮新增盲区说明

- **IMAP 行为未实测**：N2-1 的触发窗口依赖 `wait_email_command` 的实际返回时机（每 3s 轮询 + 网络延迟），端到端未验证。
- **Steam sellitem/库存字段行为仍未联网验证**（同第一轮待确认项 1、4）。
- **`_sell_phase_lock` 多进程失效**：当前单进程部署（main.py:84-89），若未来 uvicorn workers>1 该锁失效（第一轮盲区重申）。

---
