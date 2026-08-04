# AetherSwap 网络配置与故障排查手册

更新时间：2026-08-02

适用环境：Windows，本地目录 `D:\Vibe Coding\steam`，使用 `start-aetherswap-desktop.cmd` 启动。

## 一、先看最终结论

本机推荐长期使用下面这一套组合：

1. Clash Verge：必须保持运行，使用“规则模式”，选择一个稳定、低延迟、不过度共享的香港节点。
2. AetherSwap：Steam 专用代理保持为 `127.0.0.1:7890`，代理策略保持“始终使用代理”。
3. Buff：必须走国内直连，不能跟着 Steam 走海外代理。
4. UU：只在需要打开 Steam 客户端、库存或游戏下载时使用；排查 AetherSwap 时先关闭。
5. Steam++ / Watt Toolkit：运行 AetherSwap 时建议关闭，避免本地证书、Hosts、反向代理与 Clash 重叠。
6. 其他美国 VPN：关闭。不要同时运行美国 VPN、Clash、UU、Steam++四套路由。
7. 启动任务以后不要切换节点、开关代理或更换网络。需要切换时，先停止任务。

一句话版本：

> Clash 规则模式常开；AetherSwap 的 Steam 请求走 Clash；Buff 和国内接口走直连；UU只照顾 Steam 客户端；Steam++和额外 VPN 关闭。

## 二、为什么不开 Clash 也会报同样错误

日志中的典型错误是：

```text
ConnectionResetError(10054, 远程主机强迫关闭了一个现有的连接)
```

这并不表示“开不开 Clash 都一样”，而是两种不同网络状态最终都可能表现为请求失败：

- 不开 Clash：Python 后台直连 Steam，连接被重置，通常表现为 `10054`。
- 开 Clash但 AetherSwap没使用它：依然裸直连，还是 `10054`。
- 开 Clash且使用共享严重的节点：能连上 Steam，但可能变成 `HTTP 429`。
- 同时开 Steam++、UU、Clash：不同请求可能走不同路径，可能出现 SSL、Cookie地区不一致或偶发成功。

UU通常主要接管 Steam 客户端流量，不保证接管 AetherSwap 的 Python HTTPS 请求。因此“Steam客户端能打开”不等于“AetherSwap后台能连接”。

## 三、当前已经写入的 AetherSwap 配置

AetherSwap 目前的代理池配置为：

```text
启用：是
策略：2（始终使用代理）
主机：127.0.0.1
端口：7890
用户名：空
密码：空
```

含义是：AetherSwap 把 Steam HTTP 请求交给本机 Clash，由 Clash 决定最终出口节点。

程序网络边界已经修正为：

- Steam 登录浏览器：走 Steam 代理。
- Steam Cookie 验证：走 Steam 代理。
- Steam 社区市场、库存、历史价格、钱包、商店、结算：走 Steam 代理。
- Steam 交易报价接受与手机确认：走 Steam 代理。
- Buff 行情、下单、订单查询、待收货查询：强制直连。
- Buff 登录浏览器和后台保活浏览器：明确禁用浏览器代理。

## 四、各服务应该走哪条网络

| 服务 | 主要地址 | 推荐路径 | 是否需要 UU | 说明 |
| --- | --- | --- | --- | --- |
| Steam 社区市场 | `steamcommunity.com` | Clash 稳定节点 | 否 | AetherSwap最频繁访问的部分 |
| Steam 商店与登录 | `store.steampowered.com` | Clash 稳定节点 | 否 | 用于登录、Cookie验证和鉴权 |
| Steam Web API | `api.steampowered.com` | Clash 稳定节点 | 否 | 用于钱包与购物车接口 |
| Steam 结算 | `checkout.steampowered.com` | Clash 稳定节点 | 否 | 赠送或结算时使用 |
| Steam 客户端 | Steam程序本身 | UU可选 | 可选 | 与Python后台不是同一条链路 |
| Buff | `buff.163.com` | 国内直连 | 否 | 海外IP、频繁切IP容易触发风控 |
| SteamDT | `www.steamdt.com` | 国内直连 | 否 | 候选饰品和挂刀比例数据 |
| 汇率接口 | `open.er-api.com` | 直连 | 否 | 失败通常不会阻断核心交易 |
| PushPlus | `pushplus.plus` | 国内直连 | 否 | 仅用于通知 |
| Codex/聊天 | OpenAI相关地址 | Clash | 否 | 可以与AetherSwap同时使用 |

## 五、Clash Verge 应该怎样设置

### 1. 使用规则模式

不要使用全局模式。全局模式会让 Buff 也走海外出口。

在 Clash Verge 中选择：

```text
代理模式：Rule / 规则
节点：稳定香港节点
TUN：排查阶段关闭
系统代理：可以开启，供浏览器和Codex使用
```

香港节点不是绝对要求，但应满足：

- 长期固定，不频繁切换国家。
- 延迟和丢包较低。
- Steam 社区没有 `429`。
- 不使用免费、公开、高度共享节点。

### 2. 域名分流原则

不同 Clash 配置中的代理组名称不同，下面的 `PROXY` 需要替换成你配置中实际的节点组名称：

```yaml
DOMAIN-SUFFIX,buff.163.com,DIRECT
DOMAIN-SUFFIX,163.com,DIRECT
DOMAIN-SUFFIX,steamdt.com,DIRECT
DOMAIN-SUFFIX,pushplus.plus,DIRECT
DOMAIN-SUFFIX,steamcommunity.com,PROXY
DOMAIN-SUFFIX,steampowered.com,PROXY
DOMAIN-SUFFIX,steamstatic.com,PROXY
```

规则顺序很重要：Buff和国内直连规则放在兜底规则之前。

不熟悉 Clash 配置文件时，不要直接大改 YAML。只要保证规则模式、Buff直连、Steam走选定节点即可。

### 3. 端口必须一致

AetherSwap 当前使用：

```text
127.0.0.1:7890
```

如果 Clash 的混合代理端口改成了其他数字，AetherSwap也必须同步修改。否则会出现：

```text
ProxyError
Connection refused
无法连接到 127.0.0.1:7890
```

## 六、每天正确启动顺序

### 启动前

1. 确认没有未支付 Buff 订单。
2. 确认没有等待接受的 Steam 报价。
3. 打开 Clash Verge。
4. 切换到固定香港节点。
5. 选择规则模式。
6. 暂时关闭 Steam++。
7. 第一次排查时关闭 UU；稳定后可按需给 Steam 客户端使用。
8. 不要再开启单独的美国 VPN。

### 启动 AetherSwap

必须双击：

```text
D:\Vibe Coding\steam\start-aetherswap-desktop.cmd
```

不要直接双击 `run.py`，也不要在继承了 VPN 环境变量的终端里随意启动。桌面启动器会清理聊天软件留下的全局代理变量，再由 AetherSwap 自己执行 Steam/Buff 分流。

### 启动任务前检查

日志应先出现类似内容：

```text
[ProxyManager] 初始化完成: 代理数=1 已启用=True 策略=2
account_region: 同步完成 ... 币种=... 派生地区=...
```

还要确认：

- Steam Cookie验证成功。
- Steam钱包币种同步成功。
- Buff账号限制已经由客服解除。
- Buff网页能手动打开任意饰品详情。
- Buff重新登录后不再出现“未携带 session”。

全部通过后才能点击“启动任务”。

## 七、运行中能不能开 UU、Steam++或和 Codex 聊天

### Codex/聊天

可以。保持 Clash 规则模式即可。AetherSwap 会单独把 Steam交给 Clash，同时 Buff保持直连。

### UU

可以只用于 Steam 客户端，但建议遵守：

- 初次排查时关闭。
- AetherSwap已经正常运行后，不要再切换 UU 加速区服。
- 不要指望 UU 修复 Python 的 Steam API 请求。
- 下载游戏、打开客户端社区时可以用，AetherSwap本身不依赖它。

### Steam++ / Watt Toolkit

运行 AetherSwap 时建议关闭。它可能修改 Hosts、使用本地反向代理或安装本地证书，与 Clash同时工作时更容易造成：

- SSL握手失败。
- 某些 Steam 地址能开、某些不能开。
- 浏览器能开但 Python失败。
- Cookie登录时与任务运行时出口不同。

### 额外 VPN

不要使用。Clash已经承担代理作用，额外 VPN 只会增加嵌套和出口漂移。

## 八、运行过程中绝对不要做的事情

1. 不要在任务运行中从香港节点切到美国节点。
2. 不要在任务运行中关闭 Clash。
3. 不要同时反复启动和关闭 UU、Steam++。
4. 不要看到一次超时就立刻重复登录十几次。
5. 不要连续刷新 Steam市场或 Buff市场。
6. 不要把 Cookie、令牌、密码和完整日志发到公开网站。
7. 不要在待付款、待收货或待确认阶段改变网络。

需要换节点时：

```text
停止任务 -> 等待当前步骤结束 -> 关闭AetherSwap -> 换节点 -> 手动验证网页 -> 重新启动
```

## 九、错误信息对应处理方法

### `10054 远程主机强迫关闭连接`

含义：Steam HTTPS没有建立成功，常见于裸直连。

处理：

1. 确认 Clash正在运行。
2. 确认端口仍为 `7890`。
3. 确认 AetherSwap代理池是启用、策略2。
4. 关闭 Steam++后重启 AetherSwap。
5. 不要重新粘贴 Cookie，网络没通时改 Cookie没有意义。

### `HTTP 429`

含义：当前出口IP访问 Steam过于频繁，或该共享节点被其他用户用坏了。

处理：

1. 停止任务。
2. 更换另一个稳定香港节点。
3. 等待10至30分钟。
4. 先验证钱包同步，再启动任务。

不要通过提高重试频率解决，重试越快限制越严重。

### `HTTP 403`

含义：Steam拒绝当前请求，可能涉及 Cookie、地区、IP或访问方式。

处理：

1. 固定节点，不要继续切国家。
2. 在AetherSwap弹出的 Steam登录浏览器重新登录。
3. 确认登录浏览器与任务使用同一个 Steam代理。
4. 若普通浏览器也被拒绝，暂停并等待。

### `SSL`、证书或握手失败

常见原因：Steam++证书代理、Clash、杀毒软件 HTTPS扫描相互干扰。

处理：

1. 关闭 Steam++。
2. 关闭杀毒软件的 HTTPS扫描功能进行一次排查。
3. 保留 Clash一种代理。
4. 重启 AetherSwap。

### `refresh_token`

含义：自动账号密码登录没有拿到 Steam新登录流程所需的令牌，不等于账号密码一定错误。

处理：使用AetherSwap弹出的 Steam浏览器完成登录和 Steam Guard，不要反复尝试自动密码登录。

### `Steam Cookie验证失败`

先区分网络和登录：

- 同时伴随 `10054`、超时、ProxyError：先修网络。
- 请求正常返回但跳到登录页：Cookie过期，重新登录。
- Steam ID不一致：绑定了错误账号的 Cookie。

### Buff `未携带 session`

含义：Buff专用浏览器未登录或会话过期。

处理：在Buff直连状态下重新登录，并在弹出浏览器里进入一次市场和饰品详情页，再点击完成登录。

### Buff `Action Forbidden / 市场接口访问功能暂时关闭`

含义：Buff账号市场访问被平台限制。代码、Cookie和代理都不能绕过。

处理：

1. 立即停止自动任务。
2. 不要继续刷新和切IP。
3. 通过Buff App客服申诉。
4. 等客服明确解除后再测试。

### Buff接口返回 `None`

可能是网络、会话或风控被旧代码合并成同一个结果。按顺序判断：

1. Buff App能否进入市场。
2. 国内浏览器能否打开饰品详情。
3. Cookie是否包含 `session`、`csrf_token`、`Device-Id`。
4. 日志是否出现 `Action Forbidden`。

### SteamDT拉取失败

SteamDT走国内直连，不需要 Steam代理。先用普通浏览器打开 `https://www.steamdt.com/`。它失败不会通过开启 Steam++解决。

### `waiting retry / retry in 300s`

不一定是网络问题。它也可能表示本轮没有满足价格、成交量、折扣和稳定性条件的饰品。查看等待前的最后几条日志再判断。

## 十、最小化排查法

网络问题出现时，不要把所有工具同时打开。按下面顺序测试：

### 第一轮：只测试 AetherSwap后台

```text
Clash：开，规则模式，固定香港节点
UU：关
Steam++：关
额外VPN：关
AetherSwap：开，但不启动交易
```

只看钱包同步与账号验证。

### 第二轮：测试 Buff

```text
Buff：国内直连
AetherSwap：不启动交易
```

手动打开Buff市场和饰品详情。确认没有封禁、没有登录过期。

### 第三轮：测试 Steam客户端

钱包和Buff都正常后，才按需开启 UU 打开 Steam客户端。若开启 UU 后 AetherSwap再次异常，关闭 UU并保持 Clash单独运行。

## 十一、GitHub上的类似项目

### 1. SteamBuff_Market-WalletBalance

地址：<https://github.com/wsz987/SteamBuff_Market-WalletBalance>

定位：最接近“Steam倒余额”的工具。它是油猴脚本，在 Buff、C5Game、IGXE 页面按自定义比例筛选饰品，并提供 Steam社区连通性检测。

优点：

- 功能直接对应挂刀比例筛选。
- 91 Star，逻辑相对简单，容易人工复核。
- 不负责完整自动付款、收货和出售，资金控制权更强。

缺点：

- 最后主要更新停留在2022年，平台页面变化可能使脚本失效。
- 依赖另一个比例计算脚本。
- 主要是筛选助手，不是AetherSwap的完整替代品。

建议：值得作为“人工筛选和交叉验证”候选，不建议直接拿旧代码连接主账号自动交易。后续可以先审计脚本，再在新账号上只读测试。

### 2. SteamTradingSiteTracker

地址：<https://github.com/EricZhu-42/SteamTradingSiteTracker>

定位：BUFF、IGXE、C5、悠悠有品、ECO等平台的挂刀行情与历史数据。AetherSwap使用的 SteamDT/iflow思路与它接近。

优点：适合寻找低比例候选、查看趋势、做数据验证。

缺点：不是自动交易工具，不能替你付款、收货或出售。

建议：继续作为行情参考，不必重复部署整套服务。

### 3. Steamauto

地址：<https://github.com/Steamauto/Steamauto>

定位：活跃的多平台自动收发货工具，支持 Buff、悠悠有品、ECO、C5和 Steam，本地加速与通知也比较完善。

优点：维护活跃、平台覆盖广、适合已有库存和订单后的自动收发货。

缺点：它不负责寻找倒余额机会；配置项、Cookie、令牌更多，自动化权限也更高。

建议：暂时不与AetherSwap同时运行。同一 Steam账号同时运行两套自动收货、报价确认程序，容易重复操作和增加风控。

### 4. woctezuma/steam-market

地址：<https://github.com/woctezuma/steam-market>

定位：Steam市场内部套利，主要围绕卡牌、宝石、补充包和徽章，不是 Buff到 Steam的倒余额路径。

优点：研究价值高，有数据分析和多种套利思路。

缺点：操作对象、利润模型与当前饰品挂刀完全不同，而且仍然依赖 Steam Cookie与市场请求。

建议：适合研究和模拟，不适合作为当前AetherSwap的直接替代品。

### 5. TradeLock

地址：<https://github.com/cth-latest/trade-lock>

定位：根据 tradeupspy数据放置 Steam求购单、管理库存，并提供模拟模式。

优点：有模拟模式，适合了解求购单和汰换合同策略。

缺点：项目体量和提交数较小，配置示例仍带有明显模板痕迹；不是跨平台倒余额工具。

建议：只做代码阅读和模拟，不建议当前投入真实资金。

## 十二、同类项目最终判断

当前没有发现一个“维护活跃、Windows小白可直接使用、同时完成 Buff选品购买、Steam出售、自动确认、网络分流和风控”的开源项目，能明显优于已经本地改造的 AetherSwap。

更现实的组合是：

```text
SteamDT / SteamTradingSiteTracker：提供候选与行情
旧油猴倒余额脚本：人工交叉验证比例
AetherSwap：小额、低频执行
Steamauto：未来只在确有自动收发货需求时单独评估
```

不要把四套工具同时连接同一个账号。

## 十三、开始任务前的最终检查清单

- [ ] Clash正在运行。
- [ ] Clash使用规则模式。
- [ ] 使用固定稳定香港节点。
- [ ] Clash混合代理端口是7890。
- [ ] AetherSwap代理池启用、策略2。
- [ ] UU在首次排查时关闭。
- [ ] Steam++关闭。
- [ ] 没有额外美国VPN。
- [ ] `account_region`显示同步完成。
- [ ] Steam钱包币种与实际账号一致。
- [ ] Steam Cookie验证成功。
- [ ] Buff走国内直连。
- [ ] Buff客服已经解除市场限制。
- [ ] Buff App和网页能打开饰品市场。
- [ ] 没有未支付订单或等待处理的报价。
- [ ] 初次恢复只使用很小金额。

只要其中任何一项不确定，就先不要启动自动交易。