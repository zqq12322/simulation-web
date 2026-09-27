import React, { useCallback, useEffect, useState } from 'react';
import { Plus, Search, LayoutGrid, List, Info, Folder, History, Users, Settings, Lock, Globe, Trash2, RefreshCw, AlertTriangle } from 'lucide-react';
import axios from 'axios';
import NewProjectModal from './components/NewProjectModal';
import Workbench from './components/Workbench';
import { Project, ViewState } from './types';
import LandingPage from './components/LandingPage'; // Import the new LandingPage component
import {
  describeProjectError,
  formatCreatedAt,
  toProject,
  toProjectList,
} from './utils/projectsApi';

function App() {
  const [isLoggedIn, setIsLoggedIn] = useState(false); // State to track login status
  const [view, setView] = useState<ViewState>('dashboard');
  const [isNewProjectModalOpen, setIsNewProjectModalOpen] = useState(false);
  const [currentProject, setCurrentProject] = useState<Project | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // API Base URL（与 Workbench 保持一致）
  const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  /**
   * 项目列表来自后端（SQLite 持久化），不再是硬编码的 mock。
   *
   * 在这之前这里是一个写死的数组（`{ id: '1', title: 'Aerodynamic Wing v3' }`），
   * 新建的项目只存在浏览器内存里，刷新就没了，也无法被引用或共享。
   */
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [projectsError, setProjectsError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const refreshProjects = useCallback(async () => {
    setProjectsLoading(true);
    try {
      const { data } = await axios.get(`${API_BASE_URL}/api/projects`);
      setProjects(toProjectList(data));
      // 后端不可用时必须说出来，而不是显示一个空列表让用户以为项目丢了
      setProjectsError(null);
    } catch (error) {
      console.error('加载项目列表失败:', error);
      setProjectsError(describeProjectError(error));
    } finally {
      setProjectsLoading(false);
    }
  }, [API_BASE_URL]);

  useEffect(() => {
    if (!isLoggedIn) return;
    refreshProjects();
  }, [isLoggedIn, refreshProjects]);

  const handleLogin = () => {
    setIsLoggedIn(true);
  };

  const handleCreateProject = async (title: string, description: string, type: string, isPrivate: boolean = true) => {
    setIsCreating(true);
    setCreateError(null);
    try {
      const { data } = await axios.post(`${API_BASE_URL}/api/projects`, {
        title,
        description,
        simulationType: type,
        isPrivate,
      });
      const created = toProject(data);
      if (!created) {
        throw new Error('后端返回的项目记录不完整，无法打开。');
      }
      // 用服务端返回的记录重建列表，而不是本地拼一条"看起来一样"的对象：
      // ID 与时间戳都由服务端生成，本地拼的会和真实记录不一致。
      setProjects(prev => [created, ...prev.filter(item => item.id !== created.id)]);
      setCurrentProject(created);
      setIsNewProjectModalOpen(false);
      setView('workbench');
    } catch (error) {
      console.error('创建项目失败:', error);
      setCreateError(describeProjectError(error));
    } finally {
      setIsCreating(false);
    }
  };

  const handleDeleteProject = async (project: Project) => {
    if (!window.confirm(`确定删除项目「${project.title}」？此操作不可撤销。`)) return;
    setDeletingId(project.id);
    try {
      await axios.delete(`${API_BASE_URL}/api/projects/${project.id}`);
      setProjects(prev => prev.filter(item => item.id !== project.id));
      setProjectsError(null);
    } catch (error) {
      console.error('删除项目失败:', error);
      setProjectsError(describeProjectError(error));
      // 删除失败时重新拉一次，避免界面上的列表与后端不一致
      refreshProjects();
    } finally {
      setDeletingId(null);
    }
  };

  const handleProjectClick = (project: Project) => {
    setCurrentProject(project);
    setView('workbench');
  };

  const handleBackToDashboard = () => {
    setView('dashboard');
    setCurrentProject(null);
  };

  // --- RENDER LANDING PAGE ---
  if (!isLoggedIn) {
    return <LandingPage onStart={handleLogin} />;
  }

  // --- RENDER WORKBENCH ---
  if (view === 'workbench' && currentProject) {
    return (
      <Workbench 
        project={currentProject} 
        onBack={handleBackToDashboard} 
      />
    );
  }

  // --- RENDER DASHBOARD ---
  return (
    <div className="flex h-screen bg-primary text-text-primary font-sans overflow-hidden">
      
      {/* Sidebar */}
      <aside className="w-64 bg-secondary border-r border-border flex flex-col shrink-0">
        <div className="p-6 flex items-center gap-3 cursor-pointer">
          <i className="fas fa-atom text-accent-blue text-2xl"></i>
          <span className="font-bold text-lg bg-clip-text text-transparent bg-gradient-to-r from-accent-blue to-accent-purple">SimCloud AI</span>
        </div>
        
        <nav className="flex-1 px-4 space-y-1 mt-4">
          <a href="#" className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-accent-blue/10 text-accent-blue font-medium border border-accent-blue/20">
            <Folder size={18} />
            <span>My Projects</span>
          </a>
          <a href="#" className="flex items-center gap-3 px-3 py-2.5 rounded-lg text-text-secondary hover:text-white hover:bg-white/5 transition-colors">
            <History size={18} />
            <span>Recent Projects</span>
          </a>
          <a href="#" className="flex items-center gap-3 px-3 py-2.5 rounded-lg text-text-secondary hover:text-white hover:bg-white/5 transition-colors">
            <Users size={18} />
            <span>Shared with me</span>
          </a>
          <a href="#" className="flex items-center gap-3 px-3 py-2.5 rounded-lg text-text-secondary hover:text-white hover:bg-white/5 transition-colors">
            <i className="fas fa-globe w-[18px] text-center"></i>
            <span>Public Projects</span>
          </a>
        </nav>

        <div className="p-4 border-t border-border space-y-1">
          <a href="#" className="flex items-center gap-3 px-3 py-2 text-text-secondary hover:text-white hover:bg-white/5 rounded transition-colors text-sm">
             <Settings size={16} /> Settings
          </a>
          <div className="flex items-center gap-3 px-3 py-3 mt-2 border-t border-border/50 pt-4">
             <div className="w-8 h-8 rounded-full bg-gradient-to-br from-accent-blue to-accent-purple flex items-center justify-center text-xs font-bold shadow-lg shadow-accent-blue/20">
               U
             </div>
             <div className="flex flex-col">
               <span className="text-xs font-bold text-white">Guest User</span>
               <span className="text-[10px] text-text-secondary">Free Plan</span>
             </div>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 flex flex-col overflow-hidden bg-[url('https://www.transparenttextures.com/patterns/cubes.png')]">
        
        {/* Top Header */}
        <header className="h-16 border-b border-border bg-secondary/95 backdrop-blur flex items-center justify-between px-8 shrink-0">
          <h1 className="text-xl font-semibold">My Projects</h1>
          <div className="flex items-center gap-4">
             <div className="relative">
               <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-text-secondary" size={16} />
               <input 
                 type="text" 
                 placeholder="Search projects..." 
                 className="bg-[#0a0e17] border border-border rounded-full pl-10 pr-4 py-1.5 text-sm text-white focus:border-accent-blue focus:outline-none w-64 transition-all"
               />
             </div>
             <button 
               onClick={() => setIsNewProjectModalOpen(true)}
               className="bg-accent-blue hover:bg-blue-600 text-white px-4 py-2 rounded-md text-sm font-semibold flex items-center gap-2 transition-all shadow-lg shadow-accent-blue/20 transform hover:-translate-y-0.5"
             >
               <Plus size={16} /> New Project
             </button>
             <button className="bg-secondary border border-border hover:bg-white/5 text-text-secondary hover:text-white px-4 py-2 rounded-md text-sm font-medium transition-colors">
                New Folder
             </button>
          </div>
        </header>

        {/* Dashboard Area */}
        <div className="flex-1 overflow-auto p-8">

          {/* 后端不可用时必须说出来：显示空列表会让人以为项目被删光了 */}
          {projectsError && (
            <div className="mb-6 bg-yellow-500/10 border border-yellow-500/40 rounded-lg p-4 flex items-start gap-3">
              <AlertTriangle size={18} className="text-yellow-400 mt-0.5 shrink-0" />
              <div className="flex-1">
                <div className="text-sm font-semibold text-yellow-200">项目列表加载失败</div>
                <div className="text-xs text-yellow-100/80 mt-1">{projectsError}</div>
              </div>
              <button
                onClick={refreshProjects}
                className="flex items-center gap-1 text-xs text-yellow-200 hover:text-white border border-yellow-500/40 hover:border-yellow-300 rounded px-2 py-1 transition-colors shrink-0"
              >
                <RefreshCw size={12} /> 重试
              </button>
            </div>
          )}

          {/* Toolbar */}
          <div className="flex justify-between items-center mb-6">
            <span className="text-text-secondary text-sm">
              {projectsLoading ? '加载中…' : `共 ${projects.length} 个项目`}
              <span className="text-text-secondary/60"> · 按创建时间倒序</span>
            </span>
            <div className="flex bg-[#0a0e17] rounded-md border border-border p-1">
               <button className="p-1.5 bg-secondary rounded text-white shadow-sm"><LayoutGrid size={16} /></button>
               <button className="p-1.5 text-text-secondary hover:text-white"><List size={16} /></button>
            </div>
          </div>

          {/* Projects Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
            
            {/* Create New Card (Trigger) */}
            <div 
              onClick={() => setIsNewProjectModalOpen(true)}
              className="group h-64 border-2 border-dashed border-border rounded-xl flex flex-col items-center justify-center cursor-pointer hover:border-accent-blue hover:bg-accent-blue/5 transition-all duration-300"
            >
              <div className="w-16 h-16 rounded-full bg-secondary flex items-center justify-center mb-4 group-hover:scale-110 group-hover:bg-accent-blue group-hover:text-white transition-all duration-300 text-text-secondary border border-border group-hover:border-accent-blue">
                <Plus size={32} />
              </div>
              <span className="text-text-secondary font-medium group-hover:text-accent-blue transition-colors">Create a Project</span>
            </div>

            {/* 已加载但一个都没有 —— 明确告诉用户这是"空"，而不是"坏了" */}
            {!projectsLoading && !projectsError && projects.length === 0 && (
              <div className="h-64 border border-border rounded-xl flex flex-col items-center justify-center text-center px-6 bg-secondary/40">
                <Info size={24} className="text-text-secondary mb-3" />
                <div className="text-sm text-text-primary">还没有项目</div>
                <div className="text-xs text-text-secondary mt-1">
                  点左侧虚线卡片新建一个。项目会保存在后端数据库里，刷新和重启都不会丢。
                </div>
              </div>
            )}

            {/* Existing Projects */}
            {projects.map((proj) => (
              <div key={proj.id} onClick={() => handleProjectClick(proj)} className="bg-secondary border border-border rounded-xl overflow-hidden hover:shadow-xl hover:shadow-black/50 hover:border-accent-blue/50 transition-all cursor-pointer group flex flex-col h-64">
                <div className="h-36 bg-[#0a0e17] relative overflow-hidden flex items-center justify-center">
                  <div className="absolute inset-0 bg-gradient-to-b from-transparent to-secondary/80"></div>
                  <i className={`fas ${proj.simulationType === 'CFD' ? 'fa-wind' : proj.simulationType === 'FEA' ? 'fa-layer-group' : 'fa-cube'} text-4xl text-border group-hover:text-accent-blue/50 transition-colors transform group-hover:scale-110 duration-500`}></i>
                  
                  {/* Type Badge */}
                  {proj.simulationType && (
                    <div className="absolute top-3 right-3 px-2 py-0.5 bg-accent-blue/20 border border-accent-blue/30 rounded text-[10px] text-accent-blue font-mono uppercase">
                      {proj.simulationType}
                    </div>
                  )}

                  <div className="absolute bottom-3 left-3 px-2 py-1 bg-black/60 backdrop-blur rounded text-xs text-text-secondary">
                    No preview available
                  </div>
                </div>
                <div className="p-4 flex flex-col flex-1">
                  <h3 className="text-white font-semibold truncate group-hover:text-accent-blue transition-colors">{proj.title}</h3>
                  <p className="text-text-secondary text-xs mt-1 line-clamp-2">{proj.description || '（无描述）'}</p>
                  <div className="mt-auto pt-3 flex items-center justify-between text-xs text-text-secondary">
                    <span className="flex items-center gap-2">
                      <span className="flex items-center gap-1" title={proj.isPrivate === false ? 'Public project' : 'Private project'}>
                        {proj.isPrivate === false ? <Globe size={12} /> : <Lock size={12} />}
                        {proj.isPrivate === false ? 'Public' : 'Private'}
                      </span>
                      {/* 时间是后端返回的 ISO 字符串，必须经 formatCreatedAt 解析 */}
                      <span>{formatCreatedAt(proj.createdAt)}</span>
                    </span>
                    <button
                      onClick={(event) => {
                        event.stopPropagation(); // 不要顺带触发"打开项目"
                        handleDeleteProject(proj);
                      }}
                      disabled={deletingId === proj.id}
                      className="hover:text-red-400 disabled:opacity-40 transition-colors"
                      title="删除项目"
                    >
                      {deletingId === proj.id ? <RefreshCw size={14} className="animate-spin" /> : <Trash2 size={14} />}
                    </button>
                  </div>
                </div>
              </div>
            ))}

          </div>
        </div>
      </main>

      {/* Modals */}
      <NewProjectModal 
        isOpen={isNewProjectModalOpen}
        onClose={() => { setIsNewProjectModalOpen(false); setCreateError(null); }}
        onCreate={handleCreateProject}
        isSubmitting={isCreating}
        error={createError}
      />

    </div>
  );
}

export default App;