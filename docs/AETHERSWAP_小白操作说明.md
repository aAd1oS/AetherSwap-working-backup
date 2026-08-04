# AetherSwap 小白操作说明

适用目录：`D:\ai thinking\steam`

本说明基于当前这台电脑已经复现好的状态编写，并参考了项目原始 README：

- GitHub 项目：`https://github.com/VexedWilosn/AetherSwap`
- 本地项目目录：`D:\ai thinking\steam`
- 本地访问地址：`http://127.0.0.1:28472`

> 重要提醒：AetherSwap 涉及 Steam、Buff、Cookie、移动令牌密钥、代理和自动化交易。请只用于学习、研究和自担风险的个人测试。任何账号封禁、资产冻结、交易亏损、隐私泄露等后果，都需要使用者自己承担。

---

## 1. 现在已经完成了什么

当前这台电脑上已经做完这些事：

1. 已克隆项目代码到：

   ```text
   D:\ai thinking\steam
   ```

2. 已创建 Python 虚拟环境：

   ```text
   D:\ai thinking\steam\.venv
   ```

3. 已安装项目依赖：

   ```text
   requirements.txt
   ```

4. 已安装 Playwright 浏览器文件：

   ```text
   D:\ai thinking\steam\.playwright
   ```

   你已经确认能看到这些内容：

   ```text
   chromium-1228
   chromium_headless_shell-1228
   ffmpeg-1011
   winldd-1007
   ```

5. 已新增一键启动脚本：

   ```text
   D:\ai thinking\steam\start-aetherswap-server.cmd
   ```

6. 已验证本地服务可以访问：

   ```text
   http://127.0.0.1:28472
   ```

看到 AetherSwap 仪表盘页面，就说明本地复现成功。

---

## 2. 每次使用怎么启动

### 方法 A：最简单，双击启动

打开文件夹：

```text
D:\ai thinking\steam
```

双击：

```text
start-aetherswap-server.cmd
```

会出现一个命令行窗口。这个窗口就是后端服务，不要关。

然后打开浏览器访问：

```text
http://127.0.0.1:28472
```

看到 AetherSwap 页面，说明启动成功。

### 方法 B：用 PowerShell 启动

打开 PowerShell，执行：

```powershell
cd "D:\ai thinking\steam"
.\start-aetherswap-server.cmd
```

然后访问：

```text
http://127.0.0.1:28472
```

### 怎么判断启动成功

成功时通常会有这些表现：

- 浏览器能打开 `http://127.0.0.1:28472`
- 页面标题是 `AetherSwap`
- 左侧能看到仪表盘、库存管理、账号管理、Steam 令牌、代理池、策略中心、系统设置等菜单
- 首页会提示你完成基础配置

如果浏览器打不开，先看第 9 节“常见问题”。

---

## 3. 每次使用怎么停止

如果你是双击 `start-aetherswap-server.cmd` 启动的：

1. 找到那个命令行窗口。
2. 直接关闭窗口。

或者：

1. 在 AetherSwap 页面里找“退出程序”。
2. 点击退出。

如果关了浏览器但没关命令行窗口，服务还在运行。

---

## 4. 第一次打开页面后应该做什么

第一次进入页面，会看到快速引导。建议按这个顺序做：

1. 先确认你只是测试或学习，不要直接上大额账号。
2. 打开 Steam 加速器或能稳定访问 Steam 社区的网络。
3. 进入【系统设置】。
4. 填写 Steam 令牌相关密钥。
5. 配置通知方式，可选。
6. 进入【代理池】，按需要配置代理。
7. 进入【账号管理】，添加 Steam / Buff 相关账号信息。
8. 回到首页，先观察状态和日志。
9. 不要急着启动自动任务，先确认配置和风险。

建议先用小号、小金额、小范围测试。

---

## 5. 页面里各菜单大概是做什么的

### 仪表盘

主页。看运行状态、总出售金额、营收、收益、折扣比率、任务状态等。

你可以理解为“总控台”。

### 库存管理 / 持有饰品

查看当前检测到的库存饰品、持有状态、价格状态等。

如果 Steam 登录状态不正常，这里可能没有数据。

### 操作记录 / 上架记录

查看程序做过什么，例如购买、上架、出售、失败原因等。

出问题时优先看这里和运行日志。

### 数据分析

查看历史交易、收益、折扣、盈亏等数据。

这部分需要有实际记录后才有意义。

### 账号管理

管理 Steam / Buff 登录状态。

常见操作：

- 添加账号
- 验证账号
- 重新登录
- 手动填写 Cookie

如果自动登录失败，可以先检查网络，再考虑手动 Cookie。

### Steam 令牌

管理 Steam Guard 相关能力。

可能涉及：

- `shared_secret`
- `identity_secret`
- 两步验证码
- 上架确认签名

这些是高敏感信息，不要发给别人，不要截图外传。

### 代理池

配置访问 Steam / Buff 时使用的代理。

为什么需要代理：

- Steam 社区在国内可能访问不稳定
- 同一个 IP 高频访问容易触发风控
- 某些登录或行情接口需要稳定网络

小白建议先用稳定的 Steam 加速器，不要一开始折腾复杂代理池。

### 策略中心

决定程序怎么买、卖、过滤、定价。

它不是“越激进越赚钱”。默认策略偏保守，是为了少踩坑。

建议：

1. 先用默认策略。
2. 想改参数时，先复制策略。
3. 用“模拟运行”看结果。
4. 小范围测试后再扩大。

### 系统设置

配置全局参数，例如：

- Steam 令牌密钥
- 通知 Token
- 邮箱 IMAP
- 代理设置
- 刷新周期
- 风控参数

系统设置里的改动通常会立即生效。

### 运行日志

程序的实时日志。

如果遇到问题，先看这里。

### Steam 折扣 / Steam 赠礼

项目内置的 Steam 折扣和赠礼相关功能。

如果你只是先复现项目，不必一开始就配置这部分。

---

## 6. 关键配置应该怎么理解

### Steam 令牌密钥

常见字段：

```text
shared_secret
identity_secret
```

大概作用：

- `shared_secret`：用于生成 Steam 两步验证码。
- `identity_secret`：用于确认交易、确认上架等操作。

风险：

- 泄露后别人可能操作你的账号相关确认。
- 不要发给别人。
- 不要放到公开仓库。
- 不要截图到群里。

### Buff / Steam Cookie

Cookie 可以理解成“登录凭证”。

风险：

- Cookie 泄露后，别人可能冒用登录状态。
- Cookie 过期后，程序会登录失败或抓不到数据。

如果 Cookie 过期：

1. 进入【账号管理】。
2. 找到对应账号。
3. 点重新登录。
4. 如果自动登录失败，就检查网络或手动填 Cookie。

### 邮箱 IMAP

项目 README 里说明了付款后的确认流程：

- 配置了邮箱：程序可以监听付款通知邮件，自动进入后续流程。
- 没配置邮箱：程序会降级为手动确认，需要你在页面上点“已支付”。

小白建议：

- 初次测试可以先不配邮箱。
- 先用手动确认，弄懂流程。
- 稳定后再考虑配置专用邮箱。

### 代理

代理不是越多越好。

建议：

- 先保证 Steam 社区能稳定访问。
- 不懂代理池时，不要随便导入一大堆未知代理。
- 未知代理可能泄露 Cookie 或登录信息。

---

## 7. 策略中心小白用法

策略中心分三块：

1. 购入策略
2. 出售策略
3. 模块管理

### 购入策略

决定“什么东西可以买”。

常见判断：

- 折扣够不够
- 历史价格稳不稳
- 成交量够不够
- 是否超过每日预算
- Buff 和 Steam 价格是否合理

### 出售策略

决定“库存怎么卖”。

常见判断：

- 什么价格上架
- 是否等待趋势
- 是否保护利润
- 同名商品挂多少个
- 是否自动上架

### 模拟运行

小白一定要先用“模拟运行”。

模拟运行的意思：

- 只判断，不真的买
- 只演练，不真的上架
- 能看到每一步为什么通过或拒绝

### 不建议一开始做的事

不要一上来就：

- 大幅调高购买频率
- 放宽所有风控
- 删除默认保护模块
- 用大号大额余额测试
- 开大量代理高频请求

---

## 8. 从零重新安装的完整流程

如果以后你换电脑，或者这个目录删了，可以按这里重来。

### 第 1 步：准备软件

需要：

- Windows 10/11
- Python 3.10 或更高
- Git
- 能访问 GitHub / Gitee / Python 包源的网络
- Steam 加速器或稳定代理

检查 Python：

```powershell
python --version
```

检查 Git：

```powershell
git --version
```

### 第 2 步：克隆项目

进入你想放项目的目录，例如：

```powershell
cd "D:\ai thinking"
```

从 GitHub 克隆：

```powershell
git clone https://github.com/VexedWilosn/AetherSwap.git steam
cd steam
```

如果 GitHub 因 Schannel 报错，可以用：

```powershell
git -c http.sslBackend=openssl clone https://github.com/VexedWilosn/AetherSwap.git steam
```

如果你从 Gitee 拉：

```powershell
git clone https://gitee.com/vexed-wilson/AetherSwap.git steam
cd steam
```

### 第 3 步：创建虚拟环境

```powershell
python -m venv .venv
```

激活虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

看到命令行前面有：

```text
(.venv)
```

就说明激活成功。

如果 PowerShell 不让激活，看第 9 节。

### 第 4 步：安装依赖

```powershell
python -m pip install --upgrade pip
python -m pip install --no-cache-dir -r requirements.txt
```

如果网络慢，可以换国内源：

```powershell
python -m pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 第 5 步：安装 Playwright 浏览器

建议把浏览器安装到项目目录，方便迁移：

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "D:\ai thinking\steam\.playwright"
python -m playwright install chromium
```

检查是否安装成功：

```powershell
python -m playwright install --list
```

看到类似这些就对：

```text
chromium-1228
chromium_headless_shell-1228
ffmpeg-1011
winldd-1007
```

### 第 6 步：确认免责声明

本地当前已经创建过：

```text
.agreed_disclaimer
```

如果从零开始，可以用环境变量跳过非交互确认：

```powershell
$env:AETHERSWAP_AGREE_DISCLAIMER = "1"
```

### 第 7 步：启动

推荐用 server 模式，稳定简单：

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "D:\ai thinking\steam\.playwright"
$env:AETHERSWAP_MODE = "server"
$env:AETHERSWAP_HOST = "127.0.0.1"
$env:AETHERSWAP_PORT = "28472"
$env:AETHERSWAP_AGREE_DISCLAIMER = "1"
$env:AETHERSWAP_OPEN_BROWSER = "0"
python -m uvicorn app.api:app --host 127.0.0.1 --port 28472 --log-level warning
```

打开：

```text
http://127.0.0.1:28472
```

---

## 9. 常见问题和处理办法

### 问题 1：浏览器打不开 `127.0.0.1:28472`

可能原因：

- 服务没启动
- 启动窗口被你关了
- 端口被占用
- 程序启动时报错退出

处理：

1. 重新双击 `start-aetherswap-server.cmd`。
2. 不要关闭弹出的命令行窗口。
3. 再打开 `http://127.0.0.1:28472`。

检查端口：

```powershell
Get-NetTCPConnection -LocalPort 28472 -ErrorAction SilentlyContinue
```

有输出，一般说明端口有人在监听。

没有输出，说明服务没跑起来。

### 问题 2：端口 28472 被占用

换一个端口，例如 28473：

```powershell
$env:AETHERSWAP_PORT = "28473"
python -m uvicorn app.api:app --host 127.0.0.1 --port 28473 --log-level warning
```

然后访问：

```text
http://127.0.0.1:28473
```

### 问题 3：PowerShell 激活虚拟环境失败

如果执行：

```powershell
.\.venv\Scripts\Activate.ps1
```

提示脚本执行被禁止，可以临时允许当前窗口：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

这个设置只对当前 PowerShell 窗口有效。

### 问题 4：pip 安装依赖失败

先确认虚拟环境已经激活：

```text
(.venv)
```

然后重试：

```powershell
python -m pip install --no-cache-dir -r requirements.txt
```

如果下载慢：

```powershell
python -m pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

如果提示 `typing_extensions` 相关问题：

```powershell
python -m pip install --upgrade typing_extensions
python -m pip install --no-cache-dir -r requirements.txt
```

### 问题 5：Playwright 浏览器下载慢或卡住

现象：

```text
python -m playwright install chromium
```

一直卡在某个百分比。

处理：

- 开 VPN / 代理 / 加速器。
- 换网络，例如手机热点。
- 过一会儿重试。
- 确认设置了项目目录：

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "D:\ai thinking\steam\.playwright"
python -m playwright install chromium
```

检查：

```powershell
python -m playwright install --list
```

### 问题 6：GitHub 克隆报 Schannel 错误

错误类似：

```text
schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS
```

临时解决：

```powershell
git -c http.sslBackend=openssl clone https://github.com/VexedWilosn/AetherSwap.git steam
```

如果已经克隆过，只是后续拉取失败：

```powershell
git -c http.sslBackend=openssl fetch
```

### 问题 7：启动时报 `cannot import name 'Sentinel'`

这是 `typing_extensions` 太旧。

处理：

```powershell
python -m pip install --upgrade typing_extensions
python -m pip install --no-cache-dir -r requirements.txt
```

### 问题 8：Steam 登录失败

优先检查：

1. Steam 社区能不能在普通浏览器打开。
2. Steam 加速器有没有开。
3. 代理设置是否错误。
4. 账号是否需要验证码、人机验证、邮箱确认。
5. Cookie 是否过期。

处理：

- 先在普通浏览器确认 Steam 能正常登录。
- 回到 AetherSwap 的【账号管理】重新登录。
- 如果自动登录一直失败，尝试手动 Cookie。

### 问题 9：Buff Cookie 过期

表现：

- Buff 数据拉不到
- 账号状态异常
- 下单失败

处理：

1. 进入【账号管理】。
2. 找到 Buff 相关账号。
3. 点击重新登录。
4. 必要时手动填写新的 Cookie。

### 问题 10：历史价格或行情数据获取不到

可能原因：

- Steam 没登录
- 网络访问 Steam 社区失败
- 代理不可用
- 请求太频繁被限制

处理：

- 开 Steam 加速器。
- 确认 Steam 账号登录有效。
- 降低操作频率。
- 换稳定代理。

### 问题 11：系统频繁提示因波动率、趋势、斜率放弃购买

这是默认策略的保守风控，不是程序坏了。

如果你想买得更频繁，可以在策略中心复制默认策略后调整参数。

但是要明白：

- 放宽风控会增加买入数量。
- 也会增加亏损、滞销、价格波动风险。

小白不建议一开始就大幅放宽。

### 问题 12：页面能打开，但数据都是 0

可能原因：

- 刚启动，还没有交易记录。
- 没配置账号。
- 没启动任务。
- 没有库存或交易数据。

这是正常情况。

先完成账号和基础配置。

### 问题 13：命令行窗口里没有输出

当前启动脚本使用：

```text
--log-level warning
```

只有警告或错误时才会输出较多内容。

页面能打开就不用管。

如果想看更详细输出，可以手动启动：

```powershell
python -m uvicorn app.api:app --host 127.0.0.1 --port 28472 --log-level info
```

---

## 10. 安全注意事项

### 不要泄露这些东西

不要给别人看：

- Steam 账号密码
- Steam Cookie
- Buff Cookie
- `shared_secret`
- `identity_secret`
- 邮箱授权码
- 代理账号密码
- 完整配置文件
- 数据库文件

### 不要直接公开管理面板

本地访问地址：

```text
http://127.0.0.1:28472
```

只给本机用。

如果部署到服务器，不要直接把 `28472` 暴露到公网。

至少要做：

- 防火墙限制 IP
- Nginx 反向代理
- 访问鉴权
- HTTPS

### 不要一开始就实盘大额运行

建议顺序：

1. 先只启动页面。
2. 再填基础配置。
3. 再登录账号。
4. 再模拟策略。
5. 再小额测试。
6. 稳定后再考虑扩大。

---

## 11. 数据和文件说明

常见目录：

```text
app/          后端核心
web/          前端页面
config/       配置和本地数据
log/          日志
tests/        测试
.venv/        Python 虚拟环境
.playwright/  Playwright 浏览器
docs/         本地说明文档
```

重要文件：

```text
run.py
requirements.txt
requirements-server.txt
start-aetherswap-server.cmd
docs/AETHERSWAP_LOCAL_REPRO_NOTES.md
docs/AETHERSWAP_小白操作说明.md
docs/AETHERSWAP_初次交易建议步骤.md
```

备份时建议至少备份：

```text
config/
log/
start-aetherswap-server.cmd
docs/
```

如果 `config/` 里有敏感信息，备份文件也要保管好。

---

## 12. 可选：运行测试

本地复现页面不要求跑测试。

如果你想检查代码层面，可以安装 pytest：

```powershell
cd "D:\ai thinking\steam"
.\.venv\Scripts\Activate.ps1
python -m pip install pytest
python -m pytest -q
```

也可以只跑某个测试：

```powershell
python -m pytest tests\test_runtime_env.py -q
```

前端 JS 语法检查需要 Node.js：

```powershell
node --check web/js/strategies.js web/js/main.js web/js/utils.js
```

---

## 13. 可选：Docker 部署

当前这台电脑没有检测到 Docker，所以本次没有走 Docker。

如果你以后在服务器上部署，可以参考：

```bash
cp .env.example .env
mkdir -p config log
docker compose up -d --build
docker compose logs -f
```

访问：

```text
http://服务器IP:28472
```

强烈建议不要裸奔暴露到公网。

---

## 14. 最短操作清单

如果你只是想用当前已经复现好的项目：

1. 打开：

   ```text
   D:\ai thinking\steam
   ```

2. 双击：

   ```text
   start-aetherswap-server.cmd
   ```

3. 打开浏览器：

   ```text
   http://127.0.0.1:28472
   ```

4. 按页面引导完成配置。

5. 先模拟、再小额测试。

6. 用完后关闭服务窗口。

---

## 15. 你现在可以继续做什么

推荐下一步：

1. 先打开页面熟悉每个菜单。
2. 看【系统设置】里有哪些字段。
3. 看【策略中心】默认策略，不要先改。
4. 配置 Steam 加速器。
5. 准备一个测试账号。
6. 小额验证登录、Cookie、库存读取。

不推荐下一步：

- 直接开自动任务。
- 直接上主号。
- 直接大额购买。
- 直接改激进策略。
- 使用来路不明的代理。

---

## 16. 一句话版

这台电脑上 AetherSwap 已经复现好了。平时双击 `start-aetherswap-server.cmd`，浏览器打开 `http://127.0.0.1:28472`，按页面引导配置；遇到问题先看本说明第 9 节。
