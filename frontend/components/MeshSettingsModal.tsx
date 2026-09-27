import React, { useState, useEffect } from 'react';
import { X, Check, Plus, Trash2 } from 'lucide-react';
import { MeshSettings } from '../types';

interface MeshSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onMeshSettingsSave: (settings: MeshSettings) => void;
  currentSettings?: MeshSettings | null;
  selectedEntity?: { type: 'face' | 'edge' | 'vertex'; index: number } | null;
}

const MeshSettingsModal: React.FC<MeshSettingsModalProps> = ({
  isOpen,
  onClose,
  onMeshSettingsSave,
  currentSettings,
  selectedEntity
}) => {
  const [meshType, setMeshType] = useState<MeshSettings['meshType']>(currentSettings?.meshType || 'tetrahedral');
  const [meshSize, setMeshSize] = useState<number>(currentSettings?.meshSize || 1.0);
  const [quality, setQuality] = useState<number>(currentSettings?.quality || 0.8);
  const [refinementRegions, setRefinementRegions] = useState<MeshSettings['refinementRegions']>(currentSettings?.refinementRegions || []);
  const [regionEntityType, setRegionEntityType] = useState<'face' | 'edge' | 'vertex'>(selectedEntity?.type || 'face');
  const [regionEntityIndex, setRegionEntityIndex] = useState<number>(selectedEntity?.index || 0);
  const [regionRefinementLevel, setRegionRefinementLevel] = useState<number>(1);

  // The component stays mounted, so the local state must be resynced every time
  // the modal is (re)opened; otherwise it would show stale settings.
  useEffect(() => {
    if (!isOpen) return;
    setMeshType(currentSettings?.meshType || 'tetrahedral');
    setMeshSize(currentSettings?.meshSize || 1.0);
    setQuality(currentSettings?.quality || 0.8);
    setRefinementRegions(currentSettings?.refinementRegions || []);
    setRegionEntityType(selectedEntity?.type || 'face');
    setRegionEntityIndex(selectedEntity?.index || 0);
  }, [isOpen, currentSettings, selectedEntity]);

  const handleAddRefinementRegion = () => {
    const newRegion = {
      entityType: regionEntityType,
      entityIndex: regionEntityIndex,
      refinementLevel: regionRefinementLevel
    };
    setRefinementRegions([...refinementRegions, newRegion]);
  };

  const handleRemoveRefinementRegion = (index: number) => {
    setRefinementRegions(refinementRegions.filter((_, i) => i !== index));
  };

  const handleSave = () => {
    console.log('Saving mesh settings with values:');
    console.log('meshType:', meshType);
    console.log('meshSize:', meshSize);
    console.log('quality:', quality);
    console.log('refinementRegions:', refinementRegions);
    
    const settings: MeshSettings = {
      id: currentSettings?.id || `mesh_${Date.now()}`,
      name: `Mesh Settings - ${meshType}`,
      meshType,
      meshSize,
      refinementRegions,
      quality,
      status: 'not_meshed'
    };
    
    console.log('Final mesh settings object:', settings);
    
    onMeshSettingsSave(settings);
    onClose();
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50">
      <div className="bg-[#1a1e2c] border border-[#333844] rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-[#333844]">
          <h2 className="text-xl font-bold text-white">网格划分设置</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white p-1 rounded-full hover:bg-gray-800 transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Content */}
        <div className="p-4">
          {/* Basic Mesh Settings */}
          <div className="mb-6">
            <h3 className="text-lg font-semibold text-gray-200 mb-3">基本设置</h3>
            
            <div className="grid grid-cols-2 gap-4">
              {/* Mesh Type */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">网格类型</label>
                <select
                  value={meshType}
                  onChange={(e) => setMeshType(e.target.value as MeshSettings['meshType'])}
                  className="w-full px-3 py-2 bg-[#0d1119] border border-[#333844] rounded text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="tetrahedral">四面体</option>
                  <option value="hexahedral">六面体</option>
                  <option value="mixed">混合</option>
                </select>
              </div>

              {/* Mesh Size */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">网格大小</label>
                <input
                  type="number"
                  value={meshSize}
                  onChange={(e) => setMeshSize(parseFloat(e.target.value) || 1.0)}
                  className="w-full px-3 py-2 bg-[#0d1119] border border-[#333844] rounded text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                  min="0.1"
                  max="10.0"
                  step="0.1"
                />
              </div>

              {/* Quality */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">网格质量</label>
                <div className="flex items-center gap-2">
                  <input
                    type="range"
                    value={quality}
                    onChange={(e) => setQuality(parseFloat(e.target.value))}
                    className="flex-1 h-2 bg-[#333844] rounded-lg appearance-none cursor-pointer"
                    min="0.5"
                    max="1.0"
                    step="0.05"
                  />
                  <span className="text-white w-12 text-right">{quality.toFixed(2)}</span>
                </div>
              </div>
            </div>
          </div>

          {/* Refinement Regions */}
          <div className="mb-6">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-lg font-semibold text-gray-200">细化区域</h3>
              <div className="text-sm text-gray-400">
                在特定区域添加网格细化，提高局部精度
              </div>
            </div>

            {/* Current Refinement Regions */}
            {refinementRegions.length > 0 && (
              <div className="mb-4 space-y-2">
                {refinementRegions.map((region, index) => (
                  <div 
                    key={index} 
                    className="flex items-center justify-between p-3 bg-[#0d1119]/50 border border-[#333844] rounded"
                  >
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 rounded-full bg-blue-500/20 flex items-center justify-center text-blue-400">
                        {region.entityType.charAt(0).toUpperCase()}
                      </div>
                      <div>
                        <div className="font-medium text-gray-200">
                          {region.entityType} {region.entityIndex}
                        </div>
                        <div className="text-xs text-gray-400">
                          细化级别: {region.refinementLevel}
                        </div>
                      </div>
                    </div>
                    <button
                      onClick={() => handleRemoveRefinementRegion(index)}
                      className="text-red-400 hover:text-red-300 p-1 rounded hover:bg-red-500/10 transition-colors"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                ))}
              </div>
            )}

            {/* Add Refinement Region Form */}
            <div className="p-4 bg-[#0d1119]/50 border border-dashed border-[#333844] rounded">
              <h4 className="text-sm font-medium text-gray-300 mb-3">添加细化区域</h4>
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="block text-xs text-gray-400 mb-1">实体类型</label>
                  <select
                    value={regionEntityType}
                    onChange={(e) => setRegionEntityType(e.target.value as 'face' | 'edge' | 'vertex')}
                    className="w-full px-2 py-1 bg-[#0d1119] border border-[#333844] rounded text-white text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
                  >
                    <option value="face">面</option>
                    <option value="edge">边</option>
                    <option value="vertex">点</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1">实体索引</label>
                  <input
                    type="number"
                    value={regionEntityIndex}
                    onChange={(e) => setRegionEntityIndex(parseInt(e.target.value))}
                    className="w-full px-2 py-1 bg-[#0d1119] border border-[#333844] rounded text-white text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
                    min="0"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1">细化级别</label>
                  <input
                    type="number"
                    value={regionRefinementLevel}
                    onChange={(e) => setRegionRefinementLevel(parseInt(e.target.value))}
                    className="w-full px-2 py-1 bg-[#0d1119] border border-[#333844] rounded text-white text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
                    min="1"
                    max="5"
                  />
                </div>
              </div>
              <button
                onClick={handleAddRefinementRegion}
                className="mt-3 px-3 py-1.5 bg-blue-600 text-white rounded text-sm hover:bg-blue-500 transition-colors flex items-center gap-1"
              >
                <Plus size={14} />
                添加细化区域
              </button>
            </div>
          </div>

          {/* Mesh Preview Info */}
          <div className="mb-6 p-4 bg-[#0d1119]/50 rounded border border-[#333844]">
            <h3 className="text-lg font-semibold text-gray-200 mb-2">网格预览</h3>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <span className="text-gray-400">预计节点数:</span>
                <span className="ml-2 text-white font-medium">~{Math.round(1000 / meshSize)}</span>
              </div>
              <div>
                <span className="text-gray-400">预计单元数:</span>
                <span className="ml-2 text-white font-medium">~{Math.round(500 / meshSize)}</span>
              </div>
              <div>
                <span className="text-gray-400">预计生成时间:</span>
                <span className="ml-2 text-white font-medium">
                  {meshSize < 0.5 ? '较长' : meshSize < 2.0 ? '中等' : '较短'}
                </span>
              </div>
              <div>
                <span className="text-gray-400">质量等级:</span>
                <span className="ml-2 text-white font-medium">
                  {quality < 0.6 ? '低' : quality < 0.8 ? '中' : '高'}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-3 p-4 border-t border-[#333844]">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-[#333844] text-white rounded hover:bg-[#3d4451] transition-colors"
          >
            取消
          </button>
          <button
            onClick={handleSave}
            className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors"
          >
            保存并生成网格
          </button>
        </div>
      </div>
    </div>
  );
};

export default MeshSettingsModal;
