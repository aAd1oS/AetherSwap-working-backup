# AetherSwap 购买流动性保护专项审查（实施修订版）

- 审查日期：2026-08-06
- 审查目标：系统是否能避免买入**成交量低、卖压高、难以在 Steam 出手**的商品
- 初次审查方式：只读静态审查，未发起网络请求；随后按复核结论实施本地保护并运行测试。

## 实施更新（2026-08-06）

- 已在 `filter_iflow_rows` 增加本地 `min_volume` 硬校验，复用 SteamDT 请求配置，不另设第二套阈值。
- 当前配置 `min_volume=200`：SteamDT 返回的 `transactionCount < 200` 会在本地直接淘汰；成交量字段缺失、非数字或非正数按 0 处理并拒绝；门槛配置异常时安全回退到 200。
- 校验发生在稳定性分析、Steam 市场请求、策略模块和 BUFF 锁单之前，因此不依赖 SteamDT 服务端是否正确过滤，也不会被关闭 `guard.purchase_liquidity_cap` 绕过。
- 候选上限调整为“先完成本地过滤和冷却排除，再按 SteamDT 原始排名取前 N 条”，关键词、无效数据或上一轮商品不再占用 30 件检查额度。
- 新增单轮候选冷却池：只记录本轮真正开始预检的 `goods_id`，下一轮暂时排除，随后自动解除；无需数据库、逐商品计时器或清理线程。
- SteamDT 拉取失败不会推进单轮冷却；网络保护只检查了少量商品时，也只冷却实际开始检查的商品。原有严重故障 30 分钟 TTL 独立保留。
- 筛选日志新增 `成交量不足(<门槛)=数量`、`轮冷却排除=数量` 和“合格后取前 N 条”，便于确认保护实际命中及候选补位。
- 未增加网络请求，未修改历史稳定性、卖压、代理和支付逻辑。
- 累计新增 8 项针对性测试；完整测试结果为 `245 passed`。

---

## 一、结论（先看这里）

1. **原审查发现的主要结构性缺口已经修复**：本地现在直接用 SteamDT 的 `transactionCount` 与配置的 `min_volume` 比较，不再只依赖服务端过滤或通过采购数量间接限制。
2. **当前 `min_volume=200` 同时用于 SteamDT 请求和本地复核**。服务端参数失效时，低于 200、缺失或异常的成交量仍会被本地候选过滤挡住；用户主动调低或关闭门槛则属于明确配置行为。
3. **原报告所述 `daily_volume>=20/40` 可买 1 件的路径，在当前 `min_volume=200` 下已经不可达**。只有用户把门槛调低到相应范围时，流动性数量上限才会成为最后一道保护。
4. **卖压检查（sell_pressure）有效**：默认启用且阈值 2.0，在预检与锁单前各查一次。
5. **`日销量为0，跳过` 仍是含义不够清楚的旧日志，但不再构成保护绕过**：成交量为 0 的候选已经在更早的本地门槛处拒绝。该日志只可能在用户主动关闭成交量门槛或其他兼容调用路径中出现。

---

## 二、数据流追踪

### 2.1 SteamDT transactionCount 完整数据流

config.iflow.min_volume（默认 200）
  → iflow_client.py:105  params["min_volume"]
  → iflow_client.py:63   SteamDTQueryParams.min_transaction_count
  → steamdt/models.py:30 to_payload()["minTransactionCount"]
  → steamdt/fetcher.py:43  POST body（仅此一处，服务端过滤）
  → 响应解析 steamdt/parser.py:47  volume=str(item.get("transactionCount", 0))
  → app/pipeline_steps.py  vol = int(volume or 0)，异常→0；低于本地 min_volume 立即过滤
  → 购买判定：pipeline_steps.py:379-381（sell_pressure 前置）、1405-1409（liquidity cap）、analysis/stability.py:235-237（历史门槛）

**关键事实（实施后）**：
- `min_volume` 从配置透传到请求参数，并在 `filter_iflow_rows` 解析 `volume` 后进行本地第二次校验；低于门槛不会写入候选列表。
- `transactionCount` 的统计周期（24h？7 天？）在代码中**未定义**——parser.py:47 原样透传为字符串，无任何语义注释或归一化。

### 2.2 各保护环节的实际作用

| 环节 | 位置 | 默认启用 | 门槛 | 是否真正阻止锁单 |
|---|---|---|---|---|
| SteamDT minTransactionCount + 本地复核 | fetcher.py:43 / pipeline_steps.py | 200 | 服务端过滤后本地再次比较 | **是** |
| 卖压 sell_pressure | pipeline_steps.py:379-381, 1372-1374 | 是（threshold=2.0） | 前5档总量/日销 > 2.0 | **是**（daily_vol>0 且卖单非空时） |
| 流动性件数上限 liquidity cap | pipeline_steps.py:1405-1409, 1423-1426 | 是（ratio=0.05） | daily_volume x 5% 件 | **部分**（只限件数，daily_vol>=20 仍买 1 件） |
| 历史稳定性 min_daily_trades | stability.py:235-237 | 是（5） | 30 天 count>150 | **部分**（count 是记录条数非成交量） |
| 无历史数据 fail-closed | stability.py:165,182,188 | — | 数据不足即拒 | **是** |
| max_discount 二次验证 | pipeline_steps.py:1350-1367 | 是（0.9） | 锁单前复查 | 是（价格比例，非流动性） |

---

## 三、分项结论

### A. 已确认有效（当前默认配置下触发）

1. **liquidity cap 对 `daily_volume=0` 的拦截**
   - 证据：pipeline_steps.py:1405-1409（`volume_cap = int(daily_volume * 0.05)` → 0）、1423-1426（`safe_limit <= 0` → SKIP_NO_FAILED）；模块默认启用（strategy_engine.py:369）。
   - 当前配置：`safe_purchase_liquidity_ratio=0.05`（config_schema.py:59）。它现在是本地 `min_volume` 硬门槛之后的第二层数量保护。
   - 局限性：只拦截 `daily_volume < 20`（非低价）或 `< 40`（低价，受 penalty=0.5 影响，pipeline_steps.py:1407-1408）。`daily_volume=20` 时 `int(20 x 0.05)=1` → 允许买 1 件。

2. **卖压检查（sell_pressure）在预检与锁单前双重执行**
   - 证据：pipeline_steps.py:379-381（`_check_sell_pressure_precheck`，pick 阶段）、1371-1380（lock 阶段复查，用缓存或重拉的 Steam 卖单）。
   - 当前配置：模块默认启用（strategy_engine.py:361）+ `sell_pressure_threshold=2.0`（config_schema.py:64）→ 前 5 档卖单量/日销 > 2.0 即拒绝。**前提**：`daily_vol > 0` 且卖单数据非空（pipeline_steps.py:379, 1372）。

3. **历史稳定性分析默认执行且数据不足时 fail-closed**
   - 证据：`history_analysis_enabled` 默认 True（strategy_engine.py:363 guard.history_data_window 启用 → pipeline_steps.py:524-535）；无历史 → `continue` 跳过（pipeline_steps.py:693-708）；数据点 < MIN_TRADES=5 → valid=False（stability.py:187-188）。

### B. 部分有效或已补强

1. **`min_volume=200` 本地复核（已补强）**
   - SteamDT 服务端仍负责第一次过滤；本地解析 `transactionCount` 后再次执行相同门槛。
   - 服务端是否忠实应用参数仍可动态观察，但不再影响本地安全结论。
   - 剩余语义问题是 `transactionCount` 的统计周期未知，而不是代码缺少阈值执行。

2. **历史稳定性的"成交量"门槛实际是"记录条数"门槛**
   - 证据：stability.py:235-237 `base_ok = count > (days * min_daily_trades)`——`count` 是价格历史**条目数**（stability.py:186 `count = len(prices)`），Steam pricehistory 每条是一个时间桶（小时级）的聚合成交记录；`volume`（件数）只用于 vwap 加权（stability.py:194, `_vwap_iqr`:61-66 `v>0` 才累计），**不参与任何拒绝**。
   - 语义偏差：`min_daily_trades=5` 实际 = "30 天 >=151 个有成交记录的时间桶"（日均 ~5 桶）。对日均 5 桶、每桶 1 件的商品（=日均 5 件成交）放行——**这在 CS2 属于低流动性**。
   - Steam 历史数据中的成交件数仍未直接参与稳定性拒绝；但 SteamDT 的 `transactionCount` 已经参与候选硬拒绝。两者数据源和统计口径不能混为一谈。

3. **liquidity cap 只限制"一次买几件"，不限制"是否买 1 件"**
   - 证据：pipeline_steps.py:1428-1430（`num_to_buy = min(count_at_lowest, max(1, int(remaining/lowest_price)))` 再 `min(num_to_buy, max(1, safe_limit))`）——`max(1, ...)` 保证至少 1 件。
   - 影响：只有用户把本地 `min_volume` 调低到 20 附近或关闭时，这条“至少买 1 件”的行为才会成为风险；当前门槛 200 下不可达。

### C. 无效或绕过（假保护 / 可绕过路径）

1. **"日销量为0，跳过"日志含义不清，但已不构成当前绕过**
   - 证据：pipeline_steps.py:385-386（`elif daily_vol <= 0 and log_fn: log("日销量为0，跳过")`）与 1379-1380（同）。
   - 实施后，`daily_volume=0` 会先被本地 `min_volume` 门槛淘汰。自定义策略关闭 liquidity cap 也不能绕过候选过滤；只有用户主动把 `min_volume` 设置为 0 才会关闭这层保护。

2. **原低流动绕过路径的实施后状态**：
   - **路径 A**：SteamDT 服务端参数未生效——**已被本地复核阻断**；用户主动调低 `min_volume` 时仍可能放行，属于配置风险。
   - **路径 B（低价）**：原 `daily_volume=40~59` 放行路径在当前 `min_volume=200` 下**已被阻断**；主动调低门槛后仍可能出现。
   - **路径 C**：自定义策略禁用 liquidity cap——**不能绕过本地 `min_volume` 候选过滤**。
   - **路径 D**：`transactionCount` 缺失、异常或为 0——**已按 0 fail-closed 拒绝**。

3. **锁单阶段不复查历史稳定性**
   - 证据：`lock_and_confirm_payment`（pipeline_steps.py:1266-1633）复查 max_discount（1350-1367）、sell_pressure（1371-1380）、safe_limit（1399-1426），**无历史分析**。pick（pipeline_steps.py:686-758）与 lock 之间的耗时窗口内（含多次网络请求，可达数分钟）市场变化不复查。同轮数据一致性可接受，但严格说属于单点校验。

### D. 需要动态验证（代码内无法定论）

1. **SteamDT `transactionCount` 的统计周期**：parser.py:47 原样透传，无归一化。若为 7 天总量而非 24h，`min_volume=200` 的实际日均门槛仅 ~29 件/天——**比预期宽松 7 倍**。
2. **SteamDT 服务端是否忠实应用 `minTransactionCount`**：fetcher.py:41-81 无任何日志/校验确认服务端过滤生效，需一次真实请求验证（本次未联网）。
3. **Steam pricehistory 的记录粒度**（小时桶？）与 `volume` 字段是否恒为 1/0：决定 history 门槛（count>150）的实际强度与 vwap 是否退化。stability.py:186 用 `len(prices)`，若 Steam 返回按小时聚合的条目，日均 5 桶=日均 5 小时有成交。
4. **sell_pressure 的 `daily_volume` 口径**：`_compute_sell_pressure_from_orders`（pipeline_steps.py:198-232）用 `total_vol / daily_volume`——若 daily_volume 是 7 天总量，压力值被低估 7 倍，`threshold=2.0` 形同虚设。

---

## 四、最小改进建议

| # | 改进 | 文件 | 代码规模 | 回归风险 | 说明 |
|---|---|---|---|---|---|
| 1 | **本地 volume 硬门槛** | `app/pipeline_steps.py` | 已完成 | 低 | 复用当前 `min_volume=200`，没有另设默认 50 |
| 2 | **history 门槛改用真实成交量**：`base_ok = total_volume > days * min_daily_trades`（用 `sum(volumes)` 而非 `len(prices)`，stability.py:198 已有 total_volume） | `analysis/stability.py` | ~2 行 | 中 | 会拒绝更多商品（日均 5 件以下），需观察误杀率 |
| 3 | **修正假保护日志**："日销量为0，跳过"改为"日销量为0，已由流动性上限拦截"或直接 `return False` | `app/pipeline_steps.py:385-386, 1379-1380` | ~2 行 | 零 | 纯日志/行为澄清 |
| 4 | **min_volume 本地重校验** | `app/pipeline_steps.py` | 已完成（与第1项合并） | 低 | 服务端与本地双保险 |
| 5 | **lock 阶段复查 history**（可选）：锁单前再跑一次 `analyzer.analyze` 或至少复查 `total_volume` | `app/pipeline_steps.py` | ~10 行 | 中 | 网络成本 +1 请求/件，防 pick-lock 窗口漂移 |

本轮实际修改 `app/pipeline_steps.py` 和对应测试；历史 `total_volume` 门槛、锁单前重复历史请求均未实施。前者需要先观察真实数据避免误杀，后者会增加 Steam 请求和 429 风险，不建议当前实施。

---

## 五、审查盲区

- SteamDT 服务端行为、pricehistory 粒度、transactionCount 周期均需真实网络请求验证（本轮未联网）。
- `analysis/stability.py` 的 `_parse_item_date`（stability.py:70-75）依赖 `"%b %d %Y %H"` 格式，Steam 历史接口日期格式变化时所有条目被丢弃 → 触发 fail-closed（安全方向，但会误杀全部商品，已作为保守方向记录）。
- 自定义策略对 liquidity cap 的合并/替换方式仍值得单独验证，但已不能绕过策略执行前的本地 `min_volume` 候选过滤。
