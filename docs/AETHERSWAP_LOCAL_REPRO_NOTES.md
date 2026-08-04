# AetherSwap 本地复现记录

记录时间：2026-07-10

工作目录：`D:\ai thinking\steam`

## 当前状态

- 仓库已克隆到本地。
- Python 虚拟环境已创建：`.venv`
- `requirements.txt` 依赖已安装。
- Playwright 浏览器已安装到项目目录：
  - `.playwright\chromium-1228`
  - `.playwright\chromium_headless_shell-1228`
  - `.playwright\ffmpeg-1011`
  - `.playwright\winldd-1007`
- 已创建 `.agreed_disclaimer`，启动时不会再卡首次免责声明确认。
- 已新增一键启动脚本：`start-aetherswap-server.cmd`
- 服务已验证可启动，地址：

```text
http://127.0.0.1:28472
```

验证结果：

- `/api/runtime` 返回 `ok: True`
- 首页返回 HTTP `200`
- 浏览器实际渲染到 `AetherSwap` 仪表盘页面

## 启动方式

最简单：双击或运行：

```powershell
.\start-aetherswap-server.cmd
```

或者手动运行：

```powershell
cd "D:\ai thinking\steam"
.\.venv\Scripts\Activate.ps1
$env:PLAYWRIGHT_BROWSERS_PATH = "D:\ai thinking\steam\.playwright"
$env:AETHERSWAP_MODE = "server"
$env:AETHERSWAP_HOST = "127.0.0.1"
$env:AETHERSWAP_PORT = "28472"
$env:AETHERSWAP_AGREE_DISCLAIMER = "1"
$env:AETHERSWAP_OPEN_BROWSER = "0"
python -m uvicorn app.api:app --host 127.0.0.1 --port 28472 --log-level warning
```

浏览器访问：

```text
http://127.0.0.1:28472
```

## 已遇到并处理的问题

1. GitHub 克隆最初失败

   Windows Schannel 报：

   ```text
   AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS
   ```

   临时使用 OpenSSL 后端解决：

   ```powershell
   git -c http.sslBackend=openssl clone https://github.com/VexedWilosn/AetherSwap.git .
   ```

2. pip 缓存目录权限问题

   第一次安装依赖时写用户缓存失败，改用：

   ```powershell
   python -m pip install --no-cache-dir -r requirements.txt
   ```

3. Playwright Chromium 下载慢

   后来你手动下载完成，当前已正常识别。

## 可选下一步

- 在 Web 页面里填写 Steam/Buff/代理等配置。
- 若要跑测试，先装测试工具：

```powershell
python -m pip install pytest
python -m pytest -q
```

## 停止服务

如果是双击脚本启动的，关闭那个 AetherSwap Server 命令行窗口即可。

也可以在页面里点退出程序。
