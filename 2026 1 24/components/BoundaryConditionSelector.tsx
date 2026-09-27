import React, { useState } from 'react';
import { X } from 'lucide-react';
import { BoundaryConditionType, ApplicationType, AnyBoundaryCondition, Vector3 } from '../types';

interface BoundaryConditionSelectorProps {
  isOpen: boolean;
  onClose: () => void;
  onBoundaryConditionSelect: (boundaryCondition: AnyBoundaryCondition) => void;
  selectedEntity?: { type: ApplicationType; index: number };
  editingBoundaryCondition?: AnyBoundaryCondition;
  defaultBcType?: BoundaryConditionType;
}

// 定义每种边界条件类型对应的颜色
const boundaryConditionColors: Record<BoundaryConditionType, string> = {
  fixed: '#ff4444',       // 红色
  displacement: '#ffbb33', // 橙色
  force: '#33b5e5',       // 蓝色
  pressure: '#99cc00',    // 绿色
  temperature: '#aa66cc'  // 紫色
};

const BoundaryConditionSelector: React.FC<BoundaryConditionSelectorProps> = ({
  isOpen,
  onClose,
  onBoundaryConditionSelect,
  selectedEntity,
  editingBoundaryCondition,
  defaultBcType = 'fixed'
}) => {
  // 如果有编辑的边界条件，则使用其数据初始化状态，否则使用默认值或选中的实体
  const [bcType, setBcType] = useState<BoundaryConditionType>(editingBoundaryCondition?.type || defaultBcType);
  const [applicationType, setApplicationType] = useState<ApplicationType>(editingBoundaryCondition?.applicationType || selectedEntity?.type || 'face');
  const [entityIndex, setEntityIndex] = useState<number>(editingBoundaryCondition?.entityIndex || selectedEntity?.index || 0);
  const [name, setName] = useState<string>(editingBoundaryCondition?.name || `${bcType} - ${applicationType} ${entityIndex}`);
  
  // 位移约束参数 (Displacement in mm)
  const [displacement, setDisplacement] = useState<Vector3>(editingBoundaryCondition?.type === 'displacement' ? editingBoundaryCondition.displacement : { x: 0, y: 0, z: 0 });
  const [fixedX, setFixedX] = useState<boolean>(editingBoundaryCondition?.type === 'displacement' ? editingBoundaryCondition.fixedX : true);
  const [fixedY, setFixedY] = useState<boolean>(editingBoundaryCondition?.type === 'displacement' ? editingBoundaryCondition.fixedY : true);
  const [fixedZ, setFixedZ] = useState<boolean>(editingBoundaryCondition?.type === 'displacement' ? editingBoundaryCondition.fixedZ : true);
  
  // 力载荷参数 (Force in Newtons)
  const [force, setForce] = useState<Vector3>(editingBoundaryCondition?.type === 'force' ? editingBoundaryCondition.force : { x: 0, y: -100, z: 0 });
  
  // 压力载荷参数 (Pressure in MPa)
  const [pressure, setPressure] = useState<number>(editingBoundaryCondition?.type === 'pressure' ? editingBoundaryCondition.pressure : 1);
  
  // 温度条件参数 (Temperature in Celsius)
  const [temperature, setTemperature] = useState<number>(editingBoundaryCondition?.type === 'temperature' ? editingBoundaryCondition.temperature : 25);
  
  // 编辑模式下，我们需要保留原始ID
  const isEditing = !!editingBoundaryCondition;

  // 当模态框打开时，重置状态
  React.useEffect(() => {
    if (isOpen) {
      setBcType(editingBoundaryCondition?.type || defaultBcType);
      setApplicationType(editingBoundaryCondition?.applicationType || selectedEntity?.type || 'face');
      setEntityIndex(editingBoundaryCondition?.entityIndex || selectedEntity?.index || 0);
      setName(editingBoundaryCondition?.name || `${editingBoundaryCondition?.type || defaultBcType} - ${editingBoundaryCondition?.applicationType || selectedEntity?.type || 'face'} ${editingBoundaryCondition?.entityIndex || selectedEntity?.index || 0}`);
      
      if (editingBoundaryCondition) {
        if (editingBoundaryCondition.type === 'displacement') {
          setDisplacement(editingBoundaryCondition.displacement);
          setFixedX(editingBoundaryCondition.fixedX);
          setFixedY(editingBoundaryCondition.fixedY);
          setFixedZ(editingBoundaryCondition.fixedZ);
        } else if (editingBoundaryCondition.type === 'force') {
          setForce(editingBoundaryCondition.force);
        } else if (editingBoundaryCondition.type === 'pressure') {
          setPressure(editingBoundaryCondition.pressure);
        } else if (editingBoundaryCondition.type === 'temperature') {
          setTemperature(editingBoundaryCondition.temperature);
        }
      } else {
        // Reset to defaults when adding new
        setDisplacement({ x: 0, y: 0, z: 0 });
        setFixedX(true); setFixedY(true); setFixedZ(true);
        setForce({ x: 0, y: -100, z: 0 });
        setPressure(1);
        setTemperature(25);
      }
    }
  }, [isOpen, editingBoundaryCondition, selectedEntity, defaultBcType]);

  // 当边界条件类型或应用对象类型变化时，更新默认名称
  React.useEffect(() => {
    if (!isEditing) {
      setName(`${bcType} - ${applicationType} ${entityIndex}`);
    }
  }, [bcType, applicationType, entityIndex, isEditing]);

  // 处理表单提交
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    
    let boundaryCondition: AnyBoundaryCondition;
    const id = isEditing ? editingBoundaryCondition!.id : `bc_${Date.now()}`;
    const color = boundaryConditionColors[bcType];
    
    // 根据选择的边界条件类型创建相应的边界条件对象
    switch (bcType) {
      case 'fixed':
        boundaryCondition = {
          id,
          name,
          type: 'fixed',
          applicationType,
          entityIndex,
          color
        };
        break;
      case 'displacement':
        boundaryCondition = {
          id,
          name,
          type: 'displacement',
          applicationType,
          entityIndex,
          color,
          displacement,
          fixedX,
          fixedY,
          fixedZ
        };
        break;
      case 'force':
        boundaryCondition = {
          id,
          name,
          type: 'force',
          applicationType,
          entityIndex,
          color,
          force
        };
        break;
      case 'pressure':
        boundaryCondition = {
          id,
          name,
          type: 'pressure',
          applicationType,
          entityIndex,
          color,
          pressure
        };
        break;
      case 'temperature':
        boundaryCondition = {
          id,
          name,
          type: 'temperature',
          applicationType,
          entityIndex,
          color,
          temperature
        };
        break;
    }
    
    onBoundaryConditionSelect(boundaryCondition as AnyBoundaryCondition);
    onClose();
  };

  // 如果模态框不打开，不渲染任何内容
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50">
      <div className="bg-[#1a1e2c] border border-[#333844] rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        {/* 模态框头部 */}
        <div className="flex items-center justify-between p-4 border-b border-[#333844]">
          <h2 className="text-xl font-bold text-white">{isEditing ? '编辑边界条件' : '设置边界条件'}</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white p-1 rounded-full hover:bg-gray-800 transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* 模态框内容 */}
        <form onSubmit={handleSubmit} className="p-4">
          {/* 基本信息 */}
          <div className="mb-6">
            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-300 mb-1">名称</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full px-3 py-2 bg-[#0d1119] border border-[#333844] rounded text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="输入边界条件名称"
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              {/* 边界条件类型 */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">类型</label>
                <select
                  value={bcType}
                  onChange={(e) => setBcType(e.target.value as BoundaryConditionType)}
                  className="w-full px-3 py-2 bg-[#0d1119] border border-[#333844] rounded text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="fixed">固定约束 (Fixed Support)</option>
                  <option value="displacement">位移约束 (Displacement)</option>
                  <option value="force">力载荷 (Force)</option>
                  <option value="pressure">压力载荷 (Pressure)</option>
                  <option value="temperature">温度条件 (Temperature)</option>
                </select>
              </div>

              {/* 应用对象类型 */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">应用于</label>
                <select
                  value={applicationType}
                  onChange={(e) => setApplicationType(e.target.value as ApplicationType)}
                  className="w-full px-3 py-2 bg-[#0d1119] border border-[#333844] rounded text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="vertex">点 (Vertex)</option>
                  <option value="edge">边 (Edge)</option>
                  <option value="face">面 (Face)</option>
                </select>
              </div>
            </div>

            {/* 实体索引 */}
            <div className="mt-4">
              <label className="block text-sm font-medium text-gray-300 mb-1">实体索引 (Entity Index)</label>
              <input
                type="number"
                value={entityIndex}
                onChange={(e) => setEntityIndex(parseInt(e.target.value))}
                className="w-full px-3 py-2 bg-[#0d1119] border border-[#333844] rounded text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                min="0"
              />
              <p className="text-xs text-gray-500 mt-1">通常在3D视图中点击选择自动填充</p>
            </div>
          </div>

          {/* 根据边界条件类型显示不同的配置表单 */}
          <div className="mb-6">
            <h3 className="text-lg font-semibold text-gray-200 mb-3">配置参数</h3>
            
            {bcType === 'fixed' && (
              <div className="p-4 bg-[#0d1119]/50 rounded border border-[#333844]">
                <p className="text-gray-400">固定约束将限制所选实体在所有方向上的移动 (UX=UY=UZ=0)。</p>
              </div>
            )}

            {bcType === 'displacement' && (
              <div className="space-y-4">
                <div className="grid grid-cols-3 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-1">X方向位移 (mm)</label>
                    <div className="flex items-center">
                      <input
                        type="number"
                        value={displacement.x}
                        onChange={(e) => setDisplacement({ ...displacement, x: parseFloat(e.target.value) || 0 })}
                        disabled={fixedX}
                        className={`flex-1 px-3 py-2 bg-[#0d1119] border border-[#333844] rounded text-white focus:outline-none focus:ring-2 focus:ring-blue-500 ${fixedX ? 'opacity-50 cursor-not-allowed' : ''}`}
                        step="0.1"
                      />
                      <label className="ml-2 inline-flex items-center">
                        <input
                          type="checkbox"
                          checked={fixedX}
                          onChange={(e) => setFixedX(e.target.checked)}
                          className="rounded text-blue-500 focus:ring-blue-500 bg-[#0d1119] border-[#333844]"
                        />
                        <span className="ml-1 text-sm text-gray-300">固定</span>
                      </label>
                    </div>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-1">Y方向位移 (mm)</label>
                    <div className="flex items-center">
                      <input
                        type="number"
                        value={displacement.y}
                        onChange={(e) => setDisplacement({ ...displacement, y: parseFloat(e.target.value) || 0 })}
                        disabled={fixedY}
                        className={`flex-1 px-3 py-2 bg-[#0d1119] border border-[#333844] rounded text-white focus:outline-none focus:ring-2 focus:ring-blue-500 ${fixedY ? 'opacity-50 cursor-not-allowed' : ''}`}
                        step="0.1"
                      />
                      <label className="ml-2 inline-flex items-center">
                        <input
                          type="checkbox"
                          checked={fixedY}
                          onChange={(e) => setFixedY(e.target.checked)}
                          className="rounded text-blue-500 focus:ring-blue-500 bg-[#0d1119] border-[#333844]"
                        />
                        <span className="ml-1 text-sm text-gray-300">固定</span>
                      </label>
                    </div>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-1">Z方向位移 (mm)</label>
                    <div className="flex items-center">
                      <input
                        type="number"
                        value={displacement.z}
                        onChange={(e) => setDisplacement({ ...displacement, z: parseFloat(e.target.value) || 0 })}
                        disabled={fixedZ}
                        className={`flex-1 px-3 py-2 bg-[#0d1119] border border-[#333844] rounded text-white focus:outline-none focus:ring-2 focus:ring-blue-500 ${fixedZ ? 'opacity-50 cursor-not-allowed' : ''}`}
                        step="0.1"
                      />
                      <label className="ml-2 inline-flex items-center">
                        <input
                          type="checkbox"
                          checked={fixedZ}
                          onChange={(e) => setFixedZ(e.target.checked)}
                          className="rounded text-blue-500 focus:ring-blue-500 bg-[#0d1119] border-[#333844]"
                        />
                        <span className="ml-1 text-sm text-gray-300">固定</span>
                      </label>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {bcType === 'force' && (
              <div>
                <div className="grid grid-cols-3 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-1">X方向力 (N)</label>
                    <input
                      type="number"
                      value={force.x}
                      onChange={(e) => setForce({ ...force, x: parseFloat(e.target.value) || 0 })}
                      className="w-full px-3 py-2 bg-[#0d1119] border border-[#333844] rounded text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                      step="1"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-1">Y方向力 (N)</label>
                    <input
                      type="number"
                      value={force.y}
                      onChange={(e) => setForce({ ...force, y: parseFloat(e.target.value) || 0 })}
                      className="w-full px-3 py-2 bg-[#0d1119] border border-[#333844] rounded text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                      step="1"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-1">Z方向力 (N)</label>
                    <input
                      type="number"
                      value={force.z}
                      onChange={(e) => setForce({ ...force, z: parseFloat(e.target.value) || 0 })}
                      className="w-full px-3 py-2 bg-[#0d1119] border border-[#333844] rounded text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                      step="1"
                    />
                  </div>
                </div>
              </div>
            )}

            {bcType === 'pressure' && (
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">压力值 (MPa)</label>
                <input
                  type="number"
                  value={pressure}
                  onChange={(e) => setPressure(parseFloat(e.target.value) || 0)}
                  className="w-full px-3 py-2 bg-[#0d1119] border border-[#333844] rounded text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                  step="0.1"
                />
                <p className="text-xs text-gray-400 mt-1">正压力指向实体内部</p>
              </div>
            )}

            {bcType === 'temperature' && (
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">温度 (°C)</label>
                <input
                  type="number"
                  value={temperature}
                  onChange={(e) => setTemperature(parseFloat(e.target.value) || 0)}
                  className="w-full px-3 py-2 bg-[#0d1119] border border-[#333844] rounded text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                  step="0.1"
                />
              </div>
            )}
          </div>

          {/* 模态框底部 */}
          <div className="flex justify-end gap-3 pt-4 border-t border-[#333844]">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 bg-[#333844] text-white rounded hover:bg-[#3d4451] transition-colors"
            >
              取消
            </button>
            <button
              type="submit"
              className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors"
            >
              {isEditing ? '保存修改' : '应用边界条件'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default BoundaryConditionSelector;
