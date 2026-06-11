# amazingdata_worker/Dockerfile
# Worker image — depends on adshare-base for the slow setup
# (apt + AmazingData/tgw whl + their forced deps: pydantic/numba/scipy/statsmodels)
#
# Build flow:
#   1. bin/build-base.sh                  # 一次性 3-5 分钟（升级 whl 时才重 build）
#   2. docker compose build amazingdata-worker   # 5-10 秒（改 adshare 代码不触发 base 重 build）
#   3. docker compose up -d --force-recreate amazingdata-worker
#
# 注意: 构建此镜像时必须指定 --platform linux/amd64
# (AmazingData SDK C 扩展限制 — base image 也只支持 linux/amd64)

FROM adshare-base:latest

# 工作目录
WORKDIR /app

# 复制项目级文件
COPY pyproject.toml README.md ./
COPY amazingdata_worker/requirements.txt ./requirements-worker.txt

# 安装 worker 专用 Python 依赖（adshare 仓库自身的依赖，不含 SDK 强制要求的部分）
# 注：numba/scipy/statsmodels/pydantic 由 base image 提供，这里不重复装
RUN pip install --no-cache-dir -r requirements-worker.txt

# 安装 adshare 包本身（无依赖模式 - 上面 requirements.txt 已覆盖）
RUN pip install --no-cache-dir --no-deps .

# 复制项目代码
COPY adshare/ ./adshare/
COPY scripts/ ./scripts/
COPY config/ ./config/
COPY amazingdata_worker/ ./amazingdata_worker/

# 创建运行时目录
RUN mkdir -p cache logs data

# Worker 健康检查（python:3.11-slim 无 pgrep/ps，用 kill -0 检查 PID 1）
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD sh -c "kill -0 1"

# 启动命令（作为模块运行，确保 import 路径正确）
CMD ["python", "-m", "amazingdata_worker.main"]
