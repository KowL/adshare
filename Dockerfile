FROM python:3.11-slim

WORKDIR /app

# 使用阿里云镜像源加速
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/

# 安装系统依赖（合并为一层，减少体积）
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
COPY config/ ./config/
COPY scripts/ ./scripts/

# 创建缓存和日志目录
RUN mkdir -p cache logs data

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=30s --start-period=60s --retries=5 \
    CMD curl -f http://localhost:8000/health || exit 1

# 启动命令
CMD ["uvicorn", "adshare.main:app", "--host", "0.0.0.0", "--port", "8000"]
