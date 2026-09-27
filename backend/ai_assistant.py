import os
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables from backend/.env (independent of the working directory)
load_dotenv(Path(__file__).with_name(".env"))

from auth import require_user

#: 整个 router 都要求登录。用**路由级依赖**而不是给每个端点加参数：
#: 端点本身并不需要知道「你是谁」，而且 40 多个既有测试是**直接调用端点函数**的
#: （不经 HTTP），逐个加参数会让它们全部失效。
router = APIRouter(dependencies=[Depends(require_user)])


# DeepSeek uses an OpenAI-compatible API.
# The API key must come from the environment (backend/.env) and never be hard-coded.
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL) if DEEPSEEK_API_KEY else None


def get_client() -> OpenAI:
    """Return the DeepSeek client or a clear error if it is not configured."""
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="AI 服务未配置：请在 backend/.env 中设置 DEEPSEEK_API_KEY",
        )
    return client

class AIRequest(BaseModel):
    user_input: str
    context: Optional[Dict[str, Any]] = None # Optional context (e.g., current geometry info)

class AIResponse(BaseModel):
    response: str
    suggested_actions: Optional[List[Dict[str, Any]]] = None # Structured actions for the frontend to execute

@router.post("/ai/chat", response_model=AIResponse)
async def chat_with_ai(request: AIRequest):
    """
    Chat with the AI assistant to get help with simulation setup.
    """
    try:
        system_prompt = """
        You are an expert CAE (Computer-Aided Engineering) assistant. 
        Your goal is to help users configure simulations, diagnose problems, and analyze results.
        
        You have access to a simulation platform with the following capabilities:
        1. Geometry Import (STEP, STL)
        2. Mesh Generation (Tetrahedral, Hexahedral)
        3. Material Selection (Structural Steel, Aluminum, etc.)
        4. Boundary Conditions (Fixed Support, Displacement, Force, Pressure)
        5. Solver (Linear Static Structural)
        
        When the user asks for help, provide clear, step-by-step instructions.
        If the user describes a physical scenario (e.g., "Simulate a beam under load"), 
        suggest specific boundary conditions and material properties.
        
        Please always respond in Chinese.
        """
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": request.user_input}
        ]
        
        if request.context:
            messages.append({"role": "system", "content": f"Current Context: {request.context}"})

        response = get_client().chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            stream=False
        )
        
        content = response.choices[0].message.content
        
        return AIResponse(
            response=content,
            suggested_actions=[] # We can implement structured parsing later
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI Service Error: {str(e)}")

@router.post("/ai/diagnose", response_model=AIResponse)
async def diagnose_simulation(request: AIRequest):
    """
    Diagnose potential issues in the simulation setup.
    """
    try:
        system_prompt = """
        You are a CAE diagnostic expert. Review the user's simulation setup and identify potential issues.
        Common issues to look for:
        - Missing boundary conditions (rigid body motion)
        - Incompatible units
        - Mesh too coarse for detailed features
        - Material properties missing or invalid
        - Load magnitude unrealistic
        
        Analyze the provided context (simulation setup) and give warnings or recommendations.
        Please always respond in Chinese. Provide actionable advice.
        """
        
        context_str = str(request.context) if request.context else "No context provided."
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Please diagnose this setup: {context_str}"}
        ]

        response = get_client().chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            stream=False
        )
        
        content = response.choices[0].message.content
        
        return AIResponse(
            response=content,
            suggested_actions=[]
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI Diagnostic Error: {str(e)}")

@router.post("/ai/configure", response_model=AIResponse)
async def configure_parameters(request: AIRequest):
    """
    Generate simulation parameters based on natural language description.
    Example: "Simulate a steel cantilever beam with 1000N load at the tip."
    """
    try:
        system_prompt = """
        You are an AI that translates natural language engineering requests into structured simulation parameters.
        
        Target Schema (JSON):
        {
            "material_id": "structural_steel" | "aluminum_alloy" | "copper_alloy" | "abs_plastic",
            "mesh_size": float,
            "boundary_conditions": [
                {
                    "name": "string",
                    "type": "fixed" | "force",
                    "applicationType": "face",
                    "entityIndex": int (default to 0 if unsure),
                    "force": {"x": float, "y": float, "z": float} (only for force type)
                }
            ]
        }
        
        Return ONLY the valid JSON object. No explanation.
        """
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": request.user_input}
        ]

        response = get_client().chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            stream=False
        )
        
        content = response.choices[0].message.content
        
        # Simple cleanup to ensure JSON
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
            
        return AIResponse(
            response=content, # This will be the JSON string
            suggested_actions=[] 
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI Configuration Error: {str(e)}")
