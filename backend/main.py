from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from materials import router as materials_router
from constraints import router as constraints_router
from geometry import router as geometry_router
from solver import router as solver_router
from ai_assistant import router as ai_router
from jobs import router as jobs_router
from thermal import router as thermal_router
from modal import router as modal_router
from mesh_quality import router as mesh_quality_router
from projects import router as projects_router
from auth import router as auth_router

# 集中配置：上传目录为绝对路径，CORS 来源可用环境变量覆盖
from config import CORS_ALLOW_ORIGINS, UPLOAD_DIR, ensure_upload_dir
from logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

app = FastAPI(
    title="SimCloud AI 仿真后端",
    description="几何导入 / 网格与质量检查 / 线弹性静力 / 稳态热传导 / 模态分析 / 项目与用户 / AI 助手",
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
ensure_upload_dir()

# 注意：**不再**把 uploads/ 挂成静态目录。
#
# 原先 `app.mount("/uploads", StaticFiles(...))` 让"知道文件名就能下载"成为可能，
# 不需要登录——而 uploads/ 里既有用户上传的 CAD 原件，也有派生的预览 STL。
# 那时没法简单加鉴权，因为 three.js 的加载器不会带 Authorization 头；
# 现在改为前端用带认证头的方式取文件（`GET /api/geometry/{filename}/download`，
# 见 geometry.py 的说明），公开的静态目录因此可以彻底关掉。
# 目录是否存在的诊断信息仍由 /api/health 提供。

# Include routers
app.include_router(materials_router, prefix="/api")
app.include_router(constraints_router, prefix="/api")
app.include_router(geometry_router, prefix="/api")
app.include_router(solver_router, prefix="/api")
app.include_router(ai_router, prefix="/api")
app.include_router(jobs_router, prefix="/api")
app.include_router(thermal_router, prefix="/api")
app.include_router(modal_router, prefix="/api")
app.include_router(mesh_quality_router, prefix="/api")
app.include_router(projects_router, prefix="/api")
app.include_router(auth_router, prefix="/api")

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
