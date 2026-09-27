import React, { useEffect, useState } from 'react';
import { AlertTriangle, Lock, RefreshCw, User as UserIcon, LogIn, UserPlus } from 'lucide-react';

interface AuthPanelProps {
  /** 登录或注册。失败时应抛出（由本组件显示原因）。 */
  onAuthenticate: (mode: 'login' | 'register', username: string, password: string) => Promise<void>;
  /** 后端公开的认证配置（最少口令长度等），拿不到时用默认值 */
  config?: {
    allowRegistration: boolean;
    usernameMinLength: number;
    passwordMinLength: number;
  } | null;
}

type Mode = 'login' | 'register';

const DEFAULTS = { allowRegistration: true, usernameMinLength: 3, passwordMinLength: 8 };

/**
 * 登录 / 注册面板。
 *
 * 为什么单独成组件而不是塞进 LandingPage：**登录是访问控制的一部分**，
 * 它必须能在"令牌失效"时被重新唤起（例如另一个标签页登出了、令牌过期了），
 * 而不是只能从营销首页进入。
 */
const AuthPanel: React.FC<AuthPanelProps> = ({ onAuthenticate, config }) => {
  const limits = config ?? DEFAULTS;
  const [mode, setMode] = useState<Mode>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 若部署关闭了注册，而用户正好停在注册页，切回登录
  useEffect(() => {
    if (!limits.allowRegistration && mode === 'register') setMode('login');
  }, [limits.allowRegistration, mode]);

  const canSubmit =
    !submitting
    && username.trim().length >= limits.usernameMinLength
    && password.length >= limits.passwordMinLength;

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      await onAuthenticate(mode, username.trim(), password);
      // 成功后不需要清空表单：父组件会卸载本面板
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex h-screen w-screen items-center justify-center bg-primary text-text-primary font-sans px-4">
      <div className="w-full max-w-md bg-secondary border border-border rounded-2xl shadow-2xl shadow-black/40 overflow-hidden">
        <div className="px-8 pt-8 pb-6 border-b border-border">
          <div className="flex items-center gap-3 mb-2">
            <i className="fas fa-atom text-accent-blue text-2xl"></i>
            <span className="font-bold text-lg bg-clip-text text-transparent bg-gradient-to-r from-accent-blue to-accent-purple">
              SimCloud AI
            </span>
          </div>
          <p className="text-xs text-text-secondary leading-relaxed">
            {mode === 'login'
              ? '登录后可以看到你自己的项目。项目保存在后端数据库里，刷新与重启都不会丢。'
              : '注册一个新账号。每个账号只能看到并修改自己的项目。'}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="px-8 py-6 space-y-4">
          <div>
            <label htmlFor="auth-username" className="block text-xs text-text-secondary mb-1.5">
              用户名
            </label>
            <div className="relative">
              <UserIcon size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-secondary" />
              <input
                id="auth-username"
                type="text"
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder={`至少 ${limits.usernameMinLength} 个字符（字母/数字/下划线）`}
                className="w-full bg-[#0a0e17] border border-border rounded-md pl-9 pr-3 py-2.5 text-sm text-white focus:border-accent-blue focus:outline-none"
              />
            </div>
          </div>

          <div>
            <label htmlFor="auth-password" className="block text-xs text-text-secondary mb-1.5">
              口令
            </label>
            <div className="relative">
              <Lock size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-secondary" />
              <input
                id="auth-password"
                type="password"
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={`至少 ${limits.passwordMinLength} 个字符`}
                className="w-full bg-[#0a0e17] border border-border rounded-md pl-9 pr-3 py-2.5 text-sm text-white focus:border-accent-blue focus:outline-none"
              />
            </div>
          </div>

          {error && (
            <div className="flex items-start gap-2 bg-red-500/10 border border-red-500/40 rounded-md px-3 py-2">
              <AlertTriangle size={14} className="text-red-400 mt-0.5 shrink-0" />
              <span className="text-xs text-red-200">{error}</span>
            </div>
          )}

          <button
            type="submit"
            disabled={!canSubmit}
            className="w-full flex items-center justify-center gap-2 bg-gradient-to-r from-accent-blue to-accent-purple text-white font-semibold py-2.5 rounded-md shadow-lg shadow-accent-blue/20 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
          >
            {submitting && <RefreshCw size={14} className="animate-spin" />}
            {mode === 'login' ? <LogIn size={15} /> : <UserPlus size={15} />}
            {submitting ? '请稍候…' : mode === 'login' ? '登录' : '注册并登录'}
          </button>

          {limits.allowRegistration && (
            <button
              type="button"
              onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError(null); }}
              className="w-full text-xs text-text-secondary hover:text-white transition-colors py-1"
            >
              {mode === 'login' ? '还没有账号？去注册' : '已有账号？去登录'}
            </button>
          )}
        </form>
      </div>
    </div>
  );
};

export default AuthPanel;
