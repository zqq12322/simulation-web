import React, { useState, useEffect } from 'react';
import { X } from 'lucide-react';
import { SolverSettings, SolverType } from '../types';

interface SolverSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSolverSettingsSave: (settings: SolverSettings) => void;
  currentSettings?: SolverSettings | null;
}

// Only the analysis types the application actually models. These drive the
// sidebar; previously the list was decorative and nothing could be selected.
const ANALYSIS_TYPES: {
  id: SolverType;
  label: string;
  tags: string[];
  description: string;
}[] = [
  {
    id: 'structural',
    label: 'Static Structural',
    tags: ['LINEAR', 'SOLID', 'STEADY LOADS'],
    description:
      "Determine displacements, stresses and strains caused by constraints and loads, without damping effects. This is the analysis type currently solved by the backend.",
  },
  {
    id: 'cfd',
    label: 'Fluid Flow (CFD)',
    tags: ['INCOMPRESSIBLE', 'STEADY', 'TURBULENT'],
    description:
      'Steady incompressible flow for external and internal aerodynamics.',
  },
  {
    id: 'thermal',
    label: 'Heat Transfer',
    tags: ['STEADY', 'CONDUCTION', 'CONVECTION'],
    description:
      'Steady-state temperature distribution and heat flux in solids.',
  },
  {
    id: 'modal',
    label: 'Frequency (Modal)',
    tags: ['EIGENMODES', 'LINEAR'],
    description:
      'Natural frequencies and mode shapes of the structure. Solving this gives a frequency list; click any entry to display that mode shape. Loads do not affect natural frequencies in linear modal analysis, so force/pressure/temperature conditions are ignored (with a warning).',
  },
];

const PARAMETER_LABELS: Record<string, string> = {
  timeStep: 'Time step',
  iterations: 'Max iterations',
  tolerance: 'Tolerance',
  gravity: 'Include gravity',
  turbulenceModel: 'Turbulence model',
  velocityInlet: 'Inlet velocity',
  ambientTemperature: 'Ambient temperature',
  numModes: 'Number of modes',
};

const SolverSettingsModal: React.FC<SolverSettingsModalProps> = ({
  isOpen,
  onClose,
  onSolverSettingsSave,
  currentSettings
}) => {
  const [solverType, setSolverType] = useState<SolverType>(currentSettings?.solverType || 'structural');
  const [solverName, setSolverName] = useState<string>(currentSettings?.solverName || 'default');
  
  // 基础参数状态
  const [basicParameters, setBasicParameters] = useState<{
    [key: string]: number | string | boolean;
  }>(currentSettings?.parameters || {
    timeStep: 0.1,
    iterations: 1000,
    tolerance: 0.001,
    gravity: true
  });

  // 几何坐标的长度单位。默认 mm：CAD 导出的零件基本是毫米，
  // 若按 m 解释会把 10mm 的零件当成 10m，结果差 6 个数量级。
  const [lengthUnit, setLengthUnit] = useState<'m' | 'mm'>(
    currentSettings?.lengthUnit || 'mm'
  );

  // 根据求解器类型获取可用求解器列表
  const getAvailableSolvers = (type: SolverType): string[] => {
    switch (type) {
      case 'structural':
        return ['default', 'explicit', 'implicit'];
      case 'cfd':
        return ['default', 'simple', 'piso', 'rhoCentralFoam'];
      case 'thermal':
        return ['default', 'heatTransfer'];
      case 'modal':
        return ['default', 'eigenSolver'];
      default:
        return ['default'];
    }
  };

  // 根据求解器类型获取默认参数
  const getDefaultParameters = (type: SolverType): {
    [key: string]: number | string | boolean;
  } => {
    switch (type) {
      case 'structural':
        return {
          timeStep: 0.1,
          iterations: 1000,
          tolerance: 0.001,
          gravity: true
        };
      case 'cfd':
        return {
          timeStep: 0.01,
          iterations: 5000,
          tolerance: 0.0001,
          turbulenceModel: 'k-epsilon',
          velocityInlet: 1.0
        };
      case 'thermal':
        return {
          timeStep: 1.0,
          iterations: 2000,
          tolerance: 0.001,
          ambientTemperature: 25.0
        };
      case 'modal':
        return {
          iterations: 1000,
          tolerance: 0.0001,
          numModes: 10
        };
      default:
        return {
          timeStep: 0.1,
          iterations: 1000,
          tolerance: 0.001
        };
    }
  };

  // 处理求解器类型变更
  const handleSolverTypeChange = (type: SolverType) => {
    setSolverType(type);
    setSolverName(getAvailableSolvers(type)[0]);
    setBasicParameters(getDefaultParameters(type));
  };

  // The component stays mounted, so resync local state whenever the modal opens
  // instead of showing whatever was configured the last time.
  useEffect(() => {
    if (!isOpen) return;
    const type = currentSettings?.solverType || 'structural';
    setSolverType(type);
    setSolverName(currentSettings?.solverName || getAvailableSolvers(type)[0]);
    setBasicParameters(currentSettings?.parameters || getDefaultParameters(type));
    setLengthUnit(currentSettings?.lengthUnit || 'mm');
  }, [isOpen, currentSettings]);

  // Analysis type currently shown in the details panel
  const activeAnalysis =
    ANALYSIS_TYPES.find((analysis) => analysis.id === solverType) || ANALYSIS_TYPES[0];

  // 处理参数值变更
  const handleParameterChange = (key: string, value: number | string | boolean) => {
    setBasicParameters(prev => ({
      ...prev,
      [key]: value
    }));
  };

  // 保存求解器设置
  const handleSave = () => {
    const settings: SolverSettings = {
      id: currentSettings?.id || `solver_${Date.now()}`,
      name: `Solver - ${solverType}`,
      solverType,
      solverName,
      parameters: basicParameters,
      // 几何坐标的长度单位；后端据此换算成米（结果一律 SI）
      lengthUnit,
      status: 'configured'
    };
    onSolverSettingsSave(settings);
    onClose();
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50">
      <div className="bg-[#1a1e2c] border border-[#333844] rounded-lg shadow-xl w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-[#333844] bg-[#222736]">
          <h2 className="text-xl font-bold text-white flex items-center">
            <span className="text-blue-400 mr-2">Analysis Type</span>
          </h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white p-1 rounded-full hover:bg-[#333844] transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto flex bg-[#161a25]">
          {/* Sidebar Navigation */}
          <div className="w-1/3 border-r border-[#333844] p-4 space-y-4">
            <div>
              <div className="text-xs font-semibold text-blue-400 uppercase tracking-wider mb-2">STRUCTURAL / FLOW / THERMAL</div>
              <div className="space-y-1">
                {ANALYSIS_TYPES.map((analysis) => {
                  const active = solverType === analysis.id;
                  return (
                    <button
                      key={analysis.id}
                      onClick={() => handleSolverTypeChange(analysis.id)}
                      className={`w-full text-left px-3 py-2 rounded text-sm transition-colors ${
                        active
                          ? 'bg-[#2a2f3e] text-white font-medium border-l-2 border-blue-500'
                          : 'text-gray-300 hover:bg-[#2a2f3e] hover:text-white'
                      }`}
                    >
                      {analysis.label}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="border-t border-[#333844] pt-4">
              <label className="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
                Solver
              </label>
              <select
                value={solverName}
                onChange={(e) => setSolverName(e.target.value)}
                className="w-full px-3 py-2 bg-[#161a25] border border-[#333844] rounded text-white text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                {getAvailableSolvers(solverType).map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            </div>

            <div className="border-t border-[#333844] pt-4">
              <label className="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
                Length unit
              </label>
              <div className="flex gap-2">
                {(['mm', 'm'] as const).map((unit) => (
                  <button
                    key={unit}
                    onClick={() => setLengthUnit(unit)}
                    className={`flex-1 px-3 py-2 rounded text-sm transition-colors ${
                      lengthUnit === unit
                        ? 'bg-[#2a2f3e] text-white border border-blue-500'
                        : 'bg-[#161a25] text-gray-300 border border-[#333844] hover:text-white'
                    }`}
                  >
                    {unit}
                  </button>
                ))}
              </div>
              <p className="mt-2 text-xs text-gray-500 leading-relaxed">
                几何坐标的单位。选错会让结果差好几个数量级：1000 N 作用在 10×10 的面上，
                按 mm 是 10 MPa，按 m 只有 10 Pa。后端会换算成米再求解，结果始终是 SI。
              </p>
            </div>

            <p className="text-xs text-gray-500 leading-relaxed border-t border-[#333844] pt-4">
              后端与界面已实现三种分析类型：
              <span className="text-gray-300 font-medium">Static Structural</span>（线弹性静力）、
              <span className="text-gray-300 font-medium">Heat Transfer</span>（稳态热传导；
              至少需要一个「温度」边界条件，未指定的面按绝热处理）与
              <span className="text-gray-300 font-medium">Frequency (Modal)</span>
              （模态分析；求解后在视口内点选阶次查看振型。载荷不影响固有频率，
              力/压力/温度条件会被忽略并给出提示）。
              <span className="text-yellow-500/90">Fluid Flow (CFD) 尚未实现</span>——
              选中它求解会直接报错，不会拿别的分析结果冒充。
            </p>
          </div>

          {/* Main Details Panel */}
          <div className="w-2/3 p-8 flex flex-col bg-[#1a1e2c] overflow-y-auto">
            <div className="flex-1">
              <h3 className="text-2xl font-bold text-white mb-2">{activeAnalysis.label}</h3>
              <p className="text-gray-400 text-sm mb-6 leading-relaxed max-w-lg">
                {activeAnalysis.description}
              </p>

              <div className="flex flex-wrap gap-3 mb-8">
                {activeAnalysis.tags.map((tag) => (
                  <span key={tag} className="px-3 py-1 bg-[#222736] text-xs font-medium text-gray-300 rounded border border-[#333844]">{tag}</span>
                ))}
              </div>

              {/* Editable solver parameters */}
              <div className="max-w-lg">
                <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
                  Parameters
                </div>
                <div className="space-y-3">
                  {Object.entries(basicParameters).map(([key, value]) => (
                    <div key={key} className="flex items-center justify-between gap-4">
                      <label className="text-sm text-gray-300" htmlFor={`param-${key}`}>
                        {PARAMETER_LABELS[key] || key}
                      </label>
                      {typeof value === 'boolean' ? (
                        <input
                          id={`param-${key}`}
                          type="checkbox"
                          checked={value}
                          onChange={(e) => handleParameterChange(key, e.target.checked)}
                          className="h-4 w-4 accent-blue-500 cursor-pointer"
                        />
                      ) : typeof value === 'number' ? (
                        <input
                          id={`param-${key}`}
                          type="number"
                          step="any"
                          value={value}
                          onChange={(e) => handleParameterChange(key, e.target.value === '' ? 0 : Number(e.target.value))}
                          className="w-40 px-3 py-1.5 bg-[#161a25] border border-[#333844] rounded text-white text-sm text-right focus:outline-none focus:ring-1 focus:ring-blue-500"
                        />
                      ) : (
                        <input
                          id={`param-${key}`}
                          type="text"
                          value={String(value)}
                          onChange={(e) => handleParameterChange(key, e.target.value)}
                          className="w-40 px-3 py-1.5 bg-[#161a25] border border-[#333844] rounded text-white text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
                        />
                      )}
                    </div>
                  ))}
                  {Object.keys(basicParameters).length === 0 && (
                    <p className="text-sm text-gray-500 italic">该分析类型没有可配置参数。</p>
                  )}
                </div>
              </div>
            </div>
            
            <div className="mt-8 border-t border-[#333844] pt-6 flex justify-between items-center">
               <div className="text-sm text-gray-400">
                  Need help? Check the AI 助手 panel if you are not sure which option suits your simulation.
               </div>
               <div className="flex space-x-3">
                 <button 
                   onClick={onClose}
                   className="px-6 py-2 rounded text-gray-300 hover:bg-[#333844] transition-colors"
                 >
                   Cancel
                 </button>
                 <button 
                   onClick={handleSave}
                   className="px-6 py-2 rounded bg-blue-600 hover:bg-blue-500 text-white font-medium transition-colors shadow-lg shadow-blue-500/20"
                 >
                   Create Simulation
                 </button>
               </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default SolverSettingsModal;