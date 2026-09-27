from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from materials import router as materials_router
from constraints import router as constraints_router
from geometry import router as geometry_router
from solver import router as solver_router
from ai_assistant import router as ai_router

# 集中配置：上传目录为绝对路径，CORS 来源可用环境变量覆盖
from config import CORS_ALLOW_ORIGINS, UPLOAD_DIR, ensure_upload_dir
from logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

app = FastAPI(
    title="SimCloud AI 仿真后端",
    description="几何导入 / 网格划分 / 线弹性静力求解 / AI 助手",
    version="0.2.0",
)

# Configure CORS（生产环境请用 CORS_ALLOW_ORIGINS 环境变量收紧来源）
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure uploads directory exists (absolute path: no CWD dependency)
upload_dir = ensure_upload_dir()

# Mount uploads directory for static file serving
app.mount("/uploads", StaticFiles(directory=str(upload_dir)), name="uploads")

# Include routers
app.include_router(materials_router, prefix="/api")
app.include_router(constraints_router, prefix="/api")
app.include_router(geometry_router, prefix="/api")
app.include_router(solver_router, prefix="/api")
app.include_router(ai_router, prefix="/api")

@app.get("/")
async def root():
    return {"message": "Simulation Backend is running"}


@app.get("/api/health")
async def health():
    """健康检查（供 CI / 容器编排探针使用）。"""
    return {
        "status": "ok",
        "upload_dir": str(UPLOAD_DIR),
        "upload_dir_exists": UPLOAD_DIR.exists(),
    }
