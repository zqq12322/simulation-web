import React, { useCallback, useEffect, useState } from 'react';
import { X, UserPlus, Trash2, RefreshCw, AlertTriangle, Lock, Pencil } from 'lucide-react';
import axios from 'axios';
import { currentAuthHeaders, isUnauthorized, notifySessionExpired } from '../utils/authApi';
import { describeProjectError } from '../utils/projectsApi';

interface ShareModalProps {
  isOpen: boolean;
  onClose: () => void;
  projectId: string;
  projectTitle: string;
  /** 共享状态变化时通知父组件（列表里的卡片不需要，但便于将来刷新） */
  onChanged?: () => void;
}

interface ShareRecord {
  userId: string;
  username: string;
  displayName: string;
  role: 'viewer' | 'editor';
  createdAt: string;
}

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

/**
 * 共享管理弹窗（仅项目属主可用）。
 *
 * 设计取舍：**按用户名共享**而不是用户 id —— 用户知道的是用户名，
 * 那串随机 id 只应该出现在 API 内部。
 */
const ShareModal: React.FC<ShareModalProps> = ({
  isOpen, onClose, projectId, projectTitle, onChanged,
}) => {
  const [shares, setShares] = useState<ShareRecord[]>([]);
  const [username, setUsername] = useState('');
  const [role, setRole] = useState<'viewer' | 'editor'>('viewer');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await axios.get(
        `${API_BASE_URL}/api/projects/${projectId}/shares`,
        { headers: currentAuthHeaders() },
      );
      setShares(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error('加载共享名单失败:', err);
      if (isUnauthorized(err)) {
        notifySessionExpired('登录已失效，请重新登录。');
        return;
      }
      setError(describeProjectError(err));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (isOpen) {
      setUsername('');
      setRole('viewer');
      void load();
    }
  }, [isOpen, load]);

  const handleInvite = async (event: React.FormEvent) => {
    event.preventDefault();
    const name = username.trim();
    if (!name || busy) return;
    setBusy(true);
    setError(null);
    try {
      await axios.post(
        `${API_BASE_URL}/api/projects/${projectId}/shares`,
        { username: name, role },
        { headers: currentAuthHeaders() },
      );
      setUsername('');
      await load();
      onChanged?.();
    } catch (err) {
      console.error('共享失败:', err);
      if (isUnauthorized(err)) {
        notifySessionExpired('登录已失效，请重新登录。');
        return;
      }
      // 404 是"没有这个用户"，与"请求不合法"必须分开——用户要靠这句话知道自己
      // 打错了用户名，而不是以为系统坏了
      setError(describeProjectError(err));
    } finally {
      setBusy(false);
    }
  };

  const handleRoleChange = async (record: ShareRecord, next: 'viewer' | 'editor') => {
    setBusy(true);
    setError(null);
    try {
      // 再次共享就是改角色（后端是复合主键 + upsert），不需要单独的端点
      await axios.post(
        `${API_BASE_URL}/api/projects/${projectId}/shares`,
        { username: record.username, role: next },
        { headers: currentAuthHeaders() },
      );
      await load();
      onChanged?.();
    } catch (err) {
      setError(describeProjectError(err));
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async (record: ShareRecord) => {
    setBusy(true);
    setError(null);
    try {
      await axios.delete(
        `${API_BASE_URL}/api/projects/${projectId}/shares/${record.userId}`,
        { headers: currentAuthHeaders() },
      );
      await load();
      onChanged?.();
    } catch (err) {
      setError(describeProjectError(err));
    } finally {
      setBusy(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-[70]">
      <div className="bg-[#1a1e2c] border border-[#333844] rounded-lg shadow-xl w-full max-w-lg overflow-hidden flex flex-col">
        <div className="flex items-center justify-between p-4 border-b border-[#333844] bg-[#222736]">
          <div>
            <h2 className="text-lg font-bold text-white">共享项目</h2>
            <p className="text-xs text-text-secondary mt-0.5 truncate max-w-sm">{projectTitle}</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white p-1 rounded-full hover:bg-[#333844]">
            <X size={20} />
          </button>
        </div>

        <form onSubmit={handleInvite} className="p-4 border-b border-[#333844] space-y-3">
          <div className="flex gap-2">
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="协作者的用户名"
              className="flex-1 bg-[#0a0e17] border border-[#333844] rounded px-3 py-2 text-sm text-white focus:border-blue-500 focus:outline-none"
            />
            <button
              type="submit"
              disabled={!username.trim() || busy}
              className="flex items-center gap-1.5 px-4 py-2 rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm"
            >
              {busy ? <RefreshCw size={14} className="animate-spin" /> : <UserPlus size={14} />}
              共享
            </button>
          </div>

          <div className="flex gap-2">
            {([
              { id: 'viewer', label: '只读', icon: Lock, hint: '能看、能求解看结果，改不了配置' },
              { id: 'editor', label: '可编辑', icon: Pencil, hint: '可以修改仿真配置' },
            ] as const).map((option) => (
              <button
                key={option.id}
                type="button"
                onClick={() => setRole(option.id)}
                className={`flex-1 flex items-center gap-2 px-3 py-2 rounded border text-left text-xs transition-colors ${
                  role === option.id
                    ? 'bg-blue-500/10 border-blue-500 text-white'
                    : 'bg-[#0a0e17] border-[#333844] text-text-secondary hover:text-white'
                }`}
              >
                <option.icon size={14} />
                <span className="font-medium">{option.label}</span>
                <span className="text-[10px] opacity-70">{option.hint}</span>
              </button>
            ))}
          </div>

          {error && (
            <div className="flex items-start gap-2 bg-red-500/10 border border-red-500/40 rounded px-3 py-2">
              <AlertTriangle size={14} className="text-red-400 mt-0.5 shrink-0" />
              <span className="text-xs text-red-200">{error}</span>
            </div>
          )}
        </form>

        <div className="p-4 max-h-72 overflow-y-auto">
          <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
            已共享给 {shares.length} 人
          </div>
          {loading && !shares.length && (
            <div className="text-sm text-text-secondary flex items-center gap-2">
              <RefreshCw size={13} className="animate-spin" /> 加载中…
            </div>
          )}
          {!loading && shares.length === 0 && (
            <div className="text-sm text-text-secondary">
              还没有共享给任何人。这个项目目前只有你自己能看到。
            </div>
          )}
          <div className="space-y-2">
            {shares.map((record) => (
              <div key={record.userId}
                   className="flex items-center gap-3 bg-[#0a0e17] border border-[#333844] rounded px-3 py-2">
                <div className="w-7 h-7 rounded-full bg-gradient-to-br from-accent-blue to-accent-purple flex items-center justify-center text-[10px] font-bold">
                  {record.displayName.slice(0, 1).toUpperCase()}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-white truncate">{record.displayName}</div>
                  <div className="text-[10px] text-text-secondary truncate">@{record.username}</div>
                </div>
                <select
                  value={record.role}
                  disabled={busy}
                  onChange={(e) => void handleRoleChange(record, e.target.value as 'viewer' | 'editor')}
                  className="bg-[#161a25] border border-[#333844] rounded px-2 py-1 text-xs text-white focus:outline-none"
                >
                  <option value="viewer">只读</option>
                  <option value="editor">可编辑</option>
                </select>
                <button
                  onClick={() => void handleRemove(record)}
                  disabled={busy}
                  title="移除协作者"
                  className="text-text-secondary hover:text-red-400 disabled:opacity-40"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        </div>

        <div className="p-4 border-t border-[#333844] flex justify-end">
          <button onClick={onClose} className="px-5 py-2 rounded text-gray-300 hover:bg-[#333844] text-sm">
            完成
          </button>
        </div>
      </div>
    </div>
  );
};

export default ShareModal;
