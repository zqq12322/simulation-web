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
  FileBox,
  Save,
  RefreshCw
} from 'lucide-react';
import axios from 'axios';
import { currentAuthHeaders, isUnauthorized, notifySessionExpired } from '../utils/authApi';
import ImportModal from './ImportModal';
import Scene3D from './Scene3D';
import MaterialSelector from './MaterialSelector';
import BoundaryConditionSelector from './BoundaryConditionSelector';
import MeshSettingsModal from './MeshSettingsModal';
import SolverSettingsModal from './SolverSettingsModal';
import AIAssistantPanel, { AIAssistantPanelRef } from './AIAssistantPanel';
import { Project, Material, AnyBoundaryCondition, MeshSettings, SolverSettings } from '../types';
import { formatFrequency, modeDisplayField } from '../utils/modalModes';
import {
  buildSetupPayload,
  describeSaveStatus,
  restoreSetup,
  setupSignature,
  type SaveStatus,
} from '../utils/projectSetup';
import {
  canEditProject,
  describePermissionNotice,
  describeRole,
} from '../utils/projectsApi';

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
  // 后端在求解时可能忽略/降级某些边界条件；必须展示出来，
  // 否则用户会以为"求解成功"就等于结果可信。
  const [solverWarnings, setSolverWarnings] = useState<string[]>([]);
  // 后台任务进度文案（网格/求解各一份）
  const [meshJobStatus, setMeshJobStatus] = useState<string>('');
  const [solveJobStatus, setSolveJobStatus] = useState<string>('');

  // 模态分析：当前显示的阶次（0 起）。模态结果里每个振型都是一个位移场，
  // 切换阶次只是换掉要显示的位移场，见 handleSelectMode。
  const [selectedMode, setSelectedMode] = useState<number>(0);

  // ---- 项目配置的加载与自动保存 ----
  //
  // 在这之前几何/材料/边界条件/网格与求解设置**全在内存里**：关掉页面就没了，
  // 重新打开项目是一个空白工作台——"项目"这个概念的承诺其实是空的。
  const [setupStatus, setSetupStatus] = useState<SaveStatus>('idle');
  const [setupSavedAt, setSetupSavedAt] = useState<string | null>(null);
  const [setupError, setSetupError] = useState<string | null>(null);
  const [setupLoading, setSetupLoading] = useState(true);
  const [setupWarnings, setSetupWarnings] = useState<string[]>([]);
  /** 上次落库的配置签名；用它判断"有没有真的变"，避免每次重渲染都写库 */
  const lastSavedSignature = React.useRef<string>('');

  /**
   * 当前用户对这个项目的权限。
   *
   * 口径必须与后端一致（`ProjectStore.can_edit`）：owner/editor 能改配置，
   * viewer 与未认领的无主项目不能。界面用同一套规则决定"能不能编辑"，
   * 否则用户会先被允许操作、再被后端 403——那是界面在骗人。
   */
  const canEdit = canEditProject(project);
  const permissionNotice = describePermissionNotice(project);
  const roleLabel = describeRole(project);

  const saveSetup = React.useCallback(async (
    overrides?: Partial<{
      modelName: string | null;
      selectedMaterial: Material | null;
      boundaryConditions: AnyBoundaryCondition[];
      meshSettings: MeshSettings | null;
      solverSettings: SolverSettings | null;
    }>
  ): Promise<boolean> => {
    // 只读用户直接不保存，并且**明确说明原因**：
    // 悄悄不发请求会让用户以为配置存下来了，下次打开才发现是空的。
    if (!canEdit) {
      setSetupStatus('readonly');
      return false;
    }
    const payload = buildSetupPayload({
      modelName: overrides?.modelName !== undefined ? overrides.modelName : modelName,
      selectedMaterial: overrides?.selectedMaterial !== undefined
        ? overrides.selectedMaterial : selectedMaterial,
      boundaryConditions: overrides?.boundaryConditions ?? boundaryConditions,
      meshSettings: overrides?.meshSettings !== undefined
        ? overrides.meshSettings : meshSettings,
      solverSettings: overrides?.solverSettings !== undefined
        ? overrides.solverSettings : solverSettings,
    });
    const signature = setupSignature(payload);
    if (signature === lastSavedSignature.current) return true;   // 没变，不必写

    setSetupStatus('saving');
    setSetupError(null);
    try {
      const { data } = await axios.put(
        `${API_BASE_URL}/api/projects/${project.id}/setup`,
        payload,
        { headers: currentAuthHeaders() },
      );
      lastSavedSignature.current = setupSignature(data?.setup ?? payload);
      setSetupSavedAt(data?.savedAt ?? null);
      setSetupStatus('saved');
      return true;
    } catch (error: any) {
      console.error('保存项目配置失败:', error);
      if (isUnauthorized(error)) {
        notifySessionExpired('登录已失效，请重新登录后再保存配置。');
      }
      // **不能显示成"已保存"**：用户会以为配置存下来了，下次打开才发现全丢了
      setSetupStatus('error');
      setSetupError(
        error?.response?.data?.detail || error?.message || '未知错误'
      );
      return false;
    }
  }, [
    API_BASE_URL, project.id, modelName, selectedMaterial,
    boundaryConditions, meshSettings, solverSettings, canEdit,
  ]);

  // 打开项目时恢复配置。**只在项目 id 变化时加载一次**：否则自动保存触发的
  // 状态变化会让这个 effect 反复跑，把用户正在编辑的内容覆盖回旧值。
  React.useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setSetupLoading(true);
      try {
        const { data } = await axios.get(
          `${API_BASE_URL}/api/projects/${project.id}/setup`,
          { headers: currentAuthHeaders() },
        );
        if (cancelled) return;

        const restored = restoreSetup(data?.setup);
        setMeshSettings(restored.meshSettings);
        setSolverSettings(restored.solverSettings);
        setBoundaryConditions(restored.boundaryConditions);
        setModelName(restored.geometryFilename);
        setSetupWarnings(restored.warnings);
        setSetupSavedAt(data?.savedAt ?? null);

        // 材料只有 id：去后端换成完整对象；换不到（材料被删了）就留空并说明
        if (restored.materialId) {
          try {
            const material = await axios.get(
              `${API_BASE_URL}/api/materials/${restored.materialId}`,
              { headers: currentAuthHeaders() },
            );
            if (!cancelled) setSelectedMaterial(material.data);
          } catch {
            if (!cancelled) {
              setSetupWarnings(prev => [
                ...prev,
                `材料 ${restored.materialId} 已不在材料库中，请重新选择。`,
              ]);
            }
          }
        }

        // 记下"刚加载时的样子"：之后只有真正改动才会触发自动保存
        lastSavedSignature.current = setupSignature(
          buildSetupPayload({
            modelName: restored.geometryFilename,
            selectedMaterial: null,
            boundaryConditions: restored.boundaryConditions,
            meshSettings: restored.meshSettings,
            solverSettings: restored.solverSettings,
          })
        );
        setSetupStatus(restored.warnings.length || data?.setup ? 'saved' : 'idle');
      } catch (error: any) {
        if (cancelled) return;
        console.error('加载项目配置失败:', error);
        if (isUnauthorized(error)) {
          notifySessionExpired('登录已失效，请重新登录。');
          return;
        }
        setSetupStatus('error');
        setSetupError(error?.response?.data?.detail || error?.message || '加载失败');
      } finally {
        if (!cancelled) setSetupLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
    // 只依赖项目 id：配置的其余状态由自动保存负责
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [API_BASE_URL, project.id]);

  // 自动保存（防抖 1.5 秒）。配置改变时写回后端，状态条如实显示结果。
  React.useEffect(() => {
    if (setupLoading) return;
    // 只读用户不自动保存（也不显示"未修改"，而是显示"只读 · 不会保存"）
    if (!canEdit) {
      setSetupStatus('readonly');
      return;
    }
    const payload = buildSetupPayload({
      modelName, selectedMaterial, boundaryConditions, meshSettings, solverSettings,
    });
    if (setupSignature(payload) === lastSavedSignature.current) return;

    const timer = setTimeout(() => { void saveSetup(); }, 1500);
    return () => clearTimeout(timer);
  }, [
    setupLoading, canEdit, modelName, selectedMaterial, boundaryConditions,
    meshSettings, solverSettings, saveSetup,
  ]);

  /**
   * 轮询后台任务直到结束。
   *
   * 网格划分与求解是耗时操作：同步接口会让请求超时（大模型上尤其明显），
   * 后端改为 /api/jobs/* —— 提交立即返回 job_id，再轮询状态与结果。
   * 任务本身在单线程工作器里排队执行（gmsh 非线程安全），所以大模型只是
   * "变慢"，不会再超时。
   */
  const pollJob = async (
    jobId: string,
    onStatus?: (text: string) => void
  ): Promise<any> => {
    const deadline = Date.now() + 15 * 60 * 1000; // 最多等 15 分钟
    for (;;) {
      await new Promise((resolve) => setTimeout(resolve, 700));
      const { data } = await axios.get(
        `${API_BASE_URL}/api/jobs/${jobId}`,
        { headers: currentAuthHeaders() }
      );
      onStatus?.(
        data.status === 'queued'
          ? '排队中…'
          : data.status === 'running'
          ? '计算中…'
          : data.status
      );
      if (data.status === 'succeeded') return data.result;
      if (data.status === 'failed') throw new Error(data.error || '任务失败');
      if (Date.now() > deadline) throw new Error('任务超时（超过 15 分钟）');
    }
  };
  
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
            const response = await axios.get(`${API_BASE_URL}/api/materials/${params.material_id}`, {
        headers: currentAuthHeaders(),
      });
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
          const response = await axios.get(
            `${API_BASE_URL}/api/geometry/${modelName}/metadata`,
            { headers: currentAuthHeaders() }
          );
          if (response.data) {
             console.log('Geometry metadata loaded:', response.data);
             setFacesData(response.data.faces || []);
             setEdgesData(response.data.edges || []);
             setVerticesData(response.data.vertices || []);
          }
        } catch (error) {
          console.error("Failed to fetch geometry metadata:", error);
          if (isUnauthorized(error)) notifySessionExpired('登录已失效，请重新登录。');
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

    // 先把配置落库再动耗时操作：网格/求解可能跑几分钟，用户在中途刷新或
    // 换设备时不该丢掉刚配好的东西。（override 的网格设置还没进 state，显式传）
    await saveSetup({ meshSettings: resolvedSettings });

    setIsMeshing(true);
    
    // Update status to meshing
    setMeshSettings(prev => prev ? { ...prev, status: 'meshing' } : { ...resolvedSettings, status: 'meshing' });

    try {
      // 异步任务接口：提交后立即返回 job_id，再轮询。
      // （网格划分/求解是耗时操作，同步接口在大模型上会让请求超时）
      const { data: job } = await axios.post(
        `${API_BASE_URL}/api/jobs/generate-mesh`,
        {
          filename: modelName,
          mesh_size: resolvedSettings.meshSize,
        },
        { headers: currentAuthHeaders() }
      );
      const result = await pollJob(job.job_id, setMeshJobStatus);

      console.log('Mesh generation completed:', result);
      
      // Store real mesh data
      setMeshData(result);
      if (result.faces) {
          setFacesData(result.faces);
      }
      if (result.edges) {
          setEdgesData(result.edges);
      }
      if (result.vertices) {
          setVerticesData(result.vertices);
      }

      // Update status to meshed
      setMeshSettings(prev => prev ? { ...prev, status: 'meshed' } : null);
      
    } catch (error) {
      console.error("Mesh generation failed:", error);
      setMeshSettings(prev => prev ? { ...prev, status: 'failed' } : null);

      // 401 不是"网格划不出来"，而是"你不再是登录状态"：
      // 交给 App 统一清令牌并回登录面板，不要触发 AI 诊断
      // （那会把用户引向一个完全无关的方向）。
      if (isUnauthorized(error)) {
        notifySessionExpired('登录已失效，请重新登录后再生成网格。');
        return;
      }

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

  /**
   * 切换要显示的模态阶次。
   *
   * 模态结果里每阶振型都是一个位移场（后端已按最大位移归一化），
   * 所以"看第 N 阶"就是把这个位移场换进 meshData——着色与变形显示的管线
   * 与结构分析完全相同，只有图例的语义不同（相对量而不是米）。
   */
  const handleSelectMode = (index: number) => {
    setSelectedMode(index);
    setMeshData((prev: any) => {
      if (!prev) return prev;
      const field = modeDisplayField(prev.mode_shapes, index, prev.nodes?.length || 0);
      if (!field) {
        console.warn('振型数据与网格不匹配，无法切换阶次', {
          requested: index,
          modes: prev.mode_shapes?.length,
          nodes: prev.nodes?.length,
        });
        return prev;
      }
      // max_displacement 必须一起更新：变形放大系数按它计算
      return { ...prev, ...field, max_displacement: field.maxDisplacement };
    });
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

    const analysisType = solverSettings.solverType;
    const isThermal = analysisType === 'thermal';
    const isModal = analysisType === 'modal';

    // 未实现的分析类型必须**明确拒绝**，而不是悄悄按结构分析去算。
    // 此前 CFD 会被当成结构静力求解——界面提供了实际不会执行的选项，
    // 用户拿到的结果与所选分析类型无关。
    if (analysisType === 'cfd') {
      alert(
        "Fluid Flow (CFD) 后端尚未实现，无法求解。\n" +
        "请选择 Static Structural / Heat Transfer / Frequency (Modal)。"
      );
      return;
    }

    // 模态分析不要求载荷：自由-自由结构也是合法的（会得到 6 个刚体模态）。
    // 结构分析则必须有载荷，否则结果恒为零，属于"求解成功但没有意义"。
    if (!isModal && !isThermal && boundaryConditions.length === 0) {
      alert("请至少添加一个边界条件（左侧 Boundary conditions → + ）。");
      return;
    }
    if (isThermal && !boundaryConditions.some(bc => bc.type === 'temperature')) {
      alert(
        "热传导分析至少需要一个【温度】边界条件。\n" +
        "未指定的面按绝热处理——如果所有面都绝热，温度场不唯一，问题无解。"
      );
      return;
    }

    // 先把配置落库再提交耗时任务（同网格划分的理由）
    await saveSetup();

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
            if (isThermal && bc.type === 'temperature') {
                // 前端 UI 用摄氏度，后端 API 用开尔文（见 thermal.py 的单位约定）
                return {
                    ...bc,
                    temperature: (typeof bc.temperature === 'number' ? bc.temperature : 25) + 273.15,
                };
            }
            return bc;
        });

        // 异步任务接口：提交后轮询（同网格划分的理由）
        const requestBody: Record<string, any> = {
            geometry_filename: modelName,
            material_id: selectedMaterial?.id || 'structural_steel', // Default if not selected
            boundary_conditions: validBCs,
            faces: facesData, // Pass B-Rep face metadata to solver
            // 几何坐标的长度单位。默认 mm：CAD 零件基本都是毫米，
            // 后端会换算成米再求解，结果与输入单位无关（SI）。
            length_unit: solverSettings?.lengthUnit || 'mm'
        };
        if (isModal) {
            // 阶数来自 SolverSettingsModal 的 "Number of modes" 参数（默认 10）
            const requested = Number(solverSettings?.parameters?.numModes);
            requestBody.num_modes = Number.isFinite(requested) && requested > 0
                ? Math.floor(requested)
                : 6;
        }

        const jobKind = isThermal ? 'thermal' : isModal ? 'modal' : 'solve';
        const { data: job } = await axios.post(
            `${API_BASE_URL}/api/jobs/${jobKind}`,
            requestBody,
            { headers: currentAuthHeaders() }
        );
        const solveResult = await pollJob(job.job_id, setSolveJobStatus);

        console.log('Solver completed:', solveResult);

        if (isThermal) {
            // 热分析：把温度(K)换算成 °C 作为着色场，图例就能标对单位；
            // 色标只依赖相对大小，因此偏移量不影响配色。
            const temperaturesK: number[] = Array.isArray(solveResult?.temperatures)
                ? solveResult.temperatures
                : [];
            setMeshData(prev => ({
                ...prev,
                ...solveResult,
                temperatures_k: temperaturesK,
                scalarField: temperaturesK.map(t => t - 273.15),
                resultKind: 'thermal',
            }));
        } else if (isModal) {
            // 模态分析：默认显示第 1 阶振型；阶次切换见 handleSelectMode。
            // 注意振型是**归一化的相对量**（幅值任意），因此着色场是无量纲的，
            // 图例必须这么标——否则用户会把颜色读成真实位移。
            setSelectedMode(0);
            setMeshData(prev => {
                const field = modeDisplayField(
                    solveResult?.mode_shapes,
                    0,
                    prev?.nodes?.length || 0
                );
                return {
                    ...prev,
                    ...solveResult,
                    resultKind: 'modal',
                    ...(field ? { ...field, max_displacement: field.maxDisplacement } : {}),
                };
            });
            const frequencies: number[] = Array.isArray(solveResult?.frequencies)
                ? solveResult.frequencies
                : [];
            console.log(
                '模态结果：%d 阶，f1 = %s，刚体模态 %d 个',
                frequencies.length,
                formatFrequency(frequencies[0]),
                solveResult?.rigid_body_modes ?? 0
            );
        } else {
            // Merge solver results into meshData (or keep separate)
            // We need to pass stress/displacement to Scene3D
            setMeshData(prev => ({
                ...prev,
                ...solveResult, // displacements, stresses, max_stress, etc.
                resultKind: 'structural',
            }));
        }

        // 展示被忽略/降级的边界条件（若有）
        setSolverWarnings(Array.isArray(solveResult?.warnings) ? solveResult.warnings : []);

        // Update status to solved
        setSolverSettings(prev => prev ? { ...prev, status: 'solved' } : null);
        
      } catch (error: any) {
        console.error("Solver failed:", error);
        setSolverSettings(prev => prev ? { ...prev, status: 'failed' } : null);

        // 与网格划分同理：401 是"登录失效"，不是"算不出来"
        if (isUnauthorized(error)) {
          notifySessionExpired('登录已失效，请重新登录后再求解。');
          return;
        }

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

  // 结果类型决定图例的语义与单位：结构 → Von Mises(Pa)，
  // 热分析 → 温度(°C)，模态 → 相对位移(归一化, 无量纲)。
  // 收敛到一个显式的联合类型，避免把任意字符串透传给 Scene3D。
  const resolvedResultKind: 'structural' | 'thermal' | 'modal' =
    meshData?.resultKind === 'thermal' || meshData?.resultKind === 'modal'
      ? meshData.resultKind
      : 'structural';

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
             <span className="text-xs text-text-secondary leading-tight">
               {roleLabel ? roleLabel : 'Geometries / 1'}
             </span>
          </div>
          {/* 配置保存状态。**必须如实**：保存失败不能显示成"已保存"，
              否则用户会以为配置存下来了，下次打开才发现全丢了。 */}
          <div
            className={`flex items-center gap-1.5 px-2 py-1 rounded text-xs border ${
              setupStatus === 'error'
                ? 'bg-red-500/10 text-red-300 border-red-500/40'
                : setupStatus === 'saving'
                ? 'bg-yellow-500/10 text-yellow-200 border-yellow-500/30'
                : 'bg-white/5 text-text-secondary border-border'
            }`}
            title={setupError || '项目配置会自动保存到后端'}
          >
            {setupStatus === 'saving'
              ? <RefreshCw size={11} className="animate-spin" />
              : <Save size={11} />}
            {setupLoading
              ? '加载配置中…'
              : describeSaveStatus(setupStatus, { savedAt: setupSavedAt, error: setupError })}
            {setupStatus === 'error' && (
              <button
                onClick={() => void saveSetup()}
                className="ml-1 underline hover:text-white"
              >
                重试
              </button>
            )}
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
                   {isMeshing && <span className="text-yellow-500 flex items-center"><Activity size={12} className="mr-1 animate-pulse" /> 网格 {meshJobStatus || '处理中…'}</span>}
                   {isSolving && <span className="text-blue-500 flex items-center"><Activity size={12} className="mr-1 animate-pulse" /> 求解 {solveJobStatus || '处理中…'}</span>}
                   {!isMeshing && !isSolving && <span>Idle</span>}
                </div>
             </div>
          </div>
        </aside>

        {/* 3D Viewport Area */}
        <main className="flex-1 relative bg-[#050505]">

          {/* 权限提示：只读/仅编辑时必须在**进来就**说清楚，
              而不是等用户配了半小时才在保存时被拒绝 */}
          {permissionNotice && (
            <div className={`absolute top-4 left-1/2 -translate-x-1/2 z-20 max-w-2xl w-[90%] backdrop-blur rounded-md p-3 shadow-lg border ${
              canEdit
                ? 'bg-accent-purple/15 border-accent-purple/50'
                : 'bg-orange-500/15 border-orange-500/50'
            }`}>
              <div className="flex items-start gap-2">
                <i className={`fas ${canEdit ? 'fa-share-nodes' : 'fa-lock'} mt-0.5 ${
                  canEdit ? 'text-purple-300' : 'text-orange-400'
                }`}></i>
                <div className={`flex-1 text-xs space-y-1 ${
                  canEdit ? 'text-purple-100' : 'text-orange-100'
                }`}>
                  {permissionNotice.replace(/\*\*/g, '')}
                </div>
              </div>
            </div>
          )}

          {/* 求解警告：被忽略/降级的边界条件必须让用户看见 */}
          {solverWarnings.length > 0 && (
            <div className="absolute top-4 left-1/2 -translate-x-1/2 z-20 max-w-2xl w-[90%] bg-yellow-500/15 border border-yellow-500/50 backdrop-blur rounded-md p-3 shadow-lg">
              <div className="flex items-start gap-2">
                <i className="fas fa-triangle-exclamation text-yellow-400 mt-0.5"></i>
                <div className="flex-1 text-xs text-yellow-100 space-y-1">
                  <div className="font-semibold text-yellow-300">
                    求解时有 {solverWarnings.length} 项边界条件未被完整应用，结果可能不符合预期：
                  </div>
                  <ul className="list-disc list-inside space-y-0.5">
                    {solverWarnings.map((warning, index) => (
                      <li key={index}>{warning}</li>
                    ))}
                  </ul>
                </div>
                <button
                  onClick={() => setSolverWarnings([])}
                  className="text-yellow-300/70 hover:text-yellow-100 shrink-0"
                  title="关闭"
                >
                  <X size={14} />
                </button>
              </div>
            </div>
          )}

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
            resultKind={resolvedResultKind}
            modeFrequencies={meshData?.frequencies}
            rigidBodyModes={meshData?.rigid_body_modes}
            selectedMode={selectedMode}
            onSelectMode={handleSelectMode}
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