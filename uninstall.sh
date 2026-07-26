#!/bin/sh

set -eu

AS1688_INSTALL_ROOT=${AS1688_INSTALL_ROOT:-"$HOME/.local/share/as1688"}
AS1688_BIN_DIR=${AS1688_BIN_DIR:-"$HOME/.local/bin"}

rm -f "$AS1688_BIN_DIR/as1688"
rm -f "$AS1688_INSTALL_ROOT/as1688.pyz"
rmdir "$AS1688_INSTALL_ROOT" 2>/dev/null || true

echo "as1688 已卸载。普通配置和会话保留在 ~/.1688-agent-search。"
