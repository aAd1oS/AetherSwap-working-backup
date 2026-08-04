# AetherSwap 文档目录

这里放本地复现和使用说明。项目源码结构没有移动，避免破坏启动。

## 推荐阅读顺序

1. `AETHERSWAP_小白操作说明.md`

   从启动、停止、配置、常见问题讲起，适合第一次打开项目时看。

2. `AETHERSWAP_初次交易建议步骤.md`

   讲第一次绑定账号、第一次小额交易、第一次上架时怎么稳一点。

3. `AETHERSWAP_LOCAL_REPRO_NOTES.md`

   记录本机复现过程、已处理的问题和当前状态。

## 根目录保留的文件

这些文件继续留在项目根目录，不建议移动：

- `run.py`
- `requirements.txt`
- `requirements-server.txt`
- `Dockerfile`
- `docker-compose.yml`
- `.env.example`
- `README.md`
- `start-aetherswap-server.cmd`

其中 `start-aetherswap-server.cmd` 留在根目录，是为了双击启动方便。
