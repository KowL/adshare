ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

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

# 安装 Python 依赖（API 服务不安装 AmazingData SDK，由独立 worker 负责）
RUN pip install --no-cache-dir -e "." tables

# 复制项目代码
COPY adshare/ ./adshare/
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
