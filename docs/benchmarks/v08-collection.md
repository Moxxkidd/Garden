# v0.8 持久采集规模实验

这些脚本只使用合成数据和本机 loopback，不接受外部目标。产品版本尚未核销全部 v0.8 验收项，数字不构成性能承诺。

## 复现

在开发环境从仓库根目录执行，浏览器先按项目说明安装 Chromium。真实浏览器实验和浏览器测试串行运行。

```bash
PYTHONPATH=. python scripts/benchmark_collection_queue.py
PYTHONPATH=. python scripts/benchmark_collection_transport.py --transport http --output .verification/http-scale.json
PYTHONPATH=. python scripts/benchmark_collection_transport.py --transport browser --output .verification/browser-scale.json
```

队列脚本建立独立临时数据库，插入并重复入队 10,000 条工作，用新进程读取 50 页并校验唯一 ID、顺序、原始请求样本及上限。没有目标请求。结果写入 `.verification/queue-scale-result.json`。

传输脚本默认 20 轮，每轮 500 个不同 URL，保留每轮既有 max_pages=500、max_browser_requests=2000、max_candidates=10000 上限。可用 `--rounds 1 --per-round 100` 预跑。每轮断言实际服务器请求数、持久请求数、观察数一致且队列覆盖完整；不将任务生命周期警告等同于队列缺口。显式发送间隔为 0，重试为 0；这是本机能力基线，不是推荐面向真实站点的配置。

结果记录平台、Python、产品源码摘要、基准提交、各轮队列指标、总吞吐、数据库大小和父 Python 进程峰值 RSS。RSS **不包含 Chromium 子进程**；目前不单独测量活动/等待/冷却时间，因此不能作为完整资源或效率指标验收。临时目录路径写入结果供复查，脚本不自动删除现场。输出和运行数据不入库。

## 已观察结果（2026-10-16）

macOS 26.3 arm64、Python 3.10.10。队列/HTTP 使用产品提交 `1984ac2`，当时的忽略目录原型脚本；纳入仓库后的脚本增加可配置输出、平台和源码摘要记录，后续结果单独记载。

| 实验 | 实际结果 |
|---|---|
| 队列 10,000 条 | 插入 21.860s；重复去重 3.288s；新进程分页校验 0.855s；0 次发送；10,000 条 pending，覆盖未完成 |
| HTTP 20 × 500 | 10,000 个不同 URL/10,000 次实际请求/10,000 条 observed；826.957s；12.093 请求/秒；20 轮队列覆盖完整 |
| 浏览器 1 × 100 预跑 | 并发归属修复后的代码；100 次实际请求/100 条 observed；8.823s；11.333 请求/秒；队列覆盖完整，任务带页面上限警告 |

队列数据库 6,963,200 字节、Python RSS 96,501,760 字节；HTTP 数据库 43,614,208 字节、Python RSS 140,787,712 字节；浏览器预跑数据库 1,392,640 字节、Python RSS 117,620,736 字节。浏览器万级实验另记，预跑不能代替它。

浏览器万级正式实验：从 `8842795` 的 git archive 独立快照执行默认 20 × 500。结果文件 browser-scale-10000-result.json，日志 browser-scale-10000.log；产品源码摘要 `6e462a4a75072b23f1e55bec5f51f9e6dcdbfa2b17e3c452aeb458c5f091ee68`。快照没有 .git，因此脚本 base_commit=null，上述提交来自创建快照的实际 git archive 命令。20 轮每轮 500 次实际请求/500 条 observed/队列覆盖完整；每轮 completed_with_warnings（页面上限）。总计 10,000 个不同 URL、10,000 次实际请求，1951.306s，5.125 请求/秒；数据库 43,372,544 字节，父 Python 峰值 RSS 148,013,056 字节。实验期间没有其他浏览器运行；同时有不启动浏览器的小型回归，不能描述为整机独占基准。

此基线早于 0011 计时/观察效率指标和身份材料计费修改。等待计时仍标为未单独测量；后续小样本或全量结果不得与本基线混写，也不能用父进程 RSS 代表整个 Chromium 进程树。

## 当前指标版本的队列复核

在 `8842795` 后的本地 0011/指标/身份预算实现上，正式队列脚本完成 10,000 条新建与重复入队：24.811s / 7.951s，新进程读回 0.919s，0 次请求；数据库 6,963,200 字节、私有文件 10,001、父 Python RSS 92,815,360 字节。macOS 临时路径先解析为实际目录，避免 /var 符号链接触发产品的路径保护；保护规则未放宽。

## 真实进程中断实验

```bash
PYTHONPATH=. python scripts/experiment_collection_crash.py
```

此脚本只访问合成本机 HTTP 服务，主动 SIGKILL 自己启动的三个扫描子进程，并等待约 120 秒的真实租约失效。分别覆盖等待响应、证据插入尚未提交、观察事务已提交三个窗口；立即重启和租约到期后重启均断言不发送请求。前两种只能经显式预览/确认生成新任务补采，第三种保留已提交观察。SQL 事件用于定位故障注入时刻，SQL 读回用于检查实际磁盘事务边界，不替代公开服务的状态/恢复断言。临时现场保留，结果写入 `.verification/full-pipeline-crash-result.json`；脚本不执行外部目标扫描。
