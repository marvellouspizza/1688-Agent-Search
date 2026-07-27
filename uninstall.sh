#!/bin/sh

set -eu

normalize_as1688_directory() {
    AS1688_DIRECTORY=$1
    while [ "$AS1688_DIRECTORY" != "/" ] && [ "${AS1688_DIRECTORY%/}" != "$AS1688_DIRECTORY" ]; do
        AS1688_DIRECTORY=${AS1688_DIRECTORY%/}
    done
    printf '%s\n' "$AS1688_DIRECTORY"
}

AS1688_HOME_ROOT=$(normalize_as1688_directory "$HOME")
AS1688_INSTALL_ROOT=$(normalize_as1688_directory "${AS1688_INSTALL_ROOT:-"$AS1688_HOME_ROOT/.local/share/as1688"}")
AS1688_BIN_DIR=$(normalize_as1688_directory "${AS1688_BIN_DIR:-"$AS1688_HOME_ROOT/.local/bin"}")
AS1688_USER_DATA=${AGENT_SEARCH_1688_HOME:-"$AS1688_HOME_ROOT/.1688-agent-search"}

case "$AS1688_INSTALL_ROOT" in
    /*) ;;
    *)
        echo "卸载失败：安装目录必须是绝对路径：$AS1688_INSTALL_ROOT" >&2
        exit 1
        ;;
esac
case "$AS1688_INSTALL_ROOT" in
    ""|/|"$AS1688_HOME_ROOT"|*//*|*/../*|*/..|*/./*|*/.)
        echo "卸载失败：安装目录不安全：$AS1688_INSTALL_ROOT" >&2
        exit 1
        ;;
esac
if [ "$(dirname -- "$AS1688_INSTALL_ROOT")" = "/" ]; then
    echo "卸载失败：安装目录不能位于文件系统根目录下：$AS1688_INSTALL_ROOT" >&2
    exit 1
fi
case "$AS1688_BIN_DIR" in
    /*) ;;
    *)
        echo "卸载失败：命令目录必须是绝对路径：$AS1688_BIN_DIR" >&2
        exit 1
        ;;
esac
case "$AS1688_BIN_DIR" in
    ""|/|"$AS1688_HOME_ROOT"|*//*|*/../*|*/..|*/./*|*/.)
        echo "卸载失败：命令目录不安全：$AS1688_BIN_DIR" >&2
        exit 1
        ;;
esac

rm -f "$AS1688_BIN_DIR/as1688"
if [ -e "$AS1688_INSTALL_ROOT" ]; then
    rm -rf "$AS1688_INSTALL_ROOT"
fi

echo "as1688 已卸载。配置和会话保留在 ${AS1688_USER_DATA}。"
