# ============================================================
#  SimCloud AI — 后端镜像
#
#  为什么需要它：
#   1. Gmsh 的 Python wheel 虽然自带原生库，但无头环境仍依赖若干系统库
#      （libglu / libXrender 等），手工装很容易在本机/CI 上翻车；
#   2. 让 Linux / macOS 协作者不必折腾 Python 版本与虚拟环境。
#
#  构建与运行：
#      docker build -t simcloud-backend .
#      docker run --rm -p 8000:8000 --env-file backend/.env simcloud-backend
#
#  注意：backend/config.py 里的 UPLOAD_DIR 是基于 __file__ 的绝对路径，
#  因此容器里无论工作目录在哪都能正确定位 uploads/。
# ============================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Gmsh 运行所需的系统库（无头 OpenGL / X11 相关）
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libglu1-mesa \
      libxrender1 \
      libxcursor1 \
      libxft2 \
      libxinerama1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖，利用镜像层缓存：只改业务代码时不会重装 gmsh/scipy
COPY backend/requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip \
 && python -m pip install -r requirements.txt

COPY backend/ ./

# uploads 属于可再生缓存，用卷挂载出去更合适
VOLUME ["/app/uploads"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)"

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
