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
