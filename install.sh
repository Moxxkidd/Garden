#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
用法：./install.sh

在当前用户目录安装 Garden，不需要 sudo：
  命令入口：~/.local/bin/garden 和 ~/.local/bin/gardenctl
  运行环境：~/.local/share/garden/runtime
  用户数据：~/.garden

安装不会修改 shell 配置。若 ~/.local/bin 不在 PATH 中，脚本会输出对应的配置命令。
EOF
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  usage
  exit 0
fi

if [ "$#" -ne 0 ]; then
  usage >&2
  exit 2
fi

case "$(uname -s)" in
  Darwin | Linux) ;;
  *)
    echo "不支持的系统：$(uname -s)。当前仅支持 macOS、Linux 和 WSL。" >&2
    exit 1
    ;;
esac

case "$(uname -m)" in
  arm64 | aarch64 | x86_64 | amd64) ;;
  *)
    echo "不支持的 CPU 架构：$(uname -m)。" >&2
    exit 1
    ;;
esac

python_is_usable() {
  "$1" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
}

python_is_preferred() {
  "$1" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
}

GARDEN_PYTHON_BIN=""
if [ -n "${GARDEN_PYTHON:-}" ]; then
  if command -v "$GARDEN_PYTHON" >/dev/null 2>&1 && python_is_usable "$GARDEN_PYTHON"; then
    GARDEN_PYTHON_BIN="$(command -v "$GARDEN_PYTHON")"
  else
    echo "GARDEN_PYTHON 指向的解释器不可用或低于 Python 3.10：$GARDEN_PYTHON" >&2
    exit 1
  fi
else
  GARDEN_COMPATIBLE_PYTHON_BIN=""
  if command -v python3 >/dev/null 2>&1 && python_is_usable python3; then
    if python_is_preferred python3; then
      GARDEN_PYTHON_BIN="$(command -v python3)"
    else
      GARDEN_COMPATIBLE_PYTHON_BIN="$(command -v python3)"
    fi
  fi

  for candidate in \
    python3.13 python3.12 \
    /opt/homebrew/opt/python@3.13/libexec/bin/python3 \
    /opt/homebrew/opt/python@3.12/libexec/bin/python3 \
    /usr/local/opt/python@3.13/libexec/bin/python3 \
    /usr/local/opt/python@3.12/libexec/bin/python3
  do
    if [ -z "$GARDEN_PYTHON_BIN" ] \
      && command -v "$candidate" >/dev/null 2>&1 \
      && python_is_usable "$candidate"
    then
      GARDEN_PYTHON_BIN="$(command -v "$candidate")"
      break
    fi
  done

  if [ -z "$GARDEN_PYTHON_BIN" ] && [ -n "$GARDEN_COMPATIBLE_PYTHON_BIN" ]; then
    GARDEN_PYTHON_BIN="$GARDEN_COMPATIBLE_PYTHON_BIN"
  fi

  for candidate in \
    python3.11 python3.10 \
    /opt/homebrew/opt/python@3.11/libexec/bin/python3 \
    /usr/local/opt/python@3.11/libexec/bin/python3
  do
    if [ -z "$GARDEN_PYTHON_BIN" ] \
      && command -v "$candidate" >/dev/null 2>&1 \
      && python_is_usable "$candidate"
    then
      GARDEN_PYTHON_BIN="$(command -v "$candidate")"
      break
    fi
  done
fi

if [ -z "$GARDEN_PYTHON_BIN" ]; then
  echo "未找到 Python 3.10 或更高版本。可安装 Python 3.12，或通过 GARDEN_PYTHON 指定解释器。" >&2
  exit 1
fi

GARDEN_PYTHON_VERSION="$("$GARDEN_PYTHON_BIN" -c 'import platform; print(platform.python_version())')"
echo "使用 Python：$GARDEN_PYTHON_BIN ($GARDEN_PYTHON_VERSION)"

GARDEN_SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
GARDEN_USER_HOME="${HOME:?HOME 未设置，无法执行用户级安装。}"
GARDEN_HOME="${GARDEN_HOME:-$GARDEN_USER_HOME/.garden}"
GARDEN_INSTALL_ROOT="${GARDEN_INSTALL_ROOT:-$GARDEN_USER_HOME/.local/share/garden}"
GARDEN_BIN_DIR="${GARDEN_BIN_DIR:-$GARDEN_USER_HOME/.local/bin}"
GARDEN_RUNTIME_DIR="$GARDEN_INSTALL_ROOT/runtime"
GARDEN_RUNTIME_STATE_DIR="$GARDEN_HOME/runtime"

if [ -f "$GARDEN_RUNTIME_STATE_DIR/server.pid" ]; then
  GARDEN_RUNNING_PID="$(tr -d '[:space:]' < "$GARDEN_RUNTIME_STATE_DIR/server.pid")"
  if [ -n "$GARDEN_RUNNING_PID" ] && kill -0 "$GARDEN_RUNNING_PID" 2>/dev/null; then
    cat >&2 <<EOF
Garden 当前正在运行，无法安全升级。

请先执行：
garden stop

然后重新执行：
./install.sh
EOF
    exit 1
  fi
fi

mkdir -p "$GARDEN_HOME" "$GARDEN_HOME/reports" "$GARDEN_HOME/storage" \
  "$GARDEN_HOME/logs" "$GARDEN_RUNTIME_STATE_DIR" "$GARDEN_INSTALL_ROOT" "$GARDEN_BIN_DIR"

GARDEN_CONFIG_FILE="$GARDEN_HOME/config.env"
GARDEN_CONFIG_MARKER="# GARDEN_INSTALLER_MANAGED_CONFIG_VERSION=3"
GARDEN_PREVIOUS_CONFIG_MARKER="# GARDEN_INSTALLER_MANAGED_CONFIG_VERSION=2"
if [ ! -f "$GARDEN_CONFIG_FILE" ]; then
  {
    echo "$GARDEN_CONFIG_MARKER"
    grep -v \
      -e '^GARDEN_DATABASE_URL=' \
      -e '^GARDEN_PROJECT_VERSION=' \
      "$GARDEN_SOURCE_ROOT/.env.example"
  } > "$GARDEN_CONFIG_FILE"
elif grep -Fq "$GARDEN_PREVIOUS_CONFIG_MARKER" "$GARDEN_CONFIG_FILE"; then
  GARDEN_CONFIG_STAGE="$(mktemp "$GARDEN_HOME/.config.env.XXXXXX")"
  sed \
    -e "s|^$GARDEN_PREVIOUS_CONFIG_MARKER$|$GARDEN_CONFIG_MARKER|" \
    -e '/^GARDEN_PROJECT_VERSION=/d' \
    "$GARDEN_CONFIG_FILE" > "$GARDEN_CONFIG_STAGE"
  chmod --reference="$GARDEN_CONFIG_FILE" "$GARDEN_CONFIG_STAGE" 2>/dev/null \
    || chmod 0600 "$GARDEN_CONFIG_STAGE"
  mv "$GARDEN_CONFIG_STAGE" "$GARDEN_CONFIG_FILE"
  echo "已迁移安装器托管配置：版本由已安装 Garden 软件包提供"
elif ! grep -Fq "$GARDEN_CONFIG_MARKER" "$GARDEN_CONFIG_FILE" \
  && grep -Fq '# Garden local demo defaults' "$GARDEN_CONFIG_FILE" \
  && grep -Fq '# Safe-by-default guardrail: keep false for local/demo use only' "$GARDEN_CONFIG_FILE" \
  && grep -Fqx 'GARDEN_ALLOW_NON_LOCAL_TARGETS=false' "$GARDEN_CONFIG_FILE"
then
  GARDEN_CONFIG_STAGE="$(mktemp "$GARDEN_HOME/.config.env.XXXXXX")"
  {
    echo "$GARDEN_CONFIG_MARKER"
    sed \
      -e 's|# Safe-by-default guardrail: keep false for local/demo use only|# 已授权公网目标默认允许；如需仅扫描本机目标，可显式改为 false。|' \
      -e 's|^GARDEN_ALLOW_NON_LOCAL_TARGETS=false$|GARDEN_ALLOW_NON_LOCAL_TARGETS=true|' \
      -e '/^GARDEN_PROJECT_VERSION=/d' \
      "$GARDEN_CONFIG_FILE"
  } > "$GARDEN_CONFIG_STAGE"
  chmod --reference="$GARDEN_CONFIG_FILE" "$GARDEN_CONFIG_STAGE" 2>/dev/null \
    || chmod 0600 "$GARDEN_CONFIG_STAGE"
  mv "$GARDEN_CONFIG_STAGE" "$GARDEN_CONFIG_FILE"
  echo "已迁移旧版自动生成的公网目标策略：GARDEN_ALLOW_NON_LOCAL_TARGETS=true"
fi

if [ -f "$GARDEN_HOME/garden.db" ]; then
  GARDEN_BACKUP_DIR="$GARDEN_HOME/backups"
  mkdir -p "$GARDEN_BACKUP_DIR"
  GARDEN_BACKUP_FILE="$GARDEN_BACKUP_DIR/garden-$(date +%Y%m%d%H%M%S).db"
  cp -p "$GARDEN_HOME/garden.db" "$GARDEN_BACKUP_FILE"
  echo "已备份数据库：$GARDEN_BACKUP_FILE"
fi

GARDEN_STAGE_DIR="$(mktemp -d "$GARDEN_INSTALL_ROOT/.runtime.XXXXXX")"
GARDEN_OLD_RUNTIME_DIR="$GARDEN_INSTALL_ROOT/.runtime.previous.$$"
cleanup_stage() {
  if [ -d "$GARDEN_STAGE_DIR" ]; then
    rm -rf "$GARDEN_STAGE_DIR"
  fi
}
trap cleanup_stage EXIT

"$GARDEN_PYTHON_BIN" -m venv "$GARDEN_STAGE_DIR/venv"
GARDEN_STAGE_PYTHON="$GARDEN_STAGE_DIR/venv/bin/python"
export PIP_RETRIES="${PIP_RETRIES:-10}"
export PIP_TIMEOUT="${PIP_TIMEOUT:-30}"
run_install_pip() {
  case "${ALL_PROXY:-${all_proxy:-}}" in
    socks* | SOCKS*)
      echo "检测到 SOCKS ALL_PROXY；安装依赖时临时忽略该变量。"
      env -u ALL_PROXY -u all_proxy "$GARDEN_STAGE_PYTHON" -m pip "$@"
      ;;
    *)
      "$GARDEN_STAGE_PYTHON" -m pip "$@"
      ;;
  esac
}
run_install_pip install --upgrade pip
run_install_pip install "$GARDEN_SOURCE_ROOT"

GARDEN_CLI_RUNTIME=1 GARDEN_HOME="$GARDEN_HOME" \
  "$GARDEN_STAGE_PYTHON" -m app.cli.main db upgrade

cat > "$GARDEN_STAGE_DIR/launcher.py" <<'PY'
"""Garden 正式安装入口；隔离当前目录与 PYTHONPATH 后加载已安装包。"""

import os

from app.cli.main import app


if __name__ == "__main__":
    app(prog_name=os.environ.get("GARDEN_CLI_NAME", "garden"))
PY
chmod 0644 "$GARDEN_STAGE_DIR/launcher.py"

if [ -d "$GARDEN_RUNTIME_DIR" ]; then
  mv "$GARDEN_RUNTIME_DIR" "$GARDEN_OLD_RUNTIME_DIR"
fi
mv "$GARDEN_STAGE_DIR" "$GARDEN_RUNTIME_DIR"
if [ -d "$GARDEN_OLD_RUNTIME_DIR" ]; then
  rm -rf "$GARDEN_OLD_RUNTIME_DIR"
fi

write_launcher() {
  local launcher_path="$1"
  local command_name="$2"
  cat > "$launcher_path" <<EOF
#!/usr/bin/env sh
set -eu
export GARDEN_CLI_RUNTIME=1
export GARDEN_CLI_NAME="$command_name"
export GARDEN_HOME="\${GARDEN_HOME:-$GARDEN_HOME}"
exec "$GARDEN_RUNTIME_DIR/venv/bin/python" -I "$GARDEN_RUNTIME_DIR/launcher.py" "\$@"
EOF
  chmod 0755 "$launcher_path"
}

write_launcher "$GARDEN_BIN_DIR/garden" "garden"
write_launcher "$GARDEN_BIN_DIR/gardenctl" "gardenctl"

echo "Garden 已安装。"
echo "命令入口：$GARDEN_BIN_DIR/garden"

case ":${PATH}:" in
  *":${GARDEN_BIN_DIR}:"*)
    echo "验证安装：garden --version"
    ;;
  *)
    GARDEN_SHELL_NAME="$(basename "${SHELL:-sh}")"
    case "$GARDEN_SHELL_NAME" in
      zsh) GARDEN_SHELL_CONFIG="~/.zprofile" ;;
      bash) GARDEN_SHELL_CONFIG="~/.bash_profile" ;;
      *) GARDEN_SHELL_CONFIG="当前 shell 的启动配置文件" ;;
    esac
    cat <<EOF
请将 ~/.local/bin 加入 PATH 后重新打开终端：
export PATH="\$HOME/.local/bin:\$PATH"

可写入：$GARDEN_SHELL_CONFIG
验证安装：garden --version
EOF
    ;;
esac
