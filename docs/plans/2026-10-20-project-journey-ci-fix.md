# 合并后项目旅程 CI 时序修正

基线 main `2d9b30f69ed2bacb6b43ab225629e93ea73735bd`。PR #40 四项检查通过；合并后的 run 39417041422 只有 Python 3.12 失败（922 passed / 1 failed）。Python 3.10、CLI smoke 和 Docker build 通过，非 GitHub 排队事故，也非身份最终确认回归。

失败节点：`tests/test_project_journey_e2e.py::test_two_origins_project_scan_history_and_download_without_js`。页面快照仅有第一站点的两个主体，断言期待两个站点共四个主体。

`wait_for_completion` 等待源扫描终态；项目索引在 `execute_scan` 的 finally 中独立执行。两者按现有契约可分开完成。测试在扫描终态后立即加载无 JavaScript 资产页，之后页面不会自动刷新，即使后台索引完成，Playwright 等待元素仍不能得到新数据。

复现与修复：

- 原用例本地单跑通过：1 passed / 13.64s。
- 在 `ProjectLifecycleService.retry` 前引入 1 秒合成延迟，同一命令稳定触发原“四个主体”断言失败（`.verification/journey-red.log`）。没有改产品代码。
- 保留该延迟以覆盖此时间窗口；加载资产页面前，通过公开项目详情 API 有界等待两个成员均为 complete。failed 立即失败，超时输出成员状态。没有提高元素断言超时、降低资产数量或跳过用例。
- 修正后执行 `PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python -m pytest tests/test_project_journey_e2e.py tests/test_project_lifecycle.py -q -o addopts='' --tb=short`：12 passed / 13.81s，保留两条既有 websockets 弃用告警。环境 macOS / Python 3.10.10 / Chromium。
- Ruff check、format check（373 文件）与 git diff --check 通过。本次仅修改测试和记录，不重跑本地完整回归、不重建产品包；Linux 双版本完整回归由修复 PR CI 执行。

防止再次发生：无 JS 旅程需要先等待实际读取的异步投影状态，再导航。源扫描结束不等于独立项目索引结束；不要通过修改产品终态语义掩盖测试时序。
