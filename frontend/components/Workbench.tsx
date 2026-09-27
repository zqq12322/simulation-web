import React, { useState } from 'react';
import { 
  ChevronLeft, 
  Settings, 
  Share2, 
  HelpCircle, 
  Box,
  Wind,
  Layers,
  Plus,
  Lock,
  X,
  Grid as MeshIcon,
  Play,
  Cpu,
  Sparkles,
  FolderOpen,
  ChevronDown,
  ChevronRight,
  Database,
  Sliders,
  Activity,
  BarChart2,
  FileBox
} from 'lucide-react';
import axios from 'axios';
import ImportModal from './ImportModal';
import Scene3D from './Scene3D';
import MaterialSelector from './MaterialSelector';
import BoundaryConditionSelector from './BoundaryConditionSelector';
import MeshSettingsModal from './MeshSettingsModal';
import SolverSettingsModal from './SolverSettingsModal';
import AIAssistantPanel, { AIAssistantPanelRef } from './AIAssistantPanel';
import { Project, Material, AnyBoundaryCondition, MeshSettings, SolverSettings } from '../types';

interface WorkbenchProps {
  project: Project;
  onBack: () => void;
}

const Workbench: React.FC<WorkbenchProps> = ({ project, onBack }) => {
  const aiAssistantRef = React.useRef<AIAssistantPanelRef>(null);
  const [showImportModal, setShowImportModal] = useState(true); // Show immediately on mount
  const [modelUrl, setModelUrl] = useState<string | null>(null);
  const [modelName, setModelName] = useState<string | null>(null);
  
  // API Base URL - use environment variable for Vercel deployment, fallback to localhost
  const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  // Removed default demo initialization on mount so the workspace starts empty

  const [selectedMaterial, setSelectedMaterial] = useState<Material | null>(null);
  const [showMaterialSelector, setShowMaterialSelector] = useState(false);
  const [boundaryConditions, setBoundaryConditions] = useState<AnyBoundaryCondition[]>([]);
  const [showBoundaryConditionSelector, setShowBoundaryConditionSelector] = useState(false);
  const [bcSelectorDefaultType, setBcSelectorDefaultType] = useState<'fixed' | 'displacement' | 'force' | 'pressure' | 'temperature'>('fixed');
  const [selectedEntity, setSelectedEntity] = useState<{ type: 'face' | 'edge' | 'vertex'; index: number } | null>(null);
  const [editingBoundaryCondition, setEditingBoundaryCondition] = useState<AnyBoundaryCondition | null>(null);
  // Mesh settings state
  const [meshSettings, setMeshSettings] = useState<MeshSettings | null>(null);
  const [showMeshSettingsModal, setShowMeshSettingsModal] = useState(false);
  const [isMeshing, setIsMeshing] = useState(false);
  const [meshData, setMeshData] = useState<any>(null); // Store real mesh data from backend
  const [facesData, setFacesData] = useState<any[]>([]); // Store B-Rep faces data
  const [edgesData, setEdgesData] = useState<any[]>([]); // Store B-Rep edges data
  const [verticesData, setVerticesData] = useState<any[]>([]); // Store B-Rep vertices data
  const [loadedGeometry, setLoadedGeometry] = useState<any>(null); // To force clear previous geometry

  // Solver settings state
  const [solverSettings, setSolverSettings] = useState<SolverSettings | null>(null);
  const [showSolverSettingsModal, setShowSolverSettingsModal] = useState(false);
  const [isSolving, setIsSolving] = useState(false);
  
  // Simulation tree state
  const [expandedNodes, setExpandedNodes] = useState<Record<string, boolean>>({
    'geometries': true,
    'simulations': true,
    'static': true,
    'mesh': true,
    'simulation': true
  });

  const toggleNode = (nodeId: string) => {
    setExpandedNodes(prev => ({
      ...prev,
      [nodeId]: !prev[nodeId]
    }));
  };

  // AI Assistant state
  const [showAIAssistant, setShowAIAssistant] = useState(false);
  
  // Listen for AI parameters application event
  React.useEffect(() => {
    const handleApplyAIParams = async (event: CustomEvent) => {
      const params = event.detail;
      console.log('Applying AI params:', params);
      
      try {
        // 1. Apply Material
        if (params.material_id) {
          try {
            const response = await axios.get(`${API_BASE_URL}/api/materials/${params.material_id}`);
            if (response.data) {
               // Backend returns Material model, frontend expects Material interface
               // They are compatible based on fields
               setSelectedMaterial(response.data);
               console.log('Material applied:', response.data.name);
            }
          } catch (err) {
            console.error('Failed to fetch material:', params.material_id, err);
          }
        }

        // 2. Apply Mesh Settings
        if (params.mesh_size) {
           const newSettings: MeshSettings = {
             id: `mesh_ai_${Date.now()}`,
             name: 'AI Generated Mesh',
             meshType: 'tetrahedral', // Default
             meshSize: parseFloat(params.mesh_size),
             refinementRegions: [],
             quality: 0.8,
             status: 'not_meshed'
           };
           setMeshSettings(newSettings);
           console.log('Mesh settings applied:', newSettings);
        }

        // 3. Apply Boundary Conditions
        if (params.boundary_conditions && Array.isArray(params.boundary_conditions)) {
           const newBCs: AnyBoundaryCondition[] = params.boundary_conditions.map((bc: any, index: number) => {
              
              // Use the schema from backend/ai_assistant.py
              const appType = bc.applicationType || 'face';
              const entityIdx = bc.entityIndex !== undefined ? bc.entityIndex : 1;

              const baseBC = {
                 id: `bc_ai_${Date.now()}_${index}`,
                 name: bc.name || `AI ${bc.type} ${index+1}`,
                 applicationType: appType,
                 entityIndex: entityIdx,
                 color: bc.type === 'fixed' ? '#ff0000' : '#00ff00'
              };

              if (bc.type === 'fixed') {
                 return {
                    ...baseBC,
                    type: 'fixed'
                 } as any;
              } else if (bc.type === 'force') {
                 // Check if force is object {x,y,z} or array [x,y,z] just in case
                 let forceVec = { x: 0, y: -1000, z: 0 };
                 if (bc.force) {
                    if (Array.isArray(bc.force)) {
                        forceVec = { x: bc.force[0], y: bc.force[1], z: bc.force[2] };
                    } else {
                        forceVec = bc.force;
                    }
                 } else if (bc.value) {
                    // Fallback for older prompts
                    if (Array.isArray(bc.value)) {
                        forceVec = { x: bc.value[0], y: bc.value[1], z: bc.value[2] };
                    }
                 }
                 
                 return {
                    ...baseBC,
                    type: 'force',
                    force: forceVec
                 } as any;
              } else if (bc.type === 'pressure') {
                 return {
                    ...baseBC,
                    type: 'pressure',
                    pressure: bc.pressure || (typeof bc.value === 'number' ? bc.value : 1000)
                 } as any;
              }
              return null;
           }).filter(Boolean);

           if (newBCs.length > 0) {
              setBoundaryConditions(newBCs);
              console.log('Boundary conditions applied:', newBCs.length);
           }
        }

        // Open relevant panels or show success message
        alert(`AI配置已应用:\n- 材料: ${params.material_id}\n- 网格大小: ${params.mesh_size}\n- 边界条件: ${params.boundary_conditions?.length || 0}个`);

      } catch (error) {
        console.error('Error applying AI params:', error);
        alert('应用AI配置时出错');
      }
    };

    window.addEventListener('apply-ai-params' as any, handleApplyAIParams as any);

    return () => {
      window.removeEventListener('apply-ai-params' as any, handleApplyAIParams as any);
    };
  }, [API_BASE_URL]);
  
  // Debug state - log isMeshing changes
  React.useEffect(() => {
    console.log('isMeshing status changed:', isMeshing);
  }, [isMeshing]);

  // Fetch geometry metadata when modelName changes
  React.useEffect(() => {
    const fetchMetadata = async () => {
      if (modelName) {
        try {
          const response = await axios.get(`${API_BASE_URL}/api/geometry/${modelName}/metadata`);
          if (response.data) {
             console.log('Geometry metadata loaded:', response.data);
             setFacesData(response.data.faces || []);
             setEdgesData(response.data.edges || []);
             setVerticesData(response.data.vertices || []);
          }
        } catch (error) {
          console.error("Failed to fetch geometry metadata:", error);
        }
      }
    };
    
    fetchMetadata();
  }, [modelName, API_BASE_URL]);

  const handleImport = (file: File, renderFilename?: string) => {
    // Force a re-render by appending a timestamp to prevent browser caching
    const timestamp = new Date().getTime();
    const targetFile = renderFilename || file.name;
    const url = `${API_BASE_URL}/uploads/${targetFile}?t=${timestamp}`;
    console.log("Setting model URL to:", url);
    setModelUrl(url);
    setModelName(file.name); // Keep original name for backend processing
    setFacesData([]); // Clear previous faces
    setEdgesData([]); // Clear previous edges
    setVerticesData([]); // Clear previous vertices
    setLoadedGeometry(null); // Force clearing of previous geometry
    setShowImportModal(false);
    // Show Solver Settings immediately after import
    setShowSolverSettingsModal(true);
  };

  const handleMaterialSelect = (material: Material) => {
    setSelectedMaterial(material);
    setShowMaterialSelector(false);
  };
  
  const handleSelect = (type: 'face' | 'edge' | 'vertex', index: number) => {
    setSelectedEntity({ type, index });
  };

  const handleAddBoundaryConditionFromScene = (type: 'fixed' | 'force') => {
    setBcSelectorDefaultType(type);
    setEditingBoundaryCondition(null);
    setShowBoundaryConditionSelector(true);
  };
  
  const handleBoundaryConditionSelect = (boundaryCondition: AnyBoundaryCondition) => {
    let newBoundaryCondition = boundaryCondition;
    
    // 如果有选中的实体，使用选中实体的类型和索引
    if (selectedEntity) {
      newBoundaryCondition = {
        ...boundaryCondition,
        applicationType: selectedEntity.type,
        entityIndex: selectedEntity.index
      };
    }
    
    if (editingBoundaryCondition) {
      // Edit mode: replace the existing boundary condition
      setBoundaryConditions(prev => 
        prev.map(bc => bc.id === boundaryCondition.id ? newBoundaryCondition : bc)
      );
      setEditingBoundaryCondition(null);
    } else {
      // Add mode: add new boundary condition
      setBoundaryConditions(prev => [...prev, newBoundaryCondition]);
    }
    
    setShowBoundaryConditionSelector(false);
  };

  const handleMeshSettingsSave = (settings: MeshSettings) => {
    console.log('Saving mesh settings:', settings);
    setMeshSettings(settings);
    // Saving the settings now also runs the mesher, so the user cannot end up
    // with configured-but-never-generated mesh (which silently blocked solving).
    handleGenerateMesh(settings);
  };

  const handleGenerateMesh = async (settingsOverride?: MeshSettings) => {
    console.log('Attempting to generate mesh with settings:', settingsOverride || meshSettings);
    
    if (!modelName) {
      alert("请先导入几何模型。");
      return;
    }

    // Settings that were just saved in the modal are not visible in `meshSettings`
    // yet (state updates are async), so prefer the explicit override.
    const resolvedSettings: MeshSettings = settingsOverride || meshSettings || {
      id: `mesh_${Date.now()}`,
      name: 'Default Mesh Settings',
      meshType: 'tetrahedral',
      meshSize: 0.5,
      refinementRegions: [],
      quality: 0.8,
      status: 'not_meshed'
    };
    if (!meshSettings) {
      setMeshSettings(resolvedSettings);
    }

    setIsMeshing(true);
    
    // Update status to meshing
    setMeshSettings(prev => prev ? { ...prev, status: 'meshing' } : { ...resolvedSettings, status: 'meshing' });

    try {
      // Call Backend API
      const response = await axios.post(`${API_BASE_URL}/api/generate-mesh`, null, {
        params: {
          filename: modelName,
          mesh_size: resolvedSettings.meshSize
        }
      });

      console.log('Mesh generation completed:', response.data);
      
      // Store real mesh data
      setMeshData(response.data);
      if (response.data.faces) {
          setFacesData(response.data.faces);
      }
      if (response.data.edges) {
          setEdgesData(response.data.edges);
      }
      if (response.data.vertices) {
          setVerticesData(response.data.vertices);
      }

      // Update status to meshed
      setMeshSettings(prev => prev ? { ...prev, status: 'meshed' } : null);
      
    } catch (error) {
      console.error("Mesh generation failed:", error);
      setMeshSettings(prev => prev ? { ...prev, status: 'failed' } : null);
      
      // Auto-trigger AI diagnostic on failure
      setShowAIAssistant(true);
      setTimeout(() => {
          aiAssistantRef.current?.triggerDiagnostic(`网格划分失败: ${error}`);
      }, 500);
    } finally {
      setIsMeshing(false);
    }
  };

  const handleSolverSettingsSave = (settings: SolverSettings) => {
    setSolverSettings(settings);
    setShowSolverSettingsModal(false); // Make sure modal closes
  };

  const handleSolve = async () => {
    if (!modelName) {
      alert("请先导入几何模型。");
      return;
    }
    if (!solverSettings) {
      alert("请先创建仿真设置（左侧 SIMULATIONS → + ）。");
      setShowSolverSettingsModal(true);
      return;
    }
    if (meshSettings?.status !== 'meshed') {
      alert("请先生成网格（左侧 Mesh → 齿轮图标 → 生成网格）。");
      setShowMeshSettingsModal(true);
      return;
    }
    if (boundaryConditions.length === 0) {
      alert("请至少添加一个边界条件（左侧 Boundary conditions → + ）。");
      return;
    }

    setIsSolving(true);
      
      // Update status to solving
      setSolverSettings(prev => prev ? { ...prev, status: 'solving' } : null);

      try {
        // Construct boundary conditions payload
        // Filter out boundary conditions that are not valid
        const validBCs = boundaryConditions.map(bc => {
            // Ensure values are properly formatted
            if (bc.type === 'force' && typeof bc.force === 'object') {
                return { ...bc, force: bc.force };
            }
            return bc;
        });

        // Call Backend Solver API
        const response = await axios.post(`${API_BASE_URL}/api/solve`, {
            geometry_filename: modelName,
            material_id: selectedMaterial?.id || 'structural_steel', // Default if not selected
            boundary_conditions: validBCs,
            faces: facesData // Pass B-Rep face metadata to solver
        });

        console.log('Solver completed:', response.data);
        
        // Merge solver results into meshData (or keep separate)
        // We need to pass stress/displacement to Scene3D
        setMeshData(prev => ({
            ...prev,
            ...response.data // displacements, stresses, max_stress, etc.
        }));

        // Update status to solved
        setSolverSettings(prev => prev ? { ...prev, status: 'solved' } : null);
        
      } catch (error: any) {
        console.error("Solver failed:", error);
        setSolverSettings(prev => prev ? { ...prev, status: 'failed' } : null);
        
        // Auto-trigger AI diagnostic on failure
        setShowAIAssistant(true);
        // Wait for panel to open
        setTimeout(() => {
            const errorMsg = error.response?.data?.detail || error.message || "Unknown error";
            aiAssistantRef.current?.triggerDiagnostic(errorMsg);
        }, 500);
      } finally {
        setIsSolving(false);
      }
  };

  return (
    <div className="flex flex-col h-screen w-screen bg-primary overflow-hidden">
      
      {/* 1. Workbench Header */}
      <header className="h-14 bg-secondary border-b border-border flex items-center justify-between px-4 shrink-0">
        <div className="flex items-center gap-4">
          <button onClick={onBack} className="p-2 hover:bg-white/5 rounded text-text-secondary hover:text-white transition-colors">
            <ChevronLeft size={20} />
          </button>
          <div className="h-6 w-px bg-border mx-2"></div>
          <div className="flex flex-col">
             <span className="text-sm font-semibold text-white leading-tight">{project.title}</span>
             <span className="text-xs text-text-secondary leading-tight">Geometries / 1</span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button 
            onClick={() => setShowAIAssistant(!showAIAssistant)}
            className={`flex items-center gap-2 px-3 py-1.5 border rounded text-sm transition-all ${
              showAIAssistant 
                ? 'bg-purple-500/20 text-purple-400 border-purple-500/50' 
                : 'bg-transparent border-border text-text-secondary hover:text-white hover:border-text-secondary'
            }`}
          >
            <Sparkles size={14} /> AI 助手
          </button>
          
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-accent-blue to-accent-purple flex items-center justify-center text-xs font-bold shadow-lg shadow-accent-blue/20">
            U
          </div>
        </div>
      </header>

      {/* 2. Main Content */}
      <div className="flex flex-1 overflow-hidden relative">
        
        {/* Left Sidebar (Tree View) */}
        <aside className="w-72 bg-[#1a1e2c] border-r border-[#333844] flex flex-col shrink-0 overflow-y-auto text-gray-300">
          
          {/* GEOMETRIES Section */}
          <div className="border-b border-[#333844] py-1">
            <div 
              className="flex items-center px-2 py-1.5 cursor-pointer hover:bg-[#2a2f3e] group"
              onClick={() => toggleNode('geometries')}
            >
              {expandedNodes['geometries'] ? <ChevronDown size={14} className="mr-1 text-gray-400" /> : <ChevronRight size={14} className="mr-1 text-gray-400" />}
              <span className="text-xs font-bold text-gray-400 tracking-wider">GEOMETRIES</span>
              <button 
                onClick={(e) => { e.stopPropagation(); setShowImportModal(true); }}
                className="ml-auto opacity-0 group-hover:opacity-100 text-blue-400 hover:text-blue-300 transition-opacity"
                title="Add Geometry"
              >
                <Plus size={14} />
              </button>
            </div>
            
            {expandedNodes['geometries'] && (
              <div className="ml-6 mr-2 mb-2">
                {modelName ? (
                  <div className="flex items-center gap-2 px-2 py-1.5 bg-[#2a2f3e] rounded border border-blue-500/30 text-sm mt-1">
                    <Box size={14} className="text-blue-400" />
                    <span className="truncate flex-1 text-white">{modelName.replace(/\.[^/.]+$/, "")}</span>
                  </div>
                ) : (
                  <div className="px-2 py-1.5 text-xs text-gray-500 italic mt-1">
                    No geometry loaded
                  </div>
                )}
              </div>
            )}
          </div>

          {/* SIMULATIONS Section */}
          <div className="py-1 flex-1 flex flex-col">
            <div 
              className="flex items-center px-2 py-1.5 cursor-pointer hover:bg-[#2a2f3e] group"
              onClick={() => toggleNode('simulations')}
            >
              {expandedNodes['simulations'] ? <ChevronDown size={14} className="mr-1 text-gray-400" /> : <ChevronRight size={14} className="mr-1 text-gray-400" />}
              <span className="text-xs font-bold text-gray-400 tracking-wider">SIMULATIONS</span>
              <button 
                onClick={(e) => { e.stopPropagation(); setShowSolverSettingsModal(true); }}
                className="ml-auto opacity-0 group-hover:opacity-100 text-blue-400 hover:text-blue-300 transition-opacity"
                title="Add Simulation"
              >
                <Plus size={14} />
              </button>
            </div>

            {expandedNodes['simulations'] && (
              <div className="ml-3 mt-1 flex-1">
                {/* Static Simulation Node */}
                <div className="border-l border-dashed border-[#333844] ml-2.5 pl-3">
                  <div 
                    className="flex items-center py-1.5 cursor-pointer hover:text-white group -ml-1.5"
                    onClick={() => toggleNode('static')}
                  >
                    {expandedNodes['static'] ? <ChevronDown size={14} className="mr-1 text-gray-400 bg-[#1a1e2c]" /> : <ChevronRight size={14} className="mr-1 text-gray-400 bg-[#1a1e2c]" />}
                    <Activity size={14} className="mr-2 text-green-500" />
                    <span className="text-sm font-medium">{solverSettings?.solverName || 'Static'}</span>
                  </div>

                  {expandedNodes['static'] && (
                    <div className="space-y-0.5 ml-4 mt-1 border-l border-dashed border-[#333844] pl-3">
                      
                      {/* Geometry Link */}
                      <div className="flex items-center py-1 cursor-pointer hover:text-white text-sm group -ml-4">
                        <div className="w-4 border-b border-dashed border-[#333844] mr-1"></div>
                        <Box size={14} className="mr-2 text-blue-400" />
                        <span>Geometry</span>
                        <div className="ml-auto opacity-0 group-hover:opacity-100">
                           <span className="text-xs bg-green-500 text-white rounded-full w-4 h-4 flex items-center justify-center">✓</span>
                        </div>
                      </div>

                      {/* Contacts */}
                      <div className="flex items-center py-1 cursor-pointer hover:text-white text-sm group -ml-4">
                        <div className="w-4 border-b border-dashed border-[#333844] mr-1"></div>
                        <Layers size={14} className="mr-2 text-gray-400" />
                        <span>Contacts</span>
                      </div>

                      {/* Connectors */}
                      <div className="flex items-center py-1 cursor-pointer hover:text-white text-sm group -ml-4">
                        <div className="w-4 border-b border-dashed border-[#333844] mr-1"></div>
                        <Lock size={14} className="mr-2 text-gray-400" />
                        <span>Connectors (0)</span>
                      </div>

                      {/* Element technology */}
                      <div className="flex items-center py-1 cursor-pointer hover:text-white text-sm group -ml-4">
                        <div className="w-4 border-b border-dashed border-[#333844] mr-1"></div>
                        <Cpu size={14} className="mr-2 text-gray-400" />
                        <span>Element technology</span>
                      </div>

                      {/* Model (Materials) */}
                      <div className="flex flex-col py-1 cursor-pointer hover:text-white text-sm group -ml-4">
                        <div className="flex items-center">
                          <div className="w-4 border-b border-dashed border-[#333844] mr-1"></div>
                          <ChevronDown size={14} className="mr-1 text-gray-400" />
                          <FolderOpen size={14} className="mr-2 text-yellow-500" />
                          <span>Model</span>
                        </div>
                        <div className="ml-10 py-1 flex items-center justify-between group/mat">
                           <span className="text-sm">Materials</span>
                           <button 
                             onClick={(e) => { e.stopPropagation(); setShowMaterialSelector(true); }}
                             className="opacity-0 group-hover/mat:opacity-100 text-blue-400 hover:text-blue-300"
                           >
                             <Plus size={14} />
                           </button>
                        </div>
                        {selectedMaterial && (
                           <div className="ml-12 py-0.5 text-xs text-gray-400 flex items-center">
                              <div className="w-2 h-2 rounded-full mr-2" style={{ backgroundColor: selectedMaterial.color }}></div>
                              {selectedMaterial.name}
                           </div>
                        )}
                      </div>

                      {/* Boundary conditions */}
                      <div className="flex flex-col py-1 cursor-pointer hover:text-white text-sm group -ml-4">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center">
                            <div className="w-4 border-b border-dashed border-[#333844] mr-1"></div>
                            {boundaryConditions.length > 0 ? <ChevronDown size={14} className="mr-1 text-gray-400" /> : <ChevronRight size={14} className="mr-1 text-gray-400" />}
                            <Wind size={14} className="mr-2 text-blue-300" />
                            <span>Boundary conditions</span>
                          </div>
                          <button 
                            onClick={(e) => { e.stopPropagation(); setShowBoundaryConditionSelector(true); }}
                            className="opacity-0 group-hover:opacity-100 text-blue-400 hover:text-blue-300 mr-2"
                          >
                            <Plus size={14} />
                          </button>
                        </div>
                        {boundaryConditions.map((bc) => (
                           <div key={bc.id} className="ml-10 py-1 text-xs text-gray-400 flex items-center justify-between pr-2">
                             <span>{bc.name}</span>
                             <button onClick={() => setBoundaryConditions(prev => prev.filter(item => item.id !== bc.id))} className="hover:text-red-400">
                               <X size={12} />
                             </button>
                           </div>
                        ))}
                      </div>

                      {/* Numerics */}
                      <div className="flex items-center py-1 cursor-pointer hover:text-white text-sm group -ml-4">
                        <div className="w-4 border-b border-dashed border-[#333844] mr-1"></div>
                        <Sliders size={14} className="mr-2 text-gray-400" />
                        <span>Numerics</span>
                      </div>

                      {/* Simulation control */}
                      <div className="flex items-center py-1 cursor-pointer hover:text-white text-sm group -ml-4">
                        <div className="w-4 border-b border-dashed border-[#333844] mr-1"></div>
                        <Settings size={14} className="mr-2 text-gray-400" />
                        <span>Simulation control</span>
                      </div>

                      {/* Result control */}
                      <div className="flex items-center py-1 cursor-pointer hover:text-white text-sm group -ml-4">
                        <div className="w-4 border-b border-dashed border-[#333844] mr-1"></div>
                        <BarChart2 size={14} className="mr-2 text-gray-400" />
                        <span>Result control</span>
                      </div>

                      {/* Mesh */}
                      <div className="flex flex-col py-1 cursor-pointer hover:text-white text-sm group -ml-4">
                        <div className="flex items-center justify-between" onClick={() => toggleNode('mesh')}>
                          <div className="flex items-center">
                            <div className="w-4 border-b border-dashed border-[#333844] mr-1"></div>
                            {expandedNodes['mesh'] ? <ChevronDown size={14} className="mr-1 text-gray-400" /> : <ChevronRight size={14} className="mr-1 text-gray-400" />}
                            <MeshIcon size={14} className="mr-2 text-purple-400" />
                            <span>Mesh</span>
                          </div>
                          <div className="flex items-center gap-1 mr-2 opacity-0 group-hover:opacity-100">
                            <button 
                              onClick={(e) => { e.stopPropagation(); handleGenerateMesh(); }}
                              disabled={isMeshing}
                              className="text-purple-400 hover:text-purple-300 disabled:opacity-40"
                              title="生成网格"
                            >
                              <Play size={12} fill="currentColor" />
                            </button>
                            <button 
                              onClick={(e) => { e.stopPropagation(); setShowMeshSettingsModal(true); }}
                              className="text-blue-400 hover:text-blue-300"
                              title="网格设置"
                            >
                              <Settings size={14} />
                            </button>
                          </div>
                        </div>
                        {expandedNodes['mesh'] && meshSettings && (
                          <div className="ml-10 py-1 flex items-center">
                             <span className={`w-2 h-2 rounded-full mr-2 ${meshSettings.status === 'meshed' || meshSettings.status === 'solved' ? 'bg-green-500' : meshSettings.status === 'failed' ? 'bg-red-500' : 'bg-yellow-500'}`}></span>
                             <span className="text-xs text-gray-400">
                               {meshSettings.status === 'meshed' ? 'Meshed' : meshSettings.status === 'solved' ? 'Solved' : meshSettings.status === 'meshing' ? 'Meshing...' : meshSettings.status === 'failed' ? 'Failed' : 'Configured'}
                             </span>
                          </div>
                        )}
                      </div>

                      {/* Simulation Runs */}
                      <div className="flex items-center py-1 cursor-pointer hover:text-white text-sm group -ml-4 mt-2">
                        <div className="w-4 border-b border-dashed border-[#333844] mr-1"></div>
                        {expandedNodes['simulation'] ? <ChevronDown size={14} className="mr-1 text-gray-400" /> : <ChevronRight size={14} className="mr-1 text-gray-400" />}
                        <Database size={14} className="mr-2 text-blue-400" />
                        <span>Simulation Runs</span>
                        <button 
                          onClick={(e) => { e.stopPropagation(); handleSolve(); }}
                          className="ml-auto mr-2 bg-blue-600 hover:bg-blue-500 text-white p-1 rounded transition-colors"
                          title="Run Simulation"
                        >
                          <Play size={12} fill="currentColor" />
                        </button>
                      </div>

                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
          
          {/* Job Status Footer */}
          <div className="border-t border-[#333844] p-3 bg-[#161a25]">
             <div className="flex items-center justify-between text-xs text-gray-400">
                <span className="font-medium">Job status</span>
                <div className="flex items-center gap-2">
                   {isMeshing && <span className="text-yellow-500 flex items-center"><Activity size={12} className="mr-1 animate-pulse" /> Meshing...</span>}
                   {isSolving && <span className="text-blue-500 flex items-center"><Activity size={12} className="mr-1 animate-pulse" /> Solving...</span>}
                   {!isMeshing && !isSolving && <span>Idle</span>}
                </div>
             </div>
          </div>
        </aside>

        {/* 3D Viewport Area */}
        <main className="flex-1 relative bg-[#050505]">
          
          {/* Toolbar inside viewport */}
          <div className="absolute top-4 left-4 z-10 flex gap-2">
             <div className="bg-secondary/90 backdrop-blur border border-border rounded-md flex p-1 shadow-lg">
                <button className="p-2 hover:bg-white/10 rounded text-white" title="Select">
                   <Box size={18} />
                </button>
                <button className="p-2 hover:bg-white/10 rounded text-text-secondary hover:text-white" title="Measure">
                   <Layers size={18} />
                </button>
                <button className="p-2 hover:bg-white/10 rounded text-text-secondary hover:text-white" title="Settings">
                   <Settings size={18} />
                </button>
             </div>
          </div>

          {/* AI Assistant Panel */}
          <AIAssistantPanel 
            ref={aiAssistantRef}
            isOpen={showAIAssistant} 
            onClose={() => setShowAIAssistant(false)}
            context={{
              modelName,
              material: selectedMaterial,
              boundaryConditions,
              meshStatus: meshSettings?.status,
              solverStatus: solverSettings?.status
            }}
          />

          {/* Render the 3D Scene */}
          <Scene3D 
            modelUrl={modelUrl} 
            selectedMaterial={selectedMaterial} 
            boundaryConditions={boundaryConditions}
            onSelect={handleSelect}
            onAddBoundaryCondition={handleAddBoundaryConditionFromScene}
            meshSettings={
              solverSettings?.status === 'solved' && meshSettings
                ? { ...meshSettings, status: 'solved' } 
                : meshSettings
            }
            meshData={meshData}
            faces={facesData}
            edges={edgesData}
            vertices={verticesData}
          />

          {/* Bottom Overlay Info */}
          <div className="absolute bottom-4 right-4 z-10">
             <button className="w-10 h-10 bg-accent-blue text-white rounded-full flex items-center justify-center shadow-lg shadow-accent-blue/30 hover:scale-110 transition-transform">
                <HelpCircle size={20} />
             </button>
          </div>

        </main>
      </div>

      <ImportModal 
        isOpen={showImportModal} 
        onClose={() => setShowImportModal(false)} 
        onImport={handleImport}
      />
      
      <MaterialSelector
        isOpen={showMaterialSelector}
        onClose={() => setShowMaterialSelector(false)}
        onMaterialSelect={handleMaterialSelect}
        selectedMaterial={selectedMaterial}
      />
      
      <BoundaryConditionSelector
        isOpen={showBoundaryConditionSelector}
        onClose={() => {
          setShowBoundaryConditionSelector(false);
          setEditingBoundaryCondition(null);
        }}
        onBoundaryConditionSelect={handleBoundaryConditionSelect}
        selectedEntity={selectedEntity || undefined}
        editingBoundaryCondition={editingBoundaryCondition || undefined}
        defaultBcType={bcSelectorDefaultType}
      />
      
      <MeshSettingsModal
        isOpen={showMeshSettingsModal}
        onClose={() => setShowMeshSettingsModal(false)}
        onMeshSettingsSave={handleMeshSettingsSave}
        currentSettings={meshSettings}
        selectedEntity={selectedEntity}
      />
      
      <SolverSettingsModal
        isOpen={showSolverSettingsModal}
        onClose={() => setShowSolverSettingsModal(false)}
        onSolverSettingsSave={handleSolverSettingsSave}
        currentSettings={solverSettings}
      />
    </div>
  );
};

export default Workbench;