import React from 'react';
import { AlertTriangle, CheckCircle2, HelpCircle, Loader2, TrendingUp } from 'lucide-react';
import {
  ConvergenceStudy,
  describeStudyStatus,
  describeStudySummary,
  formatOrder,
  formatPercent,
  quantityLabel,
  studyRows,
  trendPoints,
} from '../utils/convergenceStudy';

interface ConvergencePanelProps {
  study: ConvergenceStudy | null;
  loading?: boolean;
  /** 进度文案（异步任务轮询中的状态）。 */
  progress?: string | null;
  error?: string | null;
}

const TONE_CLASSES: Record<string, string> = {
  good: 'text-green-500',
  warn: 'text-yellow-500',
  bad: 'text-red-400',
  info: 'text-gray-400',
};

const TONE_ICONS: Record<string, React.ReactNode> = {
  good: <CheckCircle2 size={11} className="mr-1 mt-0.5 flex-shrink-0" />,
  warn: <AlertTriangle size={11} className="mr-1 mt-0.5 flex-shrink-0" />,
  bad: <AlertTriangle size={11} className="mr-1 mt-0.5 flex-shrink-0" />,
  info: <HelpCircle size={11} className="mr-1 mt-0.5 flex-shrink-0" />,
};

/**
 * 收敛检查面板：逐级数值表 + 误差随 h 的趋势 + 结论与说明。
 *
 * 三个刻意的取舍：
 *
 * 1. **四态都显示，不等同于"通过/失败"**：`marginal`（在趋稳但没到阈值）是
 *    最常见的状态，把它画成红色会让人以为算错了，画成绿色会让人以为可以定案。
 * 2. **趋势图的横轴用实测单元数折算的 h，不用"第几级"**：各级实际加密幅度
 *    并不相等（见结论里的"实际加密比"），按级数画会把不等距的点画成等距，
 *    看着像一条漂亮的收敛曲线，其实是在骗人。
 * 3. **说明（notes）默认折叠但一定在**：里面写着"自收敛不能证明模型正确"
 *    与"应力奇异加密无用"这两条限定。可以折叠，不能省略。
 */
export default function ConvergencePanel({
  study,
  loading = false,
  progress = null,
  error = null,
}: ConvergencePanelProps) {
  if (loading) {
    return (
      <div className="ml-10 py-1 pr-3 flex items-center text-[11px] text-gray-500">
        <Loader2 size={11} className="mr-1 animate-spin" />
        正在逐级加密检查{progress ? `（${progress}）` : '…'}
      </div>
    );
  }

  if (error) {
    return (
      <div className="ml-10 py-1 pr-3 flex items-start text-[11px] text-red-400">
        <AlertTriangle size={11} className="mr-1 mt-0.5 flex-shrink-0" />
        <span className="leading-snug">{error}</span>
      </div>
    );
  }

  if (!study) return null;

  const status = describeStudyStatus(study.status);
  const rows = studyRows(study);
  const points = trendPoints(study, study.primary);

  return (
    <div className="ml-10 py-1 pr-3 space-y-1">
      <div className={`flex items-start text-[11px] leading-snug ${TONE_CLASSES[status.tone]}`}>
        {TONE_ICONS[status.tone]}
        <span>
          {status.label}
          <span className="text-gray-500"> · {describeStudySummary(study)}</span>
        </span>
      </div>

      {rows.length > 0 && (
        <table className="w-full text-[10px] text-gray-400">
          <thead>
            <tr className="text-gray-600">
              <th className="text-left font-normal">级</th>
              <th className="text-right font-normal">单元</th>
              {study.quantities.map(name => (
                <th key={name} className="text-right font-normal">
                  {quantityLabel(study, name)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(row => (
              <tr key={row.label}>
                <td className="text-left text-gray-500">{row.label}</td>
                <td className="text-right">{row.elements}</td>
                {study.quantities.map(name => (
                  <td key={name} className="text-right">{row.formatted[name]}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {points.length >= 2 && (
        <div>
          <div className="flex items-center text-[10px] text-gray-600">
            <TrendingUp size={10} className="mr-1" />
            {quantityLabel(study, study.primary)}随 h 的变化（横轴：粗 → 细）
          </div>
          <svg viewBox="0 0 100 36" className="w-full h-9" preserveAspectRatio="none">
            {/* y 已归一化到 [0,1]，这里翻成 SVG 坐标（0 在上） */}
            <polyline
              fill="none"
              stroke="#60a5fa"
              strokeWidth="1"
              vectorEffect="non-scaling-stroke"
              points={points
                .map(point => `${(point.x * 100).toFixed(2)},${((1 - point.y) * 36).toFixed(2)}`)
                .join(' ')}
            />
            {points.map((point, index) => (
              <circle
                key={index}
                cx={(point.x * 100).toFixed(2)}
                cy={((1 - point.y) * 36).toFixed(2)}
                r="1.2"
                fill="#93c5fd"
              />
            ))}
          </svg>
        </div>
      )}

      <div className="space-y-0.5">
        {study.quantities.map(name => {
          const assessment = study.assessments[name];
          if (!assessment) return null;
          return (
            <div
              key={name}
              className={`text-[10px] leading-snug ${
                assessment.converged ? 'text-gray-400' : 'text-yellow-500'
              }`}
            >
              {quantityLabel(study, name)}：阶数 {formatOrder(assessment.observedOrder)}
              {' · '}最后一级变化 {formatPercent(assessment.lastRelativeChange)}
              {assessment.converged ? '' : ' · 差值未按预期收敛'}
            </div>
          );
        })}
      </div>

      {study.verdict && (
        <div className="text-[11px] text-gray-400 leading-snug">{study.verdict}</div>
      )}

      {study.notes.length > 0 && (
        <details className="text-[10px] text-gray-500 leading-snug">
          <summary className="cursor-pointer text-gray-600">
            说明与限定（{study.notes.length} 条，建议读）
          </summary>
          <ul className="mt-1 space-y-0.5 list-disc list-inside">
            {study.notes.map((note, index) => (
              <li key={index}>{note}</li>
            ))}
          </ul>
        </details>
      )}

      {study.warnings.length > 0 && (
        <details className="text-[10px] text-yellow-600 leading-snug">
          <summary className="cursor-pointer">
            求解器警告（{study.warnings.length} 条）
          </summary>
          <ul className="mt-1 space-y-0.5 list-disc list-inside">
            {study.warnings.map((warning, index) => (
              <li key={index}>{warning}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
