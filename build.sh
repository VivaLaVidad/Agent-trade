#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  TradeStealth_Core — Linux/macOS 生产构建脚本 (PyArmor)
#  将 src/core/ 加密混淆为 .so，其余模块原样复制至 dist/
# ═══════════════════════════════════════════════════════════

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
DIST="${ROOT}/dist"
SRC="${ROOT}/src"

echo "[1/6] 检查 PyArmor ..."
if ! pip show pyarmor &>/dev/null; then
    echo "     PyArmor 未安装，正在安装..."
    pip install pyarmor
fi

echo "[2/6] 清理旧构建 ..."
rm -rf "${DIST}"
mkdir -p "${DIST}/src"

echo "[3/6] 加密核心模块 src/core/ ..."
pyarmor gen \
    --output "${DIST}/src/core" \
    --enable-jit \
    "${SRC}/core/logger.py" "${SRC}/core/security.py"

echo "[4/6] 复制非加密模块 ..."
cp -r "${SRC}/agents"     "${DIST}/src/agents"
cp -r "${SRC}/database"   "${DIST}/src/database"
cp -r "${SRC}/rpa_engine"  "${DIST}/src/rpa_engine"
cp -r "${SRC}/monitor"    "${DIST}/src/monitor"
cp    "${SRC}/__init__.py" "${DIST}/src/__init__.py"

echo "[5/6] 复制入口文件与配置 ..."
cp "${ROOT}/main.py"          "${DIST}/"
cp "${ROOT}/rpa_server.py"    "${DIST}/"
cp "${ROOT}/requirements.txt" "${DIST}/"
[ -f "${ROOT}/.env.example" ] && cp "${ROOT}/.env.example" "${DIST}/"

mkdir -p "${DIST}/db" "${DIST}/logs"

echo "[6/6] 构建完成！"
echo ""
echo "产物目录: ${DIST}"
echo "加密范围: src/core/ (security.py, logger.py)"
echo ""
echo "部署步骤:"
echo "  1. 将 dist/ 复制到目标机器"
echo "  2. 在 dist/ 中创建 .env 并配置密钥"
echo "  3. pip install -r requirements.txt"
echo "  4. python rpa_server.py   (启动 RPA 进程)"
echo "  5. python main.py         (启动主服务)"
