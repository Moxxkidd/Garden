# Garden 桌面 App Beta

## macOS App

本轮本地 Beta 支持 Apple Silicon / macOS ≥ 26.0（实测 26.3）。捆绑 Python/OpenSSL 的 Mach-O 最低系统声明是 26.0，因此未承诺旧版 macOS。App 内含固定 Python 3.12 和匹配的 Playwright Chromium，用户运行时不需要 Python、Node、Rust 或源码。扫描、身份采集和资产展示使用原有 Python 引擎与 WebUI；Rust 只处理本地窗口、菜单、文件保存和更新。

## 使用与数据

首次打开选择新建或已有 Garden 数据目录，先查看目录/代理检查。新建数据库和旧库备份升级分别需要明确确认。数据不放进 App bundle；默认目录约定仍是 `~/.garden`。本地示例不发送外部请求，真实目标仍须先授权。

相同数据目录的 CLI、Web、App 复用带实例会话的引擎。版本不一致或握手失败时先用原版本停止服务，不接管未知端口。`garden ui` 打开本地浏览器会话，不在终端输出会话令牌。也可使用包内 CLI：

```bash
GARDEN_HOME=/your/garden-data /Applications/Garden.app/Contents/Resources/engine/garden-engine cli --version
GARDEN_HOME=/your/garden-data /Applications/Garden.app/Contents/Resources/engine/garden-engine cli scan http://127.0.0.1:8080/ --detach
```

关闭活动任务窗口可以继续后台运行，或停止新增请求并等待任务收尾。退出 App 不终止 CLI 创建的实例。程序更新与用户数据恢复是两个操作；恢复只写入新的空目录，备份后的修改不会保留，周期复查保持暂停。

## 本地构建

精确版本见 `build-versions.json`、`python-build-requirements.txt`、npm/Cargo lock。需要 Apple Silicon macOS、CLT SDK、固定 Python 3.12.14 和 Node。所有构建依赖放到本 checkout 的隔离目录；脚本不修改全局工具链。

```bash
python3.12 -m venv .verification/venv
.verification/venv/bin/python -m pip install -r desktop/python-build-requirements.txt
.verification/venv/bin/python -m pip install --no-deps -e .
.verification/venv/bin/python desktop/bootstrap_toolchain.py
npm ci --prefix desktop --ignore-scripts --no-audit --no-fund --cache .verification/npm-cache

export PATH="$PWD/.verification/toolchain/bin:$PATH"
export CARGO_HOME="$PWD/.verification/cargo-home"
export CARGO_TARGET_DIR="$PWD/.verification/tauri-target"
export MACOSX_DEPLOYMENT_TARGET=26.0
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.verification/browsers"
.verification/venv/bin/python -m playwright install chromium
export GARDEN_BUILD_BROWSERS="$PLAYWRIGHT_BROWSERS_PATH"
export GARDEN_OPENSSL_LICENSE=/opt/homebrew/opt/openssl@3/LICENSE.txt
.verification/venv/bin/python desktop/build_engine.py
.verification/venv/bin/python desktop/build_notices.py
.verification/venv/bin/python desktop/build_app.py
```

`GARDEN_OPENSSL_LICENSE` 指向构建解释器实际链接的 OpenSSL 许可证文本。构建脚本只使用临时 ad-hoc 签名，不读取正式签名/公证凭据。Tauri 生成 `.app`；`hdiutil` 生成含 App 和 Applications 链接的 `.dmg`，不依赖 Finder 美化脚本。`.verification/package-report.json` 记录环境、包资源体积及 DMG SHA256。包内 `Contents/Resources/notices/` 提供实际依赖清单、Python/Node/Chromium/OpenSSL 和 Rust 许可证文本。

## 用户确认的更新

使用 Tauri 官方 updater。默认不配置正式渠道，不自动检查更新或请求通知权限。用户点击检查、勾选确认安装后才执行更新。先由官方插件校验包签名及签名绑定版本，再通过现有执行器确认空闲、生成私有数据备份和旧程序副本。单独的旧版本恢复入口在 App 退出后启动新程序；新程序完成引擎握手和主页面加载后才确认启动成功。失败则恢复此前程序，保留失败包供诊断，不自动回退数据库。

本地 Beta 更新要求程序安装位置可写，且程序与私有备份在同一文件系统；不做跨卷替换。

正式渠道需另行配置 `plugins.updater.endpoints` 和公钥；保持 `requireSignedVersion: true`，通过官方 `tauri signer sign --app-version` 或正式构建生成签名。私钥不入库。测试渠道、公钥和临时私钥只能放到隔离验证目录；不将合成更新包当作已发布版本。

本地 Beta 验证不等于正式发行。Developer ID、hardened runtime/entitlements、正式更新签名保管、Apple 公证与装订、许可证分发条件及外部干净机器验证需要发布阶段单独完成；不得通过关闭系统安全机制来运行候选包。


## Windows x64 安装包

Windows 桌面版使用用户级 NSIS 安装器 `Garden_2.1.0_x64-setup.exe`。首版目标为 Windows 10 / 11 x64，CI 使用 Windows Server 2022 x64。安装包包括 Python 引擎、Playwright Chromium 与 Microsoft Evergreen WebView2 离线安装器。安装和运行可使用普通用户账户，运行无需 Python、Node、Rust 或 WSL。

1. 打开安装器，选择程序目录。安装结束后，从开始菜单打开 Garden。
2. 首次启动时在「连接数据目录」选择只有自己账户和系统管理员可访问的本地 NTFS 目录，例如 `%USERPROFILE%\.garden`。Windows 使用目录继承的访问控制列表保护数据库、会话与备份。若没有数据库，勾选创建数据库后开始使用。
3. 更新前从 Garden 菜单退出，并停止仍在使用该目录的 CLI 实例。运行新版安装器，再选择原数据目录。若有待执行的数据库迁移，先在设置中确认备份与升级。
4. 在 Windows 设置的「应用」中卸载 Garden。程序目录与项目数据目录应分开，卸载程序后可重新接入原数据目录。

本地候选 EXE 使用无签名构建。Windows 更新通过手动运行新版安装包完成。Windows ARM64、32 位和正式签名渠道尚未纳入此迭代。

### 在 Windows 构建与验收

在原生 Windows x64 上准备 MSVC C++ Build Tools 和 Windows SDK、Node 22、Rustup，以及 `build-versions.json` 的 Windows Python 版本。在仓库根目录打开 PowerShell 7：

```powershell
$PSNativeCommandUseErrorActionPreference = $true
$pins = Get-Content desktop/build-versions.json -Raw | ConvertFrom-Json
$toolchain = "$($pins.rust)-$($pins.windows.target)"
rustup toolchain install $toolchain --profile minimal
rustup override set $toolchain

python -m venv .verification/venv
$python = ".verification/venv/Scripts/python.exe"
& $python -m pip install -r desktop/python-build-requirements.txt
& $python -m pip install --no-deps -e .
npm ci --prefix desktop --ignore-scripts --no-audit --no-fund

$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $PWD .verification/browsers
$env:GARDEN_BUILD_BROWSERS = $env:PLAYWRIGHT_BROWSERS_PATH
$env:GARDEN_OPENSSL_LICENSE = Join-Path $PWD .verification/OpenSSL-LICENSE.txt
$openssl = & $python -c "import ssl; print(ssl.OPENSSL_VERSION.split()[1])"
Invoke-WebRequest "https://raw.githubusercontent.com/openssl/openssl/openssl-$openssl/LICENSE.txt" -OutFile $env:GARDEN_OPENSSL_LICENSE
& $python -m playwright install chromium
& $python desktop/build_engine.py
& $python desktop/build_notices.py
& $python desktop/build_app.py
```

默认 EXE 输出在 `desktop/src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis/`，`.verification/package-report.json` 记录源提交、包体积和 SHA256。CI 在隔离 Windows 主机运行 `python desktop/verify_windows.py`，验证实际安装的 App、捆绑浏览器、首次建库、退出、重装和卸载。验收会更改当前账户的安装登记，请在干净的测试账户中运行。构建和验证结果见 [v2.1.0 实施记录](../docs/versions/v2.1.0.md)。
