import React, { useRef, useState } from 'react';
import { X, UploadCloud, FileBox, CheckCircle, Loader2 } from 'lucide-react';
import axios from 'axios';
import { currentAuthHeaders, isUnauthorized, notifySessionExpired } from '../utils/authApi';

interface ImportModalProps {
  isOpen: boolean;
  onClose: () => void;
  onImport: (file: File, renderFilename?: string) => void;
}

const ImportModal: React.FC<ImportModalProps> = ({ isOpen, onClose, onImport }) => {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      validateAndSetFile(e.dataTransfer.files[0]);
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    e.preventDefault();
    if (e.target.files && e.target.files[0]) {
      validateAndSetFile(e.target.files[0]);
    }
  };

  const validateAndSetFile = (file: File) => {
    const extension = file.name.split('.').pop()?.toLowerCase();
    if (['stl', 'step', 'stp', 'iges', 'igs'].includes(extension || '')) {
      if (file.size === 0) {
         setError("The selected file is empty (0 bytes). Please choose a valid file.");
         setSelectedFile(null);
         return;
      }
      setSelectedFile(file);
      setError(null);
    } else {
      setError("Please upload a valid .stl, .step, or .iges file");
    }
  };

  const handleConfirm = async () => {
    if (selectedFile) {
      setUploading(true);
      setError(null);
      
      try {
        const formData = new FormData();
        formData.append("file", selectedFile);
        
        // API Base URL - use environment variable for Vercel deployment, fallback to localhost
        const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
        
        console.log(`Uploading file ${selectedFile.name} of size ${selectedFile.size} bytes`);
        
        // Upload to backend
        const response = await axios.post(`${API_BASE_URL}/api/upload-geometry`, formData, {
          // 只带认证头，**不要显式设置 Content-Type**：FormData 必须由浏览器
          // 自己带上 multipart 的 boundary，手写 'multipart/form-data' 反而可能
          // 让后端解析不出分片。
          headers: currentAuthHeaders(),
        });
        
        console.log("Upload successful:", response.data);
        
        // Notify parent component to load file into viewer
        onImport(selectedFile, response.data.render_filename);
        onClose();
        
      } catch (err) {
        console.error("Upload failed:", err);
        if (isUnauthorized(err)) {
          notifySessionExpired('登录已失效，请重新登录后再上传几何。');
          setError("登录已失效，请重新登录。");
          return;
        }
        setError("上传文件失败。请确保后端服务已启动。");
      } finally {
        setUploading(false);
      }
    }
  };

  const onButtonClick = () => {
    fileInputRef.current?.click();
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 backdrop-blur-md animate-in fade-in duration-300">
      <div className="bg-secondary border border-border rounded-lg shadow-2xl w-[700px] overflow-hidden">
        
        {/* Header */}
        <div className="flex justify-between items-center p-5 border-b border-border bg-[#1a202e]">
          <h2 className="text-lg font-semibold text-white">Import Geometry</h2>
          <button onClick={onClose} className="text-text-secondary hover:text-white">
            <X size={20} />
          </button>
        </div>

        {/* Content */}
        <div className="p-8">
          
          <div 
            className={`
              relative flex flex-col items-center justify-center h-64 rounded-lg border-2 border-dashed transition-all duration-200
              ${dragActive ? 'border-accent-blue bg-accent-blue/5' : 'border-border bg-[#0a0e17]'}
              ${selectedFile ? 'border-accent-cyan/50 bg-accent-cyan/5' : ''}
              ${error ? 'border-red-500/50 bg-red-500/5' : ''}
            `}
            onDragEnter={handleDrag}
            onDragLeave={handleDrag}
            onDragOver={handleDrag}
            onDrop={handleDrop}
          >
            <input
              ref={fileInputRef}
              type="file"
              className="hidden"
              accept=".stl,.step,.stp,.iges,.igs"
              onChange={handleChange}
            />

            {!selectedFile ? (
              <>
                <div className="w-16 h-16 rounded-full bg-secondary flex items-center justify-center mb-4 border border-border">
                  <UploadCloud className="text-accent-blue" size={32} />
                </div>
                <p className="text-lg font-medium text-white mb-2">Drag and drop or</p>
                <button 
                  onClick={onButtonClick}
                  className="px-4 py-2 bg-secondary border border-border hover:border-text-secondary rounded text-sm text-white transition-colors"
                >
                  <i className="fas fa-laptop mr-2"></i> Import from Computer
                </button>
                <p className="mt-4 text-xs text-text-secondary">
                  Native support: <span className="text-accent-blue font-mono">.STL, .STEP, .IGES</span>
                </p>
              </>
            ) : (
              <div className="flex flex-col items-center animate-in zoom-in duration-200">
                 <CheckCircle className="text-accent-cyan mb-3" size={48} />
                 <p className="text-white font-medium text-lg">{selectedFile.name}</p>
                 <p className="text-text-secondary text-sm">
                   {selectedFile.size > 0 
                     ? (selectedFile.size < 1024 * 1024 
                         ? (selectedFile.size / 1024).toFixed(2) + ' KB' 
                         : (selectedFile.size / 1024 / 1024).toFixed(2) + ' MB')
                     : '0.00 MB'}
                 </p>
                 <button 
                   onClick={() => setSelectedFile(null)}
                   className="mt-4 text-xs text-red-400 hover:text-red-300 underline"
                 >
                   Remove file
                 </button>
              </div>
            )}
            
            {error && (
              <div className="absolute bottom-4 text-red-400 text-sm">
                {error}
              </div>
            )}
          </div>

          {/* Action Footer inside Modal */}
          <div className="mt-8 flex justify-end gap-3">
             <button 
               onClick={onClose}
               className="px-5 py-2 rounded text-text-secondary hover:text-white border border-transparent hover:border-border transition-all"
             >
               Cancel
             </button>
             <button 
               disabled={!selectedFile || uploading}
               onClick={handleConfirm}
               className="px-6 py-2 rounded bg-accent-blue text-white font-medium hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
             >
               {uploading ? (
                 <>
                   <Loader2 className="animate-spin" size={16} />
                   Uploading...
                 </>
               ) : (
                 'Import'
               )}
             </button>
          </div>

        </div>
      </div>
    </div>
  );
};

export default ImportModal;