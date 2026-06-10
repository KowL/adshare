FROM python:3.11-slim

WORKDIR /app

# 注意: 构建此镜像时必须指定 --platform linux/amd64
# docker build --platform linux/amd64 -f amazingdata_worker/Dockerfile .

# 使用阿里云镜像源加速
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    libhdf5-dev \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# 复制依赖文件
COPY pyproject.toml README.md ./

# 复制并安装 AmazingData SDK（本地 whl 文件）
COPY AmazingData-1.1.8-cp311-none-any.whl tgw-1.0.8.7-py3-none-any.whl ./
RUN pip install --no-cache-dir tgw-1.0.8.7-py3-none-any.whl \
    AmazingData-1.1.8-cp311-none-any.whl

# 安装 Python 依赖
RUN pip install --no-cache-dir -e "." tables

# 复制项目代码
COPY adshare/ ./adshare/
COPY scripts/ ./scripts/
COPY config/ ./config/
COPY amazingdata_worker/ ./amazingdata_worker/

# 创建缓存和日志目录
RUN mkdir -p cache logs data

# Worker 健康检查（简单检查进程存活）
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD pgrep -f "python -m amazingdata_worker.main" || exit 1

# 启动命令（作为模块运行，确保 import 路径正确）
CMD ["python", "-m", "amazingdata_worker.main"]
