import React, { useState } from 'react';
import { X, Check } from 'lucide-react';
import { SolverSettings, SolverType } from '../types';

interface SolverSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSolverSettingsSave: (settings: SolverSettings) => void;
  currentSettings?: SolverSettings | null;
}

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
              <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">FLOW</div>
              <div className="space-y-1">
                <button className="w-full text-left px-3 py-2 rounded text-gray-300 hover:bg-[#2a2f3e] hover:text-white text-sm">Incompressible</button>
                <button className="w-full text-left px-3 py-2 rounded text-gray-300 hover:bg-[#2a2f3e] hover:text-white text-sm">Incompressible (LBM)</button>
                <button className="w-full text-left px-3 py-2 rounded text-gray-300 hover:bg-[#2a2f3e] hover:text-white text-sm">Multi-purpose</button>
                <button className="w-full text-left px-3 py-2 rounded text-gray-300 hover:bg-[#2a2f3e] hover:text-white text-sm">Pedestrian Wind Comfort</button>
                <button className="w-full text-left px-3 py-2 rounded text-gray-300 hover:bg-[#2a2f3e] hover:text-white text-sm">Compressible</button>
                <button className="w-full text-left px-3 py-2 rounded text-gray-300 hover:bg-[#2a2f3e] hover:text-white text-sm">Convective Heat Transfer</button>
                <button className="w-full text-left px-3 py-2 rounded text-gray-300 hover:bg-[#2a2f3e] hover:text-white text-sm">Conjugate Heat Transfer</button>
                <button className="w-full text-left px-3 py-2 rounded text-gray-300 hover:bg-[#2a2f3e] hover:text-white text-sm">Conjugate Heat Transfer (IBM)</button>
                <button className="w-full text-left px-3 py-2 rounded text-gray-300 hover:bg-[#2a2f3e] hover:text-white text-sm">Multiphase</button>
              </div>
            </div>

            <div>
              <div className="text-xs font-semibold text-blue-400 uppercase tracking-wider mb-2">STRUCTURAL</div>
              <div className="space-y-1">
                <button className="w-full text-left px-3 py-2 rounded bg-[#2a2f3e] text-white font-medium text-sm border-l-2 border-blue-500">Static</button>
                <button className="w-full text-left px-3 py-2 rounded text-gray-300 hover:bg-[#2a2f3e] hover:text-white text-sm">Dynamic</button>
                <button className="w-full text-left px-3 py-2 rounded text-gray-300 hover:bg-[#2a2f3e] hover:text-white text-sm">Heat Transfer</button>
                <button className="w-full text-left px-3 py-2 rounded text-gray-300 hover:bg-[#2a2f3e] hover:text-white text-sm">Thermomechanical</button>
                <button className="w-full text-left px-3 py-2 rounded text-gray-300 hover:bg-[#2a2f3e] hover:text-white text-sm">Harmonic</button>
                <button className="w-full text-left px-3 py-2 rounded text-gray-300 hover:bg-[#2a2f3e] hover:text-white text-sm">Frequency Analysis</button>
              </div>
            </div>

            <div>
              <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">ELECTROMAGNETIC</div>
              <div className="space-y-1">
                <button className="w-full text-left px-3 py-2 rounded text-gray-300 hover:bg-[#2a2f3e] hover:text-white text-sm">Electromagnetics</button>
              </div>
            </div>
          </div>

          {/* Main Details Panel */}
          <div className="w-2/3 p-8 flex flex-col bg-[#1a1e2c]">
            <div className="flex-1">
              <h3 className="text-2xl font-bold text-white mb-2">Static Analysis</h3>
              <p className="text-gray-400 text-sm mb-8 leading-relaxed max-w-lg">
                Determine displacements, stresses, and strains in structures or components caused by constraints and loads that don't take damping effects into account. Both linear and nonlinear included.
                <a href="#" className="text-blue-400 ml-2 hover:underline">Learn more</a>
              </p>

              <div className="flex space-x-3 mb-8">
                 <span className="px-3 py-1 bg-[#222736] text-xs font-medium text-gray-300 rounded border border-[#333844]">LINEAR</span>
                 <span className="px-3 py-1 bg-[#222736] text-xs font-medium text-gray-300 rounded border border-[#333844]">NONLINEAR</span>
                 <span className="px-3 py-1 bg-[#222736] text-xs font-medium text-gray-300 rounded border border-[#333844]">SNAP-FIT</span>
                 <span className="px-3 py-1 bg-[#222736] text-xs font-medium text-gray-300 rounded border border-[#333844]">SOLID</span>
                 <span className="px-3 py-1 bg-[#222736] text-xs font-medium text-gray-300 rounded border border-[#333844]">STEADY LOADS</span>
              </div>
            </div>
            
            <div className="mt-auto border-t border-[#333844] pt-6 flex justify-between items-center">
               <div className="text-sm text-gray-400">
                  Need help? <a href="#" className="text-blue-400 hover:underline">Click here</a> if you are not sure about which option better suits your simulation needs.
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