import React, { useState } from 'react';
import { Plus, Search, LayoutGrid, List, Info, Folder, History, Users, Settings, Lock, Globe } from 'lucide-react';
import NewProjectModal from './components/NewProjectModal';
import Workbench from './components/Workbench';
import { Project, ViewState } from './types';
import LandingPage from './components/LandingPage'; // Import the new LandingPage component

function App() {
  const [isLoggedIn, setIsLoggedIn] = useState(false); // State to track login status
  const [view, setView] = useState<ViewState>('dashboard');
  const [isNewProjectModalOpen, setIsNewProjectModalOpen] = useState(false);
  const [currentProject, setCurrentProject] = useState<Project | null>(null);

  // Mock Projects
  const [projects, setProjects] = useState<Project[]>([
    { id: '1', title: 'Aerodynamic Wing v3', description: 'CFD analysis of the new wing profile', createdAt: new Date(), simulationType: 'CFD' }
  ]);

  const handleLogin = () => {
    setIsLoggedIn(true);
  };

  const handleCreateProject = (title: string, description: string, type: string, isPrivate: boolean = true) => {
    const newProject: Project = {
      id: Date.now().toString(),
      title,
      description,
      createdAt: new Date(),
      simulationType: type as any,
      isPrivate
    };
    setProjects([newProject, ...projects]);
    setCurrentProject(newProject);
    setIsNewProjectModalOpen(false);
    
    // Transition to Workbench
    setView('workbench');
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
          
          {/* Toolbar */}
          <div className="flex justify-between items-center mb-6">
            <span className="text-text-secondary text-sm">Sorted by: <span className="text-white font-medium">Last Modified</span></span>
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
                  <p className="text-text-secondary text-xs mt-1 line-clamp-2">{proj.description}</p>
                  <div className="mt-auto pt-3 flex items-center justify-between text-xs text-text-secondary">
                    <span className="flex items-center gap-2">
                      <span className="flex items-center gap-1" title={proj.isPrivate === false ? 'Public project' : 'Private project'}>
                        {proj.isPrivate === false ? <Globe size={12} /> : <Lock size={12} />}
                        {proj.isPrivate === false ? 'Public' : 'Private'}
                      </span>
                      <span>{proj.createdAt.toLocaleDateString()}</span>
                    </span>
                    <button className="hover:text-white"><Info size={14} /></button>
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
        onClose={() => setIsNewProjectModalOpen(false)}
        onCreate={handleCreateProject}
      />

    </div>
  );
}

export default App;