# 使用官方 Python 运行时作为基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 复制 requirements 并安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 暴露端口
EXPOSE 8002

# 设置环境变量
ENV ANTHROPIC_API_KEY=""
ENV GENERATOR_URL="http://localhost:8002"
ENV VERIFIER_URL="http://localhost:8000"
ENV CONTROLLER_URL="http://localhost:8001"
ENV MAX_ITERATIONS="5"

# 运行应用
CMD ["python", "main.py", "8002"]
