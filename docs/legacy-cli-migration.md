# CLI 迁移说明

## 当前入口

`garden` 是正式的用户级命令入口，`gardenctl` 是同一应用的兼容别名。所有既有命令组保持不变。

单 URL 被动扫描的推荐写法是：

```bash
garden scan http://127.0.0.1:8080/
```

旧写法继续兼容：

```bash
gardenctl scan --url http://127.0.0.1:8080/
```

位置 URL 与 `--url` 不能同时使用。默认扫描在前台显示阶段和进度；`--detach` 只提交后台任务。CLI 会自动启动或复用本机 Web UI，并输出可访问的详情地址。`Ctrl+C` 会中断本次扫描；`garden stop` 会中断全部活动扫描并停止本机 UI。

## 与旧行为的差异

`scan` 不再在 CLI 进程内承担业务编排或等待线程池，而是通过本机 Web UI 的 API 创建持久化 `ScanRun` 并读取状态。这使 Web、HTTP API 与 CLI 共享同一个任务、报告和取消状态。已中断扫描保留已写入的资产、证据和失败记录，不生成伪造完整报告。

## 高级认证工作流

target、credential、login、session、inventory、checks、findings、evidence、retest 和历史 job/report 命令均继续存在。它们用于需要登录态、人工 triage 或 retest 的显式高级流程，不是 `garden scan` 的前置步骤。
