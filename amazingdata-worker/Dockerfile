FROM python:3.11-slim

WORKDIR /app

# 强制 amd64 平台（AmazingData SDK C 扩展仅支持 linux/amd64）
# docker build 时需加 --platform linux/amd64

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
COPY amazingdata-worker/main.py ./main.py

# 创建缓存和日志目录
RUN mkdir -p cache logs data

# Worker 健康检查（简单检查进程存活）
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD pgrep -f "python /app/main.py" || exit 1

# 启动命令
CMD ["python", "/app/main.py"]
