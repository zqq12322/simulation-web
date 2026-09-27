# 前端（Vite + React 19 + TypeScript）

本目录是 SimCloud AI 的前端。**项目说明、架构、API 清单与开发流程请看仓库根目录的 [`../README.md`](../README.md)。**

```powershell
npm install      # 安装依赖
npm run dev      # 开发服务 http://localhost:3000（需先启动后端 :8000）
npm run build    # 生产构建，输出到 dist/
```

配置：复制 `.env.example` 为 `.env.local` 可覆盖后端地址（`VITE_API_URL`）。
后端默认 `http://localhost:8000`。

> 历史说明：本目录由 Google AI Studio 模板导出（包名 "Copy of SimCloud AI"），
> 因此保留了 `metadata.json` 与 `index.tsx` 作为入口（不是 Vite 默认的 `src/main.tsx`）。
> 模板原有的 Gemini Key 注入配置已移除；AI 能力统一由后端
> `../backend/ai_assistant.py`（DeepSeek）提供。
