import React, { useState } from 'react';
import { MessageSquare, Send, X, Sparkles, AlertTriangle, FileJson } from 'lucide-react';
import axios from 'axios';

export interface AIAssistantPanelRef {
  triggerDiagnostic: (errorMessage?: string) => void;
}

interface AIAssistantPanelProps {
  isOpen: boolean;
  onClose: () => void;
  context: any; // Current simulation context (geometry, materials, etc.)
}

interface Message {
  role: 'user' | 'ai' | 'system';
  content: string;
  type?: 'text' | 'json' | 'diagnostic';
}

const AIAssistantPanel = React.forwardRef<AIAssistantPanelRef, AIAssistantPanelProps>(({ isOpen, onClose, context }, ref) => {
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<Message[]>([
    { role: 'system', content: '我是您的 AI 仿真助手。您可以让我帮您配置参数、诊断问题或分析结果。', type: 'text' },
    { role: 'system', content: '已为您自动加载并运行演示案例。\n您现在看到的是一个受压立方体的应力分布云图。', type: 'text' }
  ]);
  const [loading, setLoading] = useState(false);

  // Auto-trigger demo config if requested
  const handleOneClickDemo = () => {
      // 1. Send user message
      setMessages(prev => [...prev, { role: 'user', content: '请帮我一键配置并运行演示案例' }]);
      
      // 2. Simulate AI thinking
      setLoading(true);
      setTimeout(() => {
          setLoading(false);
          
          // 3. Construct JSON config
          const demoConfig = {
              material_id: 'structural_steel',
              mesh_size: 2.0,
              boundary_conditions: [
                  {
                      name: '固定底面',
                      type: 'fixed',
                      applicationType: 'face',
                      entityIndex: 1, // Face 1 (Left/Bottom depending on orientation)
                      color: '#ff0000'
                  },
                  {
                      name: '顶面压力',
                      type: 'pressure',
                      applicationType: 'face',
                      entityIndex: 2, // Face 2 (Opposite)
                      value: 1000,
                      color: '#00ff00'
                  }
              ]
          };
          
          const jsonContent = JSON.stringify(demoConfig, null, 2);
          
          setMessages(prev => [
              ...prev, 
              { 
                  role: 'ai', 
                  content: jsonContent, 
                  type: 'json' 
              },
              {
                  role: 'system',
                  content: '已生成演示配置。点击上方“应用此配置”即可填入参数。然后请依次点击左侧的【生成网格】和【开始求解】。',
                  type: 'text'
              }
          ]);
      }, 800);
  };

  // Expose triggerDiagnostic method
  React.useImperativeHandle(ref, () => ({
    triggerDiagnostic: (errorMessage?: string) => {
      const prompt = errorMessage 
        ? `仿真求解失败，报错信息如下：\n${errorMessage}\n\n请分析可能的原因并给出修复建议。` 
        : '帮我诊断当前的仿真设置有什么问题';
      
      // Auto-send diagnostic request
      handleSend(prompt, true);
    }
  }));

  const handleSend = async (text: string = input, isSystemTriggered: boolean = false) => {
    if (!text.trim()) return;

    // If triggered by system (error), we might not want to clear user input
    if (!isSystemTriggered) {
        setInput('');
    }
    
    setMessages(prev => [...prev, { role: 'user', content: text }]);
    setLoading(true);

    try {
      // Determine intent based on keywords (simple heuristic for now)
      const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
      let endpoint = `${API_BASE_URL}/api/ai/chat`;
      // Pass history (messages) to the backend for context-aware chat
      let requestData: any = { user_input: text, context: context, history: messages };

      if (text.includes('诊断') || text.includes('检查') || text.includes('问题') || text.includes('失败') || text.includes('报错')) {
        endpoint = `${API_BASE_URL}/api/ai/diagnose`;
      } else if (text.includes('配置') || text.includes('生成参数')) {
        endpoint = `${API_BASE_URL}/api/ai/configure`;
      }

      const response = await axios.post(endpoint, requestData);
      
      let aiContent = response.data.response;
      let msgType: 'text' | 'json' | 'diagnostic' = 'text';

      // Simple type detection
      if (aiContent.trim().startsWith('{')) {
          msgType = 'json';
      } else if (endpoint.includes('diagnose')) {
          msgType = 'diagnostic';
      }

      setMessages(prev => [...prev, { role: 'ai', content: aiContent, type: msgType }]);

    } catch (error) {
      console.error('AI Error:', error);
      setMessages(prev => [...prev, { role: 'system', content: 'AI 服务暂时不可用，请检查后端连接。', type: 'text' }]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  if (!isOpen) return null;

  const handleApplyParams = (content: string) => {
    try {
      // Try to extract JSON from the content
      let jsonStr = content;
      if (content.includes('```json')) {
        jsonStr = content.split('```json')[1].split('```')[0].trim();
      } else if (content.includes('```')) {
        jsonStr = content.split('```')[1].split('```')[0].trim();
      }
      
      const params = JSON.parse(jsonStr);
      console.log("Applying AI params:", params);
      
      // Dispatch custom event for Workbench to listen
      const event = new CustomEvent('apply-ai-params', { detail: params });
      window.dispatchEvent(event);
      
      // Add system message
      setMessages(prev => [...prev, { role: 'system', content: '参数已应用！请检查左侧面板。', type: 'text' }]);
      
    } catch (e) {
      console.error("Failed to parse AI params:", e);
      alert("无法解析参数，请重试");
    }
  };

  return (
    <div className="fixed right-0 top-14 bottom-0 w-96 bg-[#1a1e2c] border-l border-[#333844] shadow-xl flex flex-col z-40 animate-in slide-in-from-right duration-300">
      {/* Header */}
      <div className="h-12 border-b border-[#333844] flex items-center justify-between px-4 bg-[#0d1119]">
        <div className="flex items-center gap-2 text-white">
          <Sparkles className="text-purple-400" size={18} />
          <span className="font-medium">AI 仿真助手</span>
        </div>
        <button onClick={onClose} className="text-gray-400 hover:text-white">
          <X size={18} />
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.map((msg, idx) => (
          <div key={idx} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div 
              className={`
                max-w-[85%] rounded-lg p-3 text-sm 
                ${msg.role === 'user' ? 'bg-blue-600 text-white' : 'bg-[#2d3342] text-gray-200'}
                ${msg.type === 'diagnostic' ? 'border border-yellow-500/50' : ''}
              `}
            >
              {msg.type === 'diagnostic' && (
                <div className="flex items-center gap-2 text-yellow-400 mb-2 border-b border-yellow-500/20 pb-1">
                  <AlertTriangle size={14} />
                  <span className="font-bold">诊断报告</span>
                </div>
              )}
              
              {msg.type === 'json' ? (
                <div className="flex flex-col gap-2">
                  <div className="font-mono text-xs bg-black/30 p-2 rounded overflow-x-auto">
                    <div className="flex items-center gap-2 text-green-400 mb-1">
                      <FileJson size={12} />
                      <span>参数配置</span>
                    </div>
                    <pre>{msg.content}</pre>
                  </div>
                  <button 
                    onClick={() => handleApplyParams(msg.content)}
                    className="self-start px-3 py-1.5 bg-green-600 hover:bg-green-700 text-white rounded text-xs flex items-center gap-1 transition-colors"
                  >
                    <Sparkles size={12} />
                    应用此配置
                  </button>
                </div>
              ) : (
                <div className="whitespace-pre-wrap">{msg.content}</div>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-[#2d3342] text-gray-400 rounded-lg p-3 text-sm flex items-center gap-2">
              <Sparkles className="animate-pulse" size={14} />
              <span>思考中...</span>
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="p-4 border-t border-[#333844] bg-[#0d1119]">
        <div className="relative">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="描述仿真需求或询问问题..."
            className="w-full bg-[#1a1e2c] border border-[#333844] rounded-lg pl-3 pr-10 py-2 text-white text-sm focus:outline-none focus:ring-1 focus:ring-purple-500 resize-none h-10 min-h-[40px] max-h-32"
            rows={1}
          />
          <button 
            onClick={handleSend}
            disabled={!input.trim() || loading}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-purple-400 hover:text-purple-300 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Send size={16} />
          </button>
        </div>
        <div className="mt-2 flex gap-2 overflow-x-auto pb-1 no-scrollbar">
          <button 
            onClick={handleOneClickDemo}
            className="whitespace-nowrap px-2 py-1 bg-[#2d3342] hover:bg-[#3d4451] rounded text-xs text-gray-300 transition-colors"
          >
            🎲 一键配置示例
          </button>
        </div>
      </div>
    </div>
  );
});

export default AIAssistantPanel;
