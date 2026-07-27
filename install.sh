#!/bin/sh

set -eu

AS1688_SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
AS1688_INSTALL_ROOT=${AS1688_INSTALL_ROOT:-"$HOME/.local/share/as1688"}
AS1688_BIN_DIR=${AS1688_BIN_DIR:-"$HOME/.local/bin"}
AS1688_REPOSITORY=${AS1688_REPOSITORY:-"marvellouspizza/1688-Agent-Search"}
AS1688_REF=${AS1688_REF:-"main"}
AS1688_DOWNLOAD_DIR=""
AS1688_STAGE=""
AS1688_BACKUP=""
AS1688_WRAPPER_TMP=""

fail_as1688_install() {
    echo "安装失败：$1" >&2
    exit 1
}

cleanup_as1688_temporary_files() {
    if [ -n "$AS1688_WRAPPER_TMP" ] && [ -e "$AS1688_WRAPPER_TMP" ]; then
        rm -f "$AS1688_WRAPPER_TMP"
    fi
    if [ -n "$AS1688_STAGE" ] && [ -d "$AS1688_STAGE" ]; then
        rm -rf "$AS1688_STAGE"
    fi
    if [ -n "$AS1688_DOWNLOAD_DIR" ] && [ -d "$AS1688_DOWNLOAD_DIR" ]; then
        rm -rf "$AS1688_DOWNLOAD_DIR"
    fi
}
trap cleanup_as1688_temporary_files EXIT HUP INT TERM

case "$AS1688_INSTALL_ROOT" in
    ""|/|"$HOME") fail_as1688_install "安装目录不安全：$AS1688_INSTALL_ROOT" ;;
esac

if [ ! -f "$AS1688_SOURCE_DIR/package.json" ] || [ ! -d "$AS1688_SOURCE_DIR/src" ]; then
    command -v curl >/dev/null 2>&1 || fail_as1688_install "在线安装需要 curl"
    command -v tar >/dev/null 2>&1 || fail_as1688_install "在线安装需要 tar"
    AS1688_DOWNLOAD_DIR=$(mktemp -d "${TMPDIR:-/tmp}/as1688-download.XXXXXX")
    AS1688_ARCHIVE="$AS1688_DOWNLOAD_DIR/source.tar.gz"
    AS1688_EXTRACTED_SOURCE="$AS1688_DOWNLOAD_DIR/source"
    mkdir -p "$AS1688_EXTRACTED_SOURCE"
    echo "正在下载 as1688：$AS1688_REPOSITORY ($AS1688_REF)"
    curl -fsSL \
        "https://github.com/$AS1688_REPOSITORY/archive/$AS1688_REF.tar.gz" \
        -o "$AS1688_ARCHIVE"
    tar -xzf "$AS1688_ARCHIVE" \
        -C "$AS1688_EXTRACTED_SOURCE" \
        --strip-components=1
    AS1688_SOURCE_DIR="$AS1688_EXTRACTED_SOURCE"
fi

command -v node >/dev/null 2>&1 || fail_as1688_install "需要 Node.js 24 或更高版本"
command -v npm >/dev/null 2>&1 || fail_as1688_install "需要 npm"
if ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 24 ? 0 : 1)'; then
    fail_as1688_install "需要 Node.js 24 或更高版本（当前 $(node --version)）"
fi
if [ ! -f "$AS1688_SOURCE_DIR/package-lock.json" ]; then
    fail_as1688_install "源码缺少 package-lock.json"
fi

AS1688_INSTALL_PARENT=$(dirname -- "$AS1688_INSTALL_ROOT")
mkdir -p "$AS1688_INSTALL_PARENT" "$AS1688_BIN_DIR"
chmod 700 "$AS1688_BIN_DIR"
AS1688_STAGE=$(mktemp -d "$AS1688_INSTALL_PARENT/.as1688-install.XXXXXX")

cp "$AS1688_SOURCE_DIR/package.json" "$AS1688_STAGE/package.json"
cp "$AS1688_SOURCE_DIR/package-lock.json" "$AS1688_STAGE/package-lock.json"
cp "$AS1688_SOURCE_DIR/tsconfig.json" "$AS1688_STAGE/tsconfig.json"
cp -R "$AS1688_SOURCE_DIR/src" "$AS1688_STAGE/src"
if [ -d "$AS1688_SOURCE_DIR/skills" ]; then
    cp -R "$AS1688_SOURCE_DIR/skills" "$AS1688_STAGE/skills"
fi

echo "正在构建 TypeScript 运行时..."
(
    cd "$AS1688_STAGE"
    npm ci --no-audit --no-fund
    npm run build
    npm prune --omit=dev --no-audit --no-fund
    rm -rf src tsconfig.json
)
chmod 700 "$AS1688_STAGE"
chmod 700 "$AS1688_STAGE/dist/cli-entry.js"

AS1688_WRAPPER_TMP=$(mktemp "$AS1688_BIN_DIR/.as1688.XXXXXX")
{
    echo '#!/bin/sh'
    echo 'set -eu'
    echo 'AS1688_RUNTIME=${AS1688_INSTALL_ROOT:-"$HOME/.local/share/as1688"}'
    echo 'export AGENT_SEARCH_1688_SKILL_ROOT="$AS1688_RUNTIME/skills"'
    echo 'exec node "$AS1688_RUNTIME/dist/cli-entry.js" "$@"'
} > "$AS1688_WRAPPER_TMP"
chmod 700 "$AS1688_WRAPPER_TMP"

if [ -e "$AS1688_INSTALL_ROOT" ]; then
    AS1688_BACKUP=$(mktemp -d "$AS1688_INSTALL_PARENT/.as1688-backup.XXXXXX")
    rmdir "$AS1688_BACKUP"
    mv "$AS1688_INSTALL_ROOT" "$AS1688_BACKUP"
fi

if ! mv "$AS1688_STAGE" "$AS1688_INSTALL_ROOT"; then
    if [ -n "$AS1688_BACKUP" ] && [ -e "$AS1688_BACKUP" ]; then
        mv "$AS1688_BACKUP" "$AS1688_INSTALL_ROOT"
    fi
    fail_as1688_install "无法启用新运行时"
fi
AS1688_STAGE=""

if ! mv "$AS1688_WRAPPER_TMP" "$AS1688_BIN_DIR/as1688"; then
    rm -rf "$AS1688_INSTALL_ROOT"
    if [ -n "$AS1688_BACKUP" ] && [ -e "$AS1688_BACKUP" ]; then
        mv "$AS1688_BACKUP" "$AS1688_INSTALL_ROOT"
    fi
    fail_as1688_install "无法安装全局命令"
fi
AS1688_WRAPPER_TMP=""
if [ -n "$AS1688_BACKUP" ] && [ -e "$AS1688_BACKUP" ]; then
    rm -rf "$AS1688_BACKUP"
fi
AS1688_BACKUP=""

if [ "${AS1688_SKIP_PATH_UPDATE:-0}" != "1" ]; then
    case ":$PATH:" in
        *":$AS1688_BIN_DIR:"*)
            ;;
        *)
            AS1688_PROFILE="$HOME/.profile"
            case "${SHELL:-}" in
                */zsh) AS1688_PROFILE="$HOME/.zshrc" ;;
            esac
            AS1688_PATH_LINE="export PATH=\"$AS1688_BIN_DIR:\$PATH\""
            if [ ! -f "$AS1688_PROFILE" ] || ! grep -Fq "$AS1688_PATH_LINE" "$AS1688_PROFILE"; then
                {
                    echo
                    echo '# as1688 global command'
                    echo "$AS1688_PATH_LINE"
                } >> "$AS1688_PROFILE"
            fi
            echo "已把 $AS1688_BIN_DIR 写入 $AS1688_PROFILE"
            echo "请重新打开终端，然后运行 as1688"
            ;;
    esac
fi

echo "as1688 安装完成：$AS1688_BIN_DIR/as1688"
echo "启动命令：as1688"
