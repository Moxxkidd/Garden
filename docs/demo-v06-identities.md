# v0.6 独立身份采集：真实本地演示

本记录来自 2026-10-06 本地 FastAPI 夹具与真实 Chromium 执行结果，未访问外部业务目标。测试账号与验证码仅供夹具使用。

## 实际操作与结果

1. 打开 Web「身份与采集」，为已有身份填写 `/login`、验证地址 `/me` 和成功元素 `#identity`。
2. 打开人工登录窗口，输入夹具账号 `reader` 与验证码 `123456`。这是测试中模拟人的输入，产品不会识别或绕过验证码。
3. 点击「已完成登录，验证并保存」。原浏览器关闭，再以保存状态开启新浏览器验证；状态变为 `ready`，此时扫描任务仍为零。
4. 返回身份页，选择该身份，入口填写 `/me`，预览后点击「确认开始采集」。测试禁用 JavaScript，使用原生表单和重定向完成操作。
5. 任务状态为 `completed`；统一清单归并为 **2 类资产主体**：`GET /me` 与 `GET /asset`。两者在该身份下均为「已观察」，HTTP 200；认证诊断数为 0。主体仍不等同于业务有效性结论；本模式跳过风险分析，摘要明确标注。
6. 桌面 1280×900 与手机 390×844 页面均被真实浏览器检查；手机表单无横向溢出。

## 独立边界验证

- 同角色两个账号在独立浏览器上下文中分别采集，Cookie 与资产归属不混用。矩阵按 `profile:<id>` 区分，同名显示标签不会合并身份；一个主体的 200／403 观察分别保留。
- 独立健康页面验证成功的普通 403 保持 `ready`。真实夹具在 `item/21` 撤销状态后，采集停止：早先已确认批次保留，当前未确认批次为身份不确定，`item/29` 尚未采集。
- 恢复认证后，检查点创建关联补采任务，取得 `item/29`，不重采此前确认的 `item/0`；原 Markdown 报告字节完全不变。
- Cookie、localStorage 与 sessionStorage 的多批次失效／补采和页面主动清除状态分别通过真实浏览器回归；验证使用当前浏览器状态，不重新加载旧快照来证明身份。
- 健康验证也计入总请求预算；预算不足以验证时不确认当前批次。补采只允许已知范围中的 GET/HEAD，保留 HEAD 方法。
- API、CLI、Web、JSON、CSV 与 Markdown 共用矩阵投影。公开输出对 URL 查询值脱敏；无效或过大导入不会回显状态原文。

## 使用限制

人工浏览器运行在 Garden 所在主机，不是远程桌面。状态导入最多 1 MiB，支持同源 Cookie、localStorage 与 sessionStorage；不保证 IndexedDB、设备绑定状态或任意 SSO 状态可恢复。必须配置正向成功元素或文本。

CLI 的人工登录生命周期连接已有 Web 服务：`garden identities login ... --api-url URL`，随后 `status ATTEMPT_ID`、`confirm ATTEMPT_ID` 或 `cancel ATTEMPT_ID` 使用相同地址。导入、验证、撤销、采集和补采共用服务层。

检查点限定为已知未完成及身份不确定请求，最多 2000 条、1 MiB URL 材料；截断会显示，不宣称发现全部站点内容。原任务和补采任务保持独立，不做跨任务资产库合并。升级使用迁移 `0008`；存在新增身份数据时，降级会拒绝丢弃数据。

## 复现入口

```bash
PYTHONPATH=. python -m pytest tests/test_identity_journey_e2e.py -q
PYTHONPATH=. python -m pytest tests/test_identity_collection.py tests/test_identity_recovery.py -q
PYTHONPATH=. python -m pytest tests/test_identity_matrix.py tests/test_identity_adapters.py -q
```

需预先安装依赖与 Chromium（`python -m playwright install chromium`）。浏览器测试按序运行，避免相互争抢资源。
