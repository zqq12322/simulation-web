from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from materials import router as materials_router
from constraints import router as constraints_router
from geometry import router as geometry_router
from solver import router as solver_router
from ai_assistant import router as ai_router
import os

app = FastAPI()

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure uploads directory exists
if not os.path.exists("uploads"):
    os.makedirs("uploads")

# Mount uploads directory for static file serving
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Include routers
app.include_router(materials_router, prefix="/api")
app.include_router(constraints_router, prefix="/api")
app.include_router(geometry_router, prefix="/api")
app.include_router(solver_router, prefix="/api")
app.include_router(ai_router, prefix="/api")

@app.get("/")
async def root():
    return {"message": "Simulation Backend is running"}
