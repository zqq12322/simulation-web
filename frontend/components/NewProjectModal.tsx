import React, { useState } from 'react';
import { X, Globe, Lock, Wind, Layers, Thermometer, Box, AlertTriangle, RefreshCw } from 'lucide-react';

interface NewProjectModalProps {
  isOpen: boolean;
  onClose: () => void;
  /**
   * 创建项目。现在会真的写到后端数据库，因此是**异步**的：
   * 失败时（后端没起来、名字不合法）必须让弹窗留在原地并说明原因，
   * 而不是关掉弹窗、让用户以为建好了。
   */
  onCreate: (title: string, description: string, type: string, isPrivate: boolean) => void | Promise<void>;
  /** 正在提交中（禁用按钮，避免重复提交） */
  isSubmitting?: boolean;
  /** 提交失败的原因 */
  error?: string | null;
}

type SimulationType = 'CFD' | 'FEA' | 'Thermal' | 'General';

const NewProjectModal: React.FC<NewProjectModalProps> = ({
  isOpen,
  onClose,
  onCreate,
  isSubmitting = false,
  error = null,
}) => {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [isPrivate, setIsPrivate] = useState(true);
  const [selectedType, setSelectedType] = useState<SimulationType>('General');

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (isSubmitting) return;      // 防止双击建出两个项目
    if (!title.trim()) return;     // 空标题后端会 422，这里先挡住
    // 不在成功后立刻清空表单：创建可能失败，失败时用户不该丢掉已经输入的内容。
    // 清空交给父组件的 onClose。
    onCreate(title, description, selectedType, isPrivate);
  };

  const handleClose = () => {
    setTitle('');
    setDescription('');
    setSelectedType('General');
    setIsPrivate(true);
    onClose();
  };

  const simulationTypes = [
    { id: 'General', label: 'General', icon: Box, desc: 'Basic geometry viewer' },
    { id: 'CFD', label: 'Aerodynamics', icon: Wind, desc: 'Incompressible flow' },
    { id: 'FEA', label: 'Structural', icon: Layers, desc: 'Static stress analysis' },
    { id: 'Thermal', label: 'Thermal', icon: Thermometer, desc: 'Heat transfer' },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="bg-[#151a25] border border-border rounded-xl shadow-2xl w-[800px] flex flex-col max-h-[90vh] overflow-hidden transform transition-all scale-100">
        
        {/* Header */}
        <div className="flex justify-between items-center px-8 py-5 border-b border-border bg-[#1a202e]">
          <div>
            <h2 className="text-xl font-bold text-white tracking-wide">Create New Simulation</h2>
            <p className="text-xs text-text-secondary mt-1">Configure your project settings and physics type</p>
          </div>
          <button onClick={onClose} className="text-text-secondary hover:text-white transition-colors bg-white/5 p-2 rounded-full hover:bg-white/10">
            <X size={20} />
          </button>
        </div>

        {/* Body */}
        <div className="flex flex-1 overflow-hidden">
          <form onSubmit={handleSubmit} className="flex-1 flex flex-col">
            <div className="p-8 space-y-8 overflow-y-auto">
              
              {/* Simulation Type Selection */}
              <div className="space-y-3">
                <label className="block text-sm font-medium text-text-secondary uppercase tracking-wider">
                  Select Physics Type
                </label>
                <div className="grid grid-cols-2 gap-4">
                  {simulationTypes.map((type) => {
                    const Icon = type.icon;
                    const isSelected = selectedType === type.id;
                    return (
                      <div 
                        key={type.id}
                        onClick={() => setSelectedType(type.id as SimulationType)}
                        className={`
                          cursor-pointer rounded-lg p-4 border transition-all duration-200 flex items-start gap-4 group
                          ${isSelected 
                            ? 'bg-accent-blue/10 border-accent-blue shadow-[0_0_15px_rgba(0,163,255,0.2)]' 
                            : 'bg-[#0a0e17] border-border hover:border-text-secondary hover:bg-white/5'}
                        `}
                      >
                        <div className={`p-2 rounded-md ${isSelected ? 'bg-accent-blue text-white' : 'bg-secondary text-text-secondary group-hover:text-white'}`}>
                          <Icon size={20} />
                        </div>
                        <div>
                          <h4 className={`text-sm font-semibold ${isSelected ? 'text-white' : 'text-text-primary'}`}>{type.label}</h4>
                          <p className="text-xs text-text-secondary mt-1">{type.desc}</p>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-8">
                {/* Left Column: Details */}
                <div className="space-y-6">
                  {/* Title */}
                  <div className="space-y-2">
                    <label className="block text-sm font-medium text-text-secondary">
                      Project Title <span className="text-accent-blue">*</span>
                    </label>
                    <input 
                      type="text" 
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                      placeholder="e.g. Front Wing Analysis v01"
                      className="w-full bg-[#0a0e17] border border-border rounded-md px-4 py-2.5 text-white focus:outline-none focus:border-accent-blue focus:ring-1 focus:ring-accent-blue transition-all placeholder:text-gray-600"
                      autoFocus
                      required
                    />
                  </div>

                  {/* Description */}
                  <div className="space-y-2">
                    <label className="block text-sm font-medium text-text-secondary">
                      Description <span className="text-xs text-gray-500">(Optional)</span>
                    </label>
                    <textarea 
                      value={description}
                      onChange={(e) => setDescription(e.target.value)}
                      placeholder="Describe the simulation goals..."
                      className="w-full bg-[#0a0e17] border border-border rounded-md px-4 py-2 text-white h-24 resize-none focus:outline-none focus:border-accent-blue focus:ring-1 focus:ring-accent-blue transition-all placeholder:text-gray-600"
                    />
                  </div>
                </div>

                {/* Right Column: Settings */}
                <div className="space-y-6">
                   {/* Visibility */}
                   <div className="space-y-3">
                      <label className="block text-sm font-medium text-text-secondary">
                        Visibility Settings
                      </label>
                      <div className="flex flex-col gap-3">
                        <button 
                          type="button"
                          onClick={() => setIsPrivate(true)}
                          className={`flex items-center gap-3 px-4 py-3 rounded-md border text-left transition-all ${isPrivate ? 'bg-accent-blue/10 border-accent-blue' : 'bg-[#0a0e17] border-border hover:border-text-secondary'}`}
                        >
                          <Lock size={18} className={isPrivate ? 'text-accent-blue' : 'text-text-secondary'} /> 
                          <div>
                            <span className={`block text-sm font-medium ${isPrivate ? 'text-accent-blue' : 'text-text-primary'}`}>Private Project</span>
                            <span className="text-xs text-text-secondary">Only accessible by you and collaborators</span>
                          </div>
                        </button>
                        
                        <button 
                          type="button"
                          onClick={() => setIsPrivate(false)}
                          className={`flex items-center gap-3 px-4 py-3 rounded-md border text-left transition-all ${!isPrivate ? 'bg-accent-blue/10 border-accent-blue' : 'bg-[#0a0e17] border-border hover:border-text-secondary'}`}
                        >
                          <Globe size={18} className={!isPrivate ? 'text-accent-blue' : 'text-text-secondary'} /> 
                          <div>
                            <span className={`block text-sm font-medium ${!isPrivate ? 'text-accent-blue' : 'text-text-primary'}`}>Public Project</span>
                            <span className="text-xs text-text-secondary">Visible to the entire community</span>
                          </div>
                        </button>
                      </div>
                   </div>
                </div>
              </div>
            </div>

            {/* Footer */}
            <div className="p-6 border-t border-border bg-[#1a202e]">
              {/* 创建失败时弹窗留在原地并说明原因，而不是关掉让用户以为建好了 */}
              {error && (
                <div className="mb-4 flex items-start gap-2 bg-red-500/10 border border-red-500/40 rounded-md px-3 py-2">
                  <AlertTriangle size={14} className="text-red-400 mt-0.5 shrink-0" />
                  <span className="text-xs text-red-200">{error}</span>
                </div>
              )}
              <div className="flex justify-between items-center">
              <div className="text-xs text-text-secondary">
                Creating in <span className="text-white font-medium">Personal Workspace</span>
              </div>
              <div className="flex gap-3">
                <button 
                  type="button" 
                  onClick={handleClose}
                  className="px-5 py-2.5 rounded-md text-text-secondary hover:text-white hover:bg-white/5 transition-colors font-medium"
                >
                  Cancel
                </button>
                <button 
                  type="submit"
                  disabled={!title.trim() || isSubmitting}
                  className="px-8 py-2.5 rounded-md bg-gradient-to-r from-accent-blue to-accent-purple text-white font-semibold shadow-lg shadow-accent-blue/20 hover:shadow-accent-blue/40 disabled:opacity-50 disabled:cursor-not-allowed transition-all transform active:scale-95 flex items-center gap-2"
                >
                  {isSubmitting && <RefreshCw size={14} className="animate-spin" />}
                  {isSubmitting ? 'Creating…' : 'Create Project'}
                </button>
              </div>
              </div>
            </div>

          </form>
        </div>
      </div>
    </div>
  );
};

export default NewProjectModal;