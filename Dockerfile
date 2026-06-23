# 应急电台智能体 - Docker 构建
# 前端: Node/Vite 构建静态文件
# 后端: FastAPI + faster-whisper 托管 API 和 frontend/dist

FROM node:20-slim AS frontend

WORKDIR /app/frontend
ARG VITE_ADMIN_TOKEN=""
ENV VITE_ADMIN_TOKEN=${VITE_ADMIN_TOKEN}

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


FROM python:3.11-slim AS backend

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    libsndfile1 \
    portaudio19-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制后端代码
COPY backend/ ./backend/
COPY --from=frontend /app/frontend/dist ./frontend/dist

# 暴露端口
EXPOSE 8000

# 工作目录
WORKDIR /app/backend

# 默认启动(模拟模式,无 GPU)
ENV MOCK_MODE=true
ENV NO_GPU=true
ENV MODEL_SIZE=small
ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
