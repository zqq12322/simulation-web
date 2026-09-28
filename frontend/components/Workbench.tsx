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
  RefreshCw,
  TrendingUp,
  FileDown
} from 'lucide-react';
import axios from 'axios';
import { currentAuthHeaders, errorStatus, isUnauthorized, notifySessionExpired } from '../utils/authApi';
import ImportModal from './ImportModal';
import Scene3D from './Scene3D';
import MaterialSelector from './MaterialSelector';
import BoundaryConditionSelector from './BoundaryConditionSelector';
import MeshSettingsModal from './MeshSettingsModal';
import SolverSettingsModal from './SolverSettingsModal';
import AIAssistantPanel, { AIAssistantPanelRef } from './AIAssistantPanel';
import { Project, Material, AnyBoundaryCondition, MeshSettings, SolverSettings } from '../types';
import { formatFrequency, modeDisplayField, firstElasticFrequency } from '../utils/modalModes';
import {
  buildSetupPayload,
  describeSaveStatus,
  restoreSetup,
  setupDocumentVersion,
  setupSignature,
  type SaveStatus,
} from '../utils/projectSetup';
import {
  canEditProject,
  describePermissionNotice,
  describeRole,
} from '../utils/projectsApi';
import {
  ModelLoadError,
  fetchModelBlobUrl,
  renderFilenameFor,
} from '../utils/modelSource';
import { MeshQuality, toMeshQuality } from '../utils/meshQuality';
import MeshQualityPanel from './MeshQualityPanel';
import { ConvergenceStudy, toConvergenceStudy } from '../utils/convergenceStudy';
import { ClipSpec, DEFAULT_CLIP_SPEC } from '../utils/clipPlane';
import ConvergencePanel from './ConvergencePanel';
import { RunAnalysis, RunList, RunRecord, toRunAnalysis, toRunList } from '../utils/runsApi';
import RunHistoryPanel from './RunHistoryPanel';
import {
  buildLegacyVtk,
  buildResultCsv,
  describeExportProblem,
  exportFilename,
  filenameStamp,
} from '../utils/resultExport';

interface WorkbenchProps {
  project: Project;
  onBack: () => void;
}

const Workbench: React.FC<WorkbenchProps> = ({ project, onBack }) => {
  const aiAssistantRef = React.useRef<AIAssistantPanelRef>(null);
  const [showImportModal, setShowImportModal] = useState(true); // Show immediately on mount
  const [modelUrl, setModelUrl] = useState<string | null>(null);
  /** 预览加载失败的说明（视口空白时必须让用户知道为什么） */
  const [previewError, setPreviewError] = useState<string | null>(null);
  /** 当前 blob URL，换模型/卸载时 revoke，避免内存泄漏 */
  const modelBlobUrlRef = React.useRef<{ url: string; revoke: () => void } | null>(null);
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
  // 网格质量（形状质量）：回答用户最常问的"我的网格行不行"。
  // 注意它只说明网格**干净不干净**，"够不够细"要靠收敛性检查（尚未实现），
  // 所以后端结论里那句限定会原样显示，不在前端改写。
  const [meshQuality, setMeshQuality] = useState<MeshQuality | null>(null);
  const [meshQualityLoading, setMeshQualityLoading] = useState(false);
  const [meshQualityError, setMeshQualityError] = useState<string | null>(null);
  /** 自增请求号：用来丢弃过期的网格质量响应（见 loadMeshQuality） */
  const meshQualityRequestRef = React.useRef(0);
  // 收敛检查（逐级加密）：回答"网格够不够细"。
  // 它是**自收敛**——你的模型没有精确解，所以结论只说明"离散误差在减小"，
  // 不说明模型与边界条件正确（后端结论里始终带着这句限定，前端不改写）。
  const [study, setStudy] = useState<ConvergenceStudy | null>(null);
  const [isStudying, setIsStudying] = useState(false);
  const [studyJobStatus, setStudyJobStatus] = useState<string>('');
  const [studyError, setStudyError] = useState<string | null>(null);
  // 求解记录：这个项目跑过哪些算例、各自什么网格、结果多少。
  // 存在后端（SQLite），所以共享给别人的项目里对方也能看到——这正是"协作"
  // 缺的那一块：在此之前被共享者只能看到配置，看不到任何算过的数值。
  const [runs, setRuns] = useState<RunList | null>(null);
  const [runsLoading, setRunsLoading] = useState(false);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [deletingRunId, setDeletingRunId] = useState<string | null>(null);
  /** 跨运行对比（按配置签名分组后的收敛判定）。 */
  const [runAnalysis, setRunAnalysis] = useState<RunAnalysis | null>(null);
  /** 剖切面状态（由工作台持有，Scene3D 只负责渲染）。 */
  const [clipSpec, setClipSpec] = useState<ClipSpec>(DEFAULT_CLIP_SPEC);

  /**
   * 导出结果用的网格与场。
   *
   * 单位按后端的口径写明在**列名里**（`displacement_m`、`von_mises_Pa`、
   * `temperature_K`）：导出文件最常见的用途就是给别人看，而"这个数是米还是
   * 毫米"必须写在文件里，不能靠口头约定。
   *
   * 模态振型是**归一化的无量纲相对量**，所以列名里不能带单位——带上会让人
   * 把颜色/幅值读成真实位移。
   */
  const buildExportPayload = () => {
    const nodes = Array.isArray(meshData?.nodes) ? meshData.nodes : null;
    const elements = Array.isArray(meshData?.elements) ? meshData.elements : null;
    if (!nodes || !elements || nodes.length === 0 || elements.length === 0) return null;

    const resultKind = meshData?.resultKind;
    if (resultKind === 'thermal') {
      const temperatures = Array.isArray(meshData?.temperatures_k)
        ? meshData.temperatures_k
        : [];
      if (temperatures.length !== nodes.length) return null;
      return {
        mesh: { nodes, elements },
        fields: { scalars: [{ name: 'temperature_K', values: temperatures }] },
      };
    }
    if (resultKind === 'modal') {
      const shape = Array.isArray(meshData?.displacements) ? meshData.displacements : [];
      if (shape.length !== nodes.length) return null;
      return {
        mesh: { nodes, elements },
        fields: { vectors: [{ name: 'mode_shape_normalized', values: shape }] },
      };
    }
    const displacements = Array.isArray(meshData?.displacements) ? meshData.displacements : [];
    const stresses = Array.isArray(meshData?.stresses) ? meshData.stresses : [];
    if (displacements.length !== nodes.length || stresses.length !== nodes.length) return null;
    return {
      mesh: { nodes, elements },
      fields: {
        vectors: [{ name: 'displacement_m', values: displacements }],
        scalars: [{ name: 'von_mises_Pa', values: stresses }],
      },
    };
  };

  const exportPayload = buildExportPayload();
  const hasExportableResult = exportPayload !== null;

  /**
   * 把结果写成文件并触发下载。
   *
   * 文本由 `utils/resultExport.ts` 的纯函数生成（可被 node 单独验证，
   * 见 verify 的"真实结果 -> CSV/VTK -> Python 读回逐位比对"），这里只负责
   * Blob + 触发下载这段**必须依赖浏览器**的胶水。
   */
  const handleExportResult = (format: 'csv' | 'vtk') => {
    const problem = describeExportProblem(exportPayload?.mesh, exportPayload?.fields);
    if (problem || !exportPayload) {
      alert(problem || '无法导出：没有可用的结果数据。');
      return;
    }
    const text = format === 'csv'
      ? buildResultCsv(exportPayload.mesh, exportPayload.fields)
      : buildLegacyVtk(exportPayload.mesh, exportPayload.fields);
    if (text === null) {
      alert('导出失败：结果数据不自洽（长度或数值有问题），请看控制台。');
      return;
    }
    const name = exportFilename(modelName, `result_${filenameStamp(new Date())}`, format);
    const blob = new Blob([text], {
      type: format === 'csv' ? 'text/csv;charset=utf-8' : 'text/vtk;charset=utf-8',
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = name;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    // 立刻释放：文件已经在下载队列里了，晚释放只会泄漏内存
    URL.revokeObjectURL(url);
  };
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
  // 乐观并发控制：保存时把这一版号作为 If-Match 回传。
  // 别人先改过就会拿到 409——这时**停止自动保存并提示**，而不是把别人的改动盖掉。
  const [setupVersion, setSetupVersion] = useState<number | null>(null);
  const setupVersionRef = React.useRef<number | null>(null);
  const [setupConflict, setSetupConflict] = useState<string | null>(null);
  /** 递增即可让下面的加载 effect 重跑（冲突后重新加载用） */
  const [setupReloadKey, setSetupReloadKey] = useState(0);
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
      // 带上 If-Match：告诉后端"我是基于哪一版改的"。
      // 期间有人改过就返回 409，而不是把我的改动**静默盖上去**
      //（在此之前，后保存的人会无声抹掉先保存的人的工作）。
      const { data } = await axios.put(
        `${API_BASE_URL}/api/projects/${project.id}/setup`,
        payload,
        {
          headers: {
            ...currentAuthHeaders(),
            ...(setupVersionRef.current !== null
              ? { 'If-Match': `"${setupVersionRef.current}"` }
              : {}),
          },
        },
      );
      setupVersionRef.current = setupDocumentVersion(data);
      if (setupVersionRef.current !== null) setSetupVersion(setupVersionRef.current);
      lastSavedSignature.current = setupSignature(data?.setup ?? payload);
      setSetupSavedAt(data?.savedAt ?? null);
      setSetupConflict(null);
      setSetupStatus('saved');
      return true;
    } catch (error: any) {
      console.error('保存项目配置失败:', error);
      if (isUnauthorized(error)) {
        notifySessionExpired('登录已失效，请重新登录后再保存配置。');
      }
      // 409 = 别人先改了。**必须停止自动保存并说出来**：
      // 继续自动保存只会一次次撞 409，而用户完全不知道发生了什么。
      if (errorStatus(error) === 409) {
        setupVersionRef.current = null;      // 版本未知：后续保存一律先重新加载
        setSetupStatus('conflict');
        setSetupConflict(
          error?.response?.data?.detail
            || '这个项目已被其他人修改，你的改动**没有**保存，请先重新加载。',
        );
        return false;
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
        const loadedVersion = setupDocumentVersion(data);
        setupVersionRef.current = loadedVersion;
        setSetupVersion(loadedVersion);
        setSetupConflict(null);

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

        // 恢复几何预览。
        //
        // 上一轮只恢复了 `modelName`（仿真配置里的名字），**没有取回模型本身**，
        // 于是重新打开项目时视口是空的——"配置都在、就是看不见模型"。
        // `meshData`（节点/单元）没有持久化，所以视口里能显示的只有几何预览；
        // 这里补上它。
        if (restored.geometryFilename) {
          await loadModelPreview(restored.geometryFilename);
        } else {
          setModelUrl(null);
        }

        // 配置里若记着"已划网格"，就把质量也取回来。否则重新打开项目会看到
        // "网格状态是 Meshed、但质量面板一片空白"——和上一轮"视口是空的"
        // 属于同一类问题：状态说有，界面却不给。
        if (
          restored.geometryFilename
          && (restored.meshSettings?.status === 'meshed'
            || restored.meshSettings?.status === 'solved')
        ) {
          void loadMeshQuality(restored.geometryFilename);
        }

        // 求解记录与项目绑定，打开项目时一并读取（被共享的人也能看到属主
        // 算过什么、什么网格、结果多少——这正是共享缺的那一块）。
        void loadRuns();

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
    // 只依赖项目 id 与"重新加载"计数：配置的其余状态由自动保存负责。
    // `setupReloadKey` 用来在冲突之后手动重载服务器版本。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [API_BASE_URL, project.id, setupReloadKey]);

  // 自动保存（防抖 1.5 秒）。配置改变时写回后端，状态条如实显示结果。
  React.useEffect(() => {
    if (setupLoading) return;
    // **冲突期间不自动保存**：这时手里的基线是过期的，继续写只会一次次撞 409，
    // 而用户完全不知道发生了什么。先让他重新加载（或自己决定怎么办）。
    if (setupConflict) return;
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
    setupLoading, canEdit, setupConflict, modelName, selectedMaterial,
    boundaryConditions, meshSettings, solverSettings, saveSetup,
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

  /**
   * 取回几何预览并交给 three.js。
   *
   * 不再用公开的 `/uploads/xxx.stl`（知道文件名就能下载，不需要登录）；
   * 改为带认证头 fetch 到 blob URL（见 utils/modelSource.ts）。
   *
   * 每个 blob URL 都要 revoke：否则每换一次模型就留下一块无法回收的内存
   * （大模型的 STL 有几 MB，切几次就很可观）。
   */
  const loadModelPreview = React.useCallback(async (
    geometryFilename: string,
    renderFilename?: string | null,
  ): Promise<boolean> => {
    // 优先用后端**告诉我们的**预览文件名（上传响应里的 render_filename），
    // 它才是权威来源；只有在恢复已保存的项目时（配置里只存了几何名）
    // 才按后端的规则推导。
    const target = renderFilename || renderFilenameFor(geometryFilename);
    try {
      // 认证头由这里提供（modelSource 刻意不自己去读令牌存储）
      const result = await fetchModelBlobUrl(API_BASE_URL, target, {
        headers: currentAuthHeaders(),
      });
      modelBlobUrlRef.current?.revoke();
      modelBlobUrlRef.current = result;
      setModelUrl(result.url);
      return true;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      console.error('加载几何预览失败:', error);
      if (error instanceof ModelLoadError && error.status === 401) {
        notifySessionExpired(message);
        return false;
      }
      // 预览失败不该让整个工作台不可用（配置、网格、求解都还能做），
      // 但必须说出来——否则用户只会看到一个空视口
      setModelUrl(null);
      setPreviewError(message);
      return false;
    }
  }, [API_BASE_URL]);

  const handleImport = (file: File, renderFilename?: string) => {
    setModelName(file.name); // Keep original name for backend processing
    setFacesData([]); // Clear previous faces
    setEdgesData([]); // Clear previous edges
    setVerticesData([]); // Clear previous vertices
    setLoadedGeometry(null); // Force clearing of previous geometry
    setPreviewError(null);
    // 换了几何，上一个几何的网格质量就没有意义了。不清掉的话，用户会看到
    // "新零件 + 旧网格的质量直方图"，而那是最容易被当成真的假信息。
    setMeshQuality(null);
    setMeshQualityError(null);
    setShowImportModal(false);
    // Show Solver Settings immediately after import
    setShowSolverSettingsModal(true);
    void loadModelPreview(file.name, renderFilename);
  };

  // 卸载时释放 blob URL（否则每打开一个项目都会留下一块无法回收的内存）
  React.useEffect(() => () => {
    modelBlobUrlRef.current?.revoke();
    modelBlobUrlRef.current = null;
  }, []);

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

  /**
   * 拉取网格质量（形状质量）。
   *
   * 两个容易被忽略的细节：
   *
   * 1. **404 / 409 不是故障**：它们分别表示"几何文件不在了"和"还没划网格"，
   *    也就是用户还没生成网格——此时面板应当安静地不出现，而不是报红把人吓一跳。
   * 2. **要防过期响应**：连续改网格尺寸会发出多个请求，先发的可能后到。
   *    这里用自增的请求号丢弃过期响应，否则界面会显示上一次网格的质量
   *    （数字看着很正常，但对应的是已经不存在的网格）。
   */
  const loadMeshQuality = React.useCallback(async (filename: string | null) => {
    const requestId = ++meshQualityRequestRef.current;
    const isCurrent = () => requestId === meshQualityRequestRef.current;

    if (!filename) {
      setMeshQuality(null);
      setMeshQualityError(null);
      setMeshQualityLoading(false);
      return;
    }

    setMeshQualityLoading(true);
    setMeshQualityError(null);
    try {
      const { data } = await axios.get(`${API_BASE_URL}/api/mesh-quality`, {
        params: { filename },
        headers: currentAuthHeaders(),
      });
      if (!isCurrent()) return;
      setMeshQuality(toMeshQuality(data));
    } catch (error) {
      if (!isCurrent()) return;
      if (isUnauthorized(error)) {
        notifySessionExpired('登录已失效，请重新登录后再检查网格质量。');
        return;
      }
      const status = errorStatus(error);
      if (status === 404 || status === 409) {
        setMeshQuality(null);
        return;
      }
      setMeshQuality(null);
      setMeshQualityError(
        status === null
          ? '无法连接后端，网格质量未检查。'
          : `网格质量检查失败（HTTP ${status}）。`,
      );
    } finally {
      if (isCurrent()) setMeshQualityLoading(false);
    }
  }, [API_BASE_URL]);

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

      // 网格刚划完，正好是问"这个网格行不行"的时机
      void loadMeshQuality(modelName);
      
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

  /**
   * 组装求解请求体（结构 / 热 / 模态共用）。
   *
   * 抽出来是为了让「跑一次求解」和「跑收敛检查」用**同一份**配置：收敛检查的
   * 全部意义就在于"各级之间只有网格不同"。两处各写一套的话，很容易在某一处
   * 漏了温度换算或改了单位，于是收敛检查悄悄算的是另一个问题——结论看着
   * 头头是道，其实毫无意义。
   *
   * 返回 `null` 表示前置条件不满足（几何/仿真设置缺失，或选了未实现的 CFD）。
   * 提示用户是调用方的事，这里不做任何弹窗。
   */
  const buildSolverSetup = (): Record<string, any> | null => {
    if (!modelName || !solverSettings) return null;
    const analysisType = solverSettings.solverType;
    if (analysisType === 'cfd') return null;

    const isThermal = analysisType === 'thermal';
    const isModal = analysisType === 'modal';

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

    const setup: Record<string, any> = {
      geometry_filename: modelName,
      material_id: selectedMaterial?.id || 'structural_steel', // Default if not selected
      boundary_conditions: validBCs,
      faces: facesData, // Pass B-Rep face metadata to solver
      // 几何坐标的长度单位。默认 mm：CAD 零件基本都是毫米，
      // 后端会换算成米再求解，结果与输入单位无关（SI）。
      length_unit: solverSettings?.lengthUnit || 'mm',
    };
    if (isModal) {
      // 阶数来自 SolverSettingsModal 的 "Number of modes" 参数（默认 10）
      const requested = Number(solverSettings?.parameters?.numModes);
      setup.num_modes = Number.isFinite(requested) && requested > 0
        ? Math.floor(requested)
        : 6;
    }
    return setup;
  };

  /**
   * 逐级加密的收敛检查（回答"我的网格够不够细"）。
   *
   * 与"跑一次求解"共用 `buildSolverSetup()`：收敛检查的全部意义就在于
   * 各级之间**只有网格不同**。
   *
   * 走异步任务（3~4 次"划网格 + 求解"，同步接口在大模型上必然超时），并且
   * 后端每一步都划在几何的**临时副本**上——不会覆盖你当前的网格。
   */
  const handleConvergenceStudy = async () => {
    if (!modelName) {
      alert("请先导入几何模型。");
      return;
    }
    if (!solverSettings) {
      alert("请先创建仿真设置（左侧 SIMULATIONS → + ）。\n收敛检查要用同一份配置逐级加密求解。");
      setShowSolverSettingsModal(true);
      return;
    }
    if (solverSettings.solverType === 'cfd') {
      alert("Fluid Flow (CFD) 后端尚未实现，无法做收敛检查。");
      return;
    }
    if (meshSettings?.status !== 'meshed') {
      alert("请先生成网格（左侧 Mesh → 齿轮图标 → 生成网格）。\n收敛检查需要一个可用的基础网格尺寸作为起点。");
      setShowMeshSettingsModal(true);
      return;
    }

    const setup = buildSolverSetup();
    if (!setup) {
      alert("仿真配置不完整，无法开始收敛检查。");
      return;
    }

    // 基础网格尺寸取**当前**网格设置：从"你现在用的网格"开始逐级加密，
    // 结论才直接回答"我现在这个网格够不够细"。
    const baseMeshSize = Number(meshSettings?.meshSize);
    await saveSetup();

    setIsStudying(true);
    setStudyError(null);
    setStudyJobStatus('');
    try {
      const { data: job } = await axios.post(
        `${API_BASE_URL}/api/jobs/convergence`,
        {
          analysis_type: solverSettings.solverType,
          setup,
          levels: 4,
          base_mesh_size: Number.isFinite(baseMeshSize) && baseMeshSize > 0
            ? baseMeshSize
            : 1.5,
        },
        { headers: currentAuthHeaders() },
      );
      const result = await pollJob(job.job_id, setStudyJobStatus);
      const parsed = toConvergenceStudy(result);
      setStudy(parsed);
      if (!parsed) {
        setStudyError('收敛检查返回的结果无法解析，请查看后端日志。');
      }
    } catch (error) {
      if (isUnauthorized(error)) {
        notifySessionExpired('登录已失效，请重新登录后再做收敛检查。');
        return;
      }
      setStudy(null);
      // 后端的 400 里带着可读原因（例如"级数不足"、"配置不合法"），原样透出
      const detail = (error as any)?.response?.data?.detail;
      const status = errorStatus(error);
      setStudyError(
        typeof detail === 'string' && detail
          ? detail
          : status === null
            ? '无法连接后端，收敛检查未执行。'
            : `收敛检查失败（HTTP ${status}）。`,
      );
    } finally {
      setIsStudying(false);
      setStudyJobStatus('');
    }
  };

  /**
   * 读取项目的求解记录。
   *
   * 404 表示"项目没了/我无权看"——不是本面板要解决的问题（工作台会因为项目
   * 加载失败而整体报错），所以这里安静地不显示。401 交给 App 统一处理。
   */
  const loadRuns = React.useCallback(async () => {
    setRunsLoading(true);
    setRunsError(null);
    try {
      const { data } = await axios.get(
        `${API_BASE_URL}/api/projects/${project.id}/runs`,
        { headers: currentAuthHeaders() },
      );
      setRuns(toRunList(data));
      // 跨运行对比与列表一起取：它回答"同一套配置下的几次运行收敛了吗"，
      // 而那正是"跑了好几次"之后最想知道的事。
      try {
        const analysis = await axios.get(
          `${API_BASE_URL}/api/projects/${project.id}/runs/analysis`,
          { headers: currentAuthHeaders() },
        );
        setRunAnalysis(toRunAnalysis(analysis.data));
      } catch (analysisError) {
        // 对比失败不该让历史列表也看不见（列表是有用的，对比是附加信息）
        console.warn('读取跨运行对比失败:', analysisError);
        setRunAnalysis(null);
      }
    } catch (error) {
      if (isUnauthorized(error)) {
        notifySessionExpired('登录已失效，请重新登录后再查看求解记录。');
        return;
      }
      const status = errorStatus(error);
      if (status === 404) {
        setRuns(null);
        return;
      }
      setRuns(null);
      setRunsError(
        status === null
          ? '无法连接后端，求解记录未读取。'
          : `读取求解记录失败（HTTP ${status}）。`,
      );
    } finally {
      setRunsLoading(false);
    }
  }, [API_BASE_URL, project.id]);

  /**
   * 记一条求解记录。
   *
   * **只提交标量**（不提交位移/应力数组）：那些数组是 MB 级的，而记录只需要
   * 几个数（见 backend/run_store.py 的说明）。考察量按分析类型取，模态取
   * **第一阶弹性频率**——刚体模态恒为 0，记下来等于没记。
   *
   * 记录失败**不影响求解结果**（结果已经在界面上），所以这里只提示、不抛出。
   */
  const recordRun = async (
    analysisType: string,
    solveResult: any,
    meshSize: unknown,
  ) => {
    const quantities: Record<string, number> = {};
    if (analysisType === 'thermal') {
      quantities.max_heat_flux = Number(solveResult?.max_heat_flux);
      quantities.max_temperature = Number(solveResult?.max_temperature);
    } else if (analysisType === 'modal') {
      const frequency = firstElasticFrequency(
        solveResult?.frequencies,
        solveResult?.rigid_body_modes ?? 0,
      );
      if (frequency === null) return;      // 取不到就别记一条空记录
      quantities.first_elastic_frequency = frequency;
    } else {
      quantities.max_stress = Number(solveResult?.max_stress);
      quantities.max_displacement = Number(solveResult?.max_displacement);
    }

    try {
      await axios.post(
        `${API_BASE_URL}/api/projects/${project.id}/runs`,
        {
          analysisType,
          quantities,
          meshSize: Number.isFinite(Number(meshSize)) ? Number(meshSize) : undefined,
          elements: Array.isArray(meshData?.elements) ? meshData.elements.length : undefined,
          nodes: Array.isArray(meshData?.nodes) ? meshData.nodes.length : undefined,
          warnings: Array.isArray(solveResult?.warnings) ? solveResult.warnings : [],
          // 配置指纹：跨运行对比时用它确认几次运行**只有网格不同**。
          // 材料/边界条件一变，数值的变化就跟网格无关，混在一起算收敛阶是编数字。
          setupSignature: setupSignature(
            buildSetupPayload({
              modelName,
              selectedMaterial,
              boundaryConditions,
              meshSettings,
              solverSettings,
            }),
          ),
        },
        { headers: currentAuthHeaders() },
      );
      await loadRuns();
    } catch (error) {
      // 401 已经由求解流程处理；这里只记日志，不打断用户
      console.warn('记录求解历史失败（不影响本次结果）:', error);
    }
  };

  const handleDeleteRun = async (runId: string) => {
    setDeletingRunId(runId);
    try {
      await axios.delete(
        `${API_BASE_URL}/api/projects/${project.id}/runs/${runId}`,
        { headers: currentAuthHeaders() },
      );
      await loadRuns();
    } catch (error) {
      if (isUnauthorized(error)) {
        notifySessionExpired('登录已失效，请重新登录。');
        return;
      }
      const status = errorStatus(error);
      setRunsError(
        status === null
          ? '无法连接后端，删除未执行。'
          : `删除求解记录失败（HTTP ${status}）。`,
      );
    } finally {
      setDeletingRunId(null);
    }
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
        // 请求体与"跑一次求解"完全共用（见 buildSolverSetup 的注释）
        const requestBody = buildSolverSetup();
        if (!requestBody) {
          console.error('求解配置不完整，无法组装请求体');
          return;
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

        // 记一条求解历史（存在后端，共享给别人的项目里对方也看得到）。
        // 失败只提示、不影响本次结果——结果已经在界面上了。
        void recordRun(analysisType, solveResult, meshSettings?.meshSize);
        
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
            {setupStatus === 'conflict' && (
              <button
                onClick={() => setSetupReloadKey(key => key + 1)}
                className="ml-1 underline hover:text-white"
                title="丢弃本地未保存的改动，重新读取服务器上的配置"
              >
                重新加载
              </button>
            )}
          </div>
          {/* 冲突必须显式说出来，并说明"你的改动没有保存"。
              只显示一个红色状态码不够——用户会以为已经被保存了。 */}
          {setupConflict && (
            <div className="mt-1 text-[11px] text-yellow-500 leading-snug max-w-[260px]">
              这个项目已被其他人修改，**你的改动没有保存**。
              {setupConflict}
              <br />
              重新加载会丢弃你本地未保存的改动。
            </div>
          )}
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
                              onClick={(e) => { e.stopPropagation(); handleConvergenceStudy(); }}
                              disabled={isStudying || isMeshing}
                              className="text-emerald-400 hover:text-emerald-300 disabled:opacity-40"
                              title="收敛性检查（逐级加密，回答“网格够不够细”）"
                            >
                              <TrendingUp size={13} />
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
                        {expandedNodes['mesh'] && (
                          <MeshQualityPanel
                            quality={meshQuality}
                            loading={meshQualityLoading}
                            error={meshQualityError}
                          />
                        )}
                        {expandedNodes['mesh'] && (
                          <ConvergencePanel
                            study={study}
                            loading={isStudying}
                            progress={studyJobStatus}
                            error={studyError}
                          />
                        )}
                      </div>

                      {/* Simulation Runs */}
                      <div
                        className="flex items-center py-1 cursor-pointer hover:text-white text-sm group -ml-4 mt-2"
                        onClick={() => toggleNode('simulation')}
                      >
                        <div className="w-4 border-b border-dashed border-[#333844] mr-1"></div>
                        {expandedNodes['simulation'] ? <ChevronDown size={14} className="mr-1 text-gray-400" /> : <ChevronRight size={14} className="mr-1 text-gray-400" />}
                        <Database size={14} className="mr-2 text-blue-400" />
                        <span>Simulation Runs</span>
                        {/* 导出：把结果带出这个工具（给协作者核对、存档、写报告）。
                            只在真有结果时出现——列一个点了会报错的按钮没有意义。 */}
                        {hasExportableResult && (
                          <div className="ml-auto mr-2 flex items-center gap-1">
                            <button
                              onClick={(e) => { e.stopPropagation(); handleExportResult('csv'); }}
                              className="text-gray-400 hover:text-white"
                              title="导出逐节点结果表（CSV）"
                            >
                              <FileDown size={13} />
                            </button>
                            <button
                              onClick={(e) => { e.stopPropagation(); handleExportResult('vtk'); }}
                              className="text-gray-400 hover:text-white text-[10px] font-medium"
                              title="导出 VTK（ParaView 可直接打开）"
                            >
                              VTK
                            </button>
                          </div>
                        )}
                        <button 
                          onClick={(e) => { e.stopPropagation(); handleSolve(); }}
                          className={`${hasExportableResult ? '' : 'ml-auto '}mr-2 bg-blue-600 hover:bg-blue-500 text-white p-1 rounded transition-colors`}
                          title="Run Simulation"
                        >
                          <Play size={12} fill="currentColor" />
                        </button>
                      </div>
                      {/* 求解记录：跑过哪些算例、什么网格、结果多少。
                          存在后端，所以被共享的人也能看到属主算过什么。 */}
                      {expandedNodes['simulation'] && (
                        <RunHistoryPanel
                          list={runs}
                          analysis={runAnalysis}
                          loading={runsLoading}
                          error={runsError}
                          canEdit={canEditProject(project)}
                          onDelete={handleDeleteRun}
                          deletingId={deletingRunId}
                        />
                      )}

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

          {/* 预览加载失败：不说明原因的话，用户只会看到一个空视口 */}
          {previewError && (
            <div className="absolute top-4 left-1/2 -translate-x-1/2 z-20 max-w-2xl w-[90%] bg-red-500/15 border border-red-500/50 backdrop-blur rounded-md p-3 shadow-lg">
              <div className="flex items-start gap-2">
                <i className="fas fa-triangle-exclamation text-red-400 mt-0.5"></i>
                <div className="flex-1 text-xs text-red-100">
                  <div className="font-semibold text-red-300">几何预览加载失败</div>
                  <div className="mt-1">{previewError}</div>
                  <div className="mt-1 text-red-200/70">
                    仿真配置、网格划分与求解不受影响；重新导入几何即可恢复预览。
                  </div>
                </div>
                <button
                  onClick={() => setPreviewError(null)}
                  className="text-red-300/70 hover:text-red-100 shrink-0"
                  title="关闭"
                >
                  <X size={14} />
                </button>
              </div>
            </div>
          )}

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
            clipSpec={clipSpec}
            onClipChange={setClipSpec}
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