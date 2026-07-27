#!/bin/sh

set -eu

AS1688_SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
AS1688_INSTALL_ROOT=${AS1688_INSTALL_ROOT:-"$HOME/.local/share/as1688"}
AS1688_BIN_DIR=${AS1688_BIN_DIR:-"$HOME/.local/bin"}
AS1688_REPOSITORY=${AS1688_REPOSITORY:-"marvellouspizza/1688-Agent-Search"}
AS1688_REF=${AS1688_REF:-"main"}
AS1688_DOWNLOAD_DIR=""
AS1688_STAGE=""

cleanup_as1688_temporary_files() {
    if [ -n "$AS1688_STAGE" ]; then
        rm -rf "$AS1688_STAGE"
    fi
    if [ -n "$AS1688_DOWNLOAD_DIR" ]; then
        rm -rf "$AS1688_DOWNLOAD_DIR"
    fi
}
trap cleanup_as1688_temporary_files EXIT HUP INT TERM

if [ ! -d "$AS1688_SOURCE_DIR/src/agent_search_1688" ]; then
    if ! command -v curl >/dev/null 2>&1; then
        echo "安装失败：在线安装需要 curl" >&2
        exit 1
    fi
    if ! command -v tar >/dev/null 2>&1; then
        echo "安装失败：在线安装需要 tar" >&2
        exit 1
    fi
    AS1688_DOWNLOAD_DIR=$(mktemp -d "${TMPDIR:-/tmp}/as1688-download.XXXXXX")
    AS1688_ARCHIVE="$AS1688_DOWNLOAD_DIR/source.tar.gz"
    AS1688_EXTRACTED_SOURCE="$AS1688_DOWNLOAD_DIR/source"
    mkdir -p "$AS1688_EXTRACTED_SOURCE"
    echo "正在下载 as1688：$AS1688_REPOSITORY ($AS1688_REF)"
    curl -fsSL \
        "https://github.com/$AS1688_REPOSITORY/archive/refs/heads/$AS1688_REF.tar.gz" \
        -o "$AS1688_ARCHIVE"
    tar -xzf "$AS1688_ARCHIVE" \
        -C "$AS1688_EXTRACTED_SOURCE" \
        --strip-components=1
    AS1688_SOURCE_DIR="$AS1688_EXTRACTED_SOURCE"
fi

AS1688_PYTHON=$(command -v python3 || true)
if [ -z "$AS1688_PYTHON" ]; then
    echo "安装失败：需要 Python 3.9 或更高版本" >&2
    exit 1
fi

if ! "$AS1688_PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 9))'; then
    echo "安装失败：需要 Python 3.9 或更高版本" >&2
    exit 1
fi

mkdir -p "$AS1688_INSTALL_ROOT" "$AS1688_BIN_DIR"
chmod 700 "$AS1688_INSTALL_ROOT" "$AS1688_BIN_DIR"

AS1688_STAGE=$(mktemp -d "$AS1688_INSTALL_ROOT/.install.XXXXXX")

# 先由 shell 复制到用户目录，再让 Python 打包。这样 Xcode Python 不需要
# 直接读取 macOS Documents 或 Downloads 中受保护的项目源码。
cp -R "$AS1688_SOURCE_DIR/src" "$AS1688_STAGE/src"
"$AS1688_PYTHON" -m zipapp "$AS1688_STAGE/src" \
    --main agent_search_1688.cli:main \
    --python '/usr/bin/env python3' \
    --output "$AS1688_STAGE/as1688.pyz"
chmod 700 "$AS1688_STAGE/as1688.pyz"
mv "$AS1688_STAGE/as1688.pyz" "$AS1688_INSTALL_ROOT/as1688.pyz"
if [ -d "$AS1688_SOURCE_DIR/skills" ]; then
    mkdir -p "$AS1688_INSTALL_ROOT/skills"
    cp -R "$AS1688_SOURCE_DIR/skills/." "$AS1688_INSTALL_ROOT/skills/"
fi

AS1688_WRAPPER="$AS1688_STAGE/as1688"
{
    echo '#!/bin/sh'
    echo 'AS1688_RUNTIME=${AS1688_INSTALL_ROOT:-"$HOME/.local/share/as1688"}'
    echo 'export AGENT_SEARCH_1688_SKILL_ROOT="$AS1688_RUNTIME/skills"'
    echo 'exec python3 "$AS1688_RUNTIME/as1688.pyz" "$@"'
} > "$AS1688_WRAPPER"
chmod 700 "$AS1688_WRAPPER"
mv "$AS1688_WRAPPER" "$AS1688_BIN_DIR/as1688"

case ":$PATH:" in
    *":$AS1688_BIN_DIR:"*)
        ;;
    *)
        AS1688_PROFILE="$HOME/.profile"
        case "${SHELL:-}" in
            */zsh) AS1688_PROFILE="$HOME/.zshrc" ;;
        esac
        AS1688_PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
        if [ ! -f "$AS1688_PROFILE" ] || ! grep -Fq "$AS1688_PATH_LINE" "$AS1688_PROFILE"; then
            {
                echo
                echo '# as1688 global command'
                echo "$AS1688_PATH_LINE"
            } >> "$AS1688_PROFILE"
        fi
        echo "已把 ~/.local/bin 写入 $AS1688_PROFILE"
        echo "请重新打开终端，然后运行 as1688"
        ;;
esac

echo "as1688 安装完成：$AS1688_BIN_DIR/as1688"
echo "启动命令：as1688"
