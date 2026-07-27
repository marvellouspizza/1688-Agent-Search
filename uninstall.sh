#!/bin/sh

set -eu

AS1688_INSTALL_ROOT=${AS1688_INSTALL_ROOT:-"$HOME/.local/share/as1688"}
AS1688_BIN_DIR=${AS1688_BIN_DIR:-"$HOME/.local/bin"}
AS1688_USER_DATA=${AGENT_SEARCH_1688_HOME:-"$HOME/.1688-agent-search"}

case "$AS1688_INSTALL_ROOT" in
    ""|/|"$HOME")
        echo "卸载失败：安装目录不安全：$AS1688_INSTALL_ROOT" >&2
        exit 1
        ;;
esac

rm -f "$AS1688_BIN_DIR/as1688"
if [ -e "$AS1688_INSTALL_ROOT" ]; then
    rm -rf "$AS1688_INSTALL_ROOT"
fi

echo "as1688 已卸载。配置和会话保留在 $AS1688_USER_DATA。"
