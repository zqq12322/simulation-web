import React, { useState, useEffect } from 'react';
import { X, Plus, Check, ChevronDown, ChevronUp, RefreshCw, AlertTriangle } from 'lucide-react';
import { Material } from '../types';
import axios from 'axios';
import { currentAuthHeaders, isUnauthorized, notifySessionExpired } from '../utils/authApi';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

interface MaterialSelectorProps {
  isOpen: boolean;
  onClose: () => void;
  onMaterialSelect: (material: Material) => void;
  selectedMaterial: Material | null;
}

const MaterialSelector: React.FC<MaterialSelectorProps> = ({
  isOpen,
  onClose,
  onMaterialSelect,
  selectedMaterial
}) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<'all' | 'metal' | 'plastic' | 'concrete' | 'wood' | 'custom'>('all');
  const [materials, setMaterials] = useState<Material[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCustomMaterialForm, setShowCustomMaterialForm] = useState(false);
  const [customMaterial, setCustomMaterial] = useState<Omit<Material, 'id'>>({
    name: '',
    density: 1000,
    youngsModulus: 1e9,
    poissonsRatio: 0.3,
    color: '#FFFFFF',
    type: 'custom'
  });

  // Fetch materials from backend
  const fetchMaterials = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await axios.get(`${API_BASE_URL}/api/materials`, {
        headers: currentAuthHeaders(),
      });
      setMaterials(response.data);
    } catch (err) {
      console.error("Failed to fetch materials:", err);
      if (isUnauthorized(err)) {
        notifySessionExpired('登录已失效，请重新登录。');
        setError('登录已失效，请重新登录。');
        return;
      }
      // 401 与"后端没起来"是两回事，提示必须分开——否则用户会一直去重启服务
      setError(`无法从后端加载材料库 (${API_BASE_URL})，请确认后端服务已启动。`);
      setMaterials([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      fetchMaterials();
    }
  }, [isOpen]);

  const filteredMaterials = materials.filter(material => {
    const matchesSearch = material.name.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesCategory = selectedCategory === 'all' || material.type === selectedCategory;
    return matchesSearch && matchesCategory;
  });

  const handleCustomMaterialSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const newMaterialPayload = {
        ...customMaterial,
        id: `custom-${Date.now()}`
      };
      
      // Save to backend so it is selectable there as well
      await axios.post(`${API_BASE_URL}/api/materials`, newMaterialPayload, {
        headers: currentAuthHeaders(),
      });
      
      setMaterials([...materials, newMaterialPayload]);
      setShowCustomMaterialForm(false);
      setCustomMaterial({
        name: '',
        density: 1000,
        youngsModulus: 1e9,
        poissonsRatio: 0.3,
        color: '#FFFFFF',
        type: 'custom'
      });
    } catch (err) {
      console.error("Failed to create material:", err);
      if (isUnauthorized(err)) {
        notifySessionExpired('登录已失效，请重新登录后再保存材料。');
        return;
      }
      const detail = (err as any)?.response?.data?.detail;
      alert(detail ? `保存材料失败：${detail}` : "保存自定义材料失败");
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
      <div className="bg-secondary border border-border rounded-lg shadow-xl max-w-3xl w-full max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-border">
          <div className="flex items-center gap-3">
            <h2 className="text-xl font-bold text-white">Material Selection</h2>
            {loading && <RefreshCw className="animate-spin text-accent-blue" size={18} />}
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-white/10 rounded-full text-text-secondary hover:text-white transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Search and Filter */}
        <div className="p-4 border-b border-border">
          <div className="flex flex-col md:flex-row gap-4">
            <div className="flex-1">
              <input
                type="text"
                placeholder="Search materials..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="w-full px-3 py-2 bg-primary border border-border rounded-md text-white placeholder-text-secondary focus:outline-none focus:ring-2 focus:ring-accent-blue"
              />
            </div>
            <div className="md:w-48">
              <select
                value={selectedCategory}
                onChange={(e) => setSelectedCategory(e.target.value as any)}
                className="w-full px-3 py-2 bg-primary border border-border rounded-md text-white focus:outline-none focus:ring-2 focus:ring-accent-blue"
              >
                <option value="all">All Categories</option>
                <option value="metal">Metal</option>
                <option value="plastic">Plastic</option>
                <option value="concrete">Concrete</option>
                <option value="wood">Wood</option>
                <option value="custom">Custom</option>
              </select>
            </div>
          </div>
        </div>

        {/* Material List */}
        <div className="p-4">
          {error && (
            <div className="mb-4 flex items-start gap-2 rounded-md border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-300">
              <AlertTriangle size={16} className="mt-0.5 shrink-0" />
              <div className="flex-1">{error}</div>
              <button
                onClick={fetchMaterials}
                className="shrink-0 rounded border border-red-500/40 px-2 py-1 text-xs hover:bg-red-500/20 transition-colors"
              >
                重试
              </button>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {filteredMaterials.map((material) => (
              <div
                key={material.id}
                onClick={() => onMaterialSelect(material)}
                className={`border rounded-lg p-4 cursor-pointer transition-all ${selectedMaterial?.id === material.id ? 'border-accent-blue bg-accent-blue/10' : 'border-border hover:border-accent-blue/50'}`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div
                      className="w-10 h-10 rounded-full"
                      style={{ backgroundColor: material.color }}
                    />
                    <div>
                      <h3 className="font-semibold text-white">{material.name}</h3>
                      <p className="text-xs text-text-secondary capitalize">{material.type}</p>
                    </div>
                  </div>
                  {selectedMaterial?.id === material.id && (
                    <Check size={18} className="text-accent-blue" />
                  )}
                </div>
                
                {/* Material Properties */}
                <div className="mt-3 space-y-1">
                  <div className="flex justify-between text-sm">
                    <span className="text-text-secondary">Density:</span>
                    <span className="text-white">{material.density} kg/m³</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-text-secondary">Young's Modulus:</span>
                    <span className="text-white">{(material.youngsModulus / 1e9).toFixed(1)} GPa</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-text-secondary">Poisson's Ratio:</span>
                    <span className="text-white">{material.poissonsRatio.toFixed(2)}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>

          {!loading && !error && filteredMaterials.length === 0 && (
            <div className="py-8 text-center text-sm text-text-secondary italic">
              {materials.length === 0 ? '材料库为空。' : '没有符合筛选条件的材料。'}
            </div>
          )}
        </div>

        {/* Add Custom Material Button */}
        <div className="p-4 border-t border-border">
          <button
            onClick={() => setShowCustomMaterialForm(!showCustomMaterialForm)}
            className="flex items-center justify-between w-full px-4 py-3 bg-primary border border-dashed border-border rounded-md text-text-secondary hover:text-white hover:border-accent-blue transition-colors"
          >
            <div className="flex items-center gap-2">
              <Plus size={16} />
              <span>Create Custom Material</span>
            </div>
            {showCustomMaterialForm ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </button>

          {/* Custom Material Form */}
          {showCustomMaterialForm && (
            <form onSubmit={handleCustomMaterialSubmit} className="mt-4 space-y-3">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-text-secondary mb-1">Name</label>
                  <input
                    type="text"
                    value={customMaterial.name}
                    onChange={(e) => setCustomMaterial({ ...customMaterial, name: e.target.value })}
                    className="w-full px-3 py-2 bg-primary border border-border rounded-md text-white focus:outline-none focus:ring-2 focus:ring-accent-blue"
                    required
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-text-secondary mb-1">Type</label>
                  <select
                    value={customMaterial.type}
                    onChange={(e) => setCustomMaterial({ ...customMaterial, type: e.target.value as any })}
                    className="w-full px-3 py-2 bg-primary border border-border rounded-md text-white focus:outline-none focus:ring-2 focus:ring-accent-blue"
                  >
                    <option value="metal">Metal</option>
                    <option value="plastic">Plastic</option>
                    <option value="concrete">Concrete</option>
                    <option value="wood">Wood</option>
                    <option value="custom">Custom</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-text-secondary mb-1">Density (kg/m³)</label>
                  <input
                    type="number"
                    value={customMaterial.density}
                    onChange={(e) => setCustomMaterial({ ...customMaterial, density: parseFloat(e.target.value) })}
                    className="w-full px-3 py-2 bg-primary border border-border rounded-md text-white focus:outline-none focus:ring-2 focus:ring-accent-blue"
                    required
                    min="1"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-text-secondary mb-1">Young's Modulus (Pa)</label>
                  <input
                    type="number"
                    value={customMaterial.youngsModulus}
                    onChange={(e) => setCustomMaterial({ ...customMaterial, youngsModulus: parseFloat(e.target.value) })}
                    className="w-full px-3 py-2 bg-primary border border-border rounded-md text-white focus:outline-none focus:ring-2 focus:ring-accent-blue"
                    required
                    min="1"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-text-secondary mb-1">Poisson's Ratio</label>
                  <input
                    type="number"
                    value={customMaterial.poissonsRatio}
                    onChange={(e) => setCustomMaterial({ ...customMaterial, poissonsRatio: parseFloat(e.target.value) })}
                    className="w-full px-3 py-2 bg-primary border border-border rounded-md text-white focus:outline-none focus:ring-2 focus:ring-accent-blue"
                    required
                    min="0"
                    max="0.5"
                    step="0.01"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-text-secondary mb-1">Color</label>
                  <input
                    type="color"
                    value={customMaterial.color}
                    onChange={(e) => setCustomMaterial({ ...customMaterial, color: e.target.value })}
                    className="w-full h-10 p-0 border border-border rounded-md bg-primary cursor-pointer focus:outline-none focus:ring-2 focus:ring-accent-blue"
                  />
                </div>
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => {
                    setShowCustomMaterialForm(false);
                    setCustomMaterial({
                      name: '',
                      density: 1000,
                      youngsModulus: 1e9,
                      poissonsRatio: 0.3,
                      color: '#FFFFFF',
                      type: 'custom'
                    });
                  }}
                  className="px-4 py-2 bg-primary border border-border rounded-md text-text-secondary hover:text-white transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 bg-accent-blue text-white rounded-md hover:bg-accent-blue/90 transition-colors"
                >
                  Create Material
                </button>
              </div>
            </form>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end p-4 border-t border-border gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-primary border border-border rounded-md text-text-secondary hover:text-white transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => {
              if (selectedMaterial) {
                onMaterialSelect(selectedMaterial);
              }
            }}
            disabled={!selectedMaterial}
            className="px-4 py-2 bg-accent-blue text-white rounded-md hover:bg-accent-blue/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            Apply Material
          </button>
        </div>
      </div>
    </div>
  );
};

export default MaterialSelector;
