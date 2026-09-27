import React from 'react';
import { AlertTriangle, CheckCircle2, Loader2 } from 'lucide-react';
import {
  MeshQuality,
  barHeight,
  describeQualitySummary,
  describeQualityWarnings,
  formatQuality,
  histogramIsComplete,
  maxBinCount,
} from '../utils/meshQuality';

interface MeshQualityPanelProps {
  /** 已归一化的质量数据；`null` 表示还没检查过。 */
  quality: MeshQuality | null;
  /** 正在向后端查询。 */
  loading?: boolean;
  /** 查询失败的原因（可直接显示）。 */
  error?: string | null;
}

/**
 * 网格质量面板：直方图 + 分项提醒 + 后端结论。
 *
 * 显示上的三个刻意取舍：
 *
 * 1. **最小值最大**：判断网格能不能用看最差的那个单元。平均值好看完全可能是
 *    由几个刀片单元拉低的，把它当主指标会误导人。所以摘要里先给最低值。
 * 2. **"形状好 ≠ 够细"必须一直可见**：后端结论句里带着这句（见
 *    `mesh_quality._verdict`），这里原样透出而不改写。否则用户会把"没有畸形
 *    单元"读成"结果可信"，而网格密度是另一件事（收敛性检查，尚未实现）。
 * 3. **直方图不自洽时说出来**：各箱计数之和若不等于单元数，说明有一批单元
 *    没被画出来——被丢掉的往往是畸形单元，正是最该被看到的。
 */
export default function MeshQualityPanel({
  quality,
  loading = false,
  error = null,
}: MeshQualityPanelProps) {
  if (loading) {
    return (
      <div className="ml-10 py-1 pr-3 flex items-center text-[11px] text-gray-500">
        <Loader2 size={11} className="mr-1 animate-spin" />
        正在检查网格质量…
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

  if (!quality) return null;

  const ceiling = maxBinCount(quality.histogram);
  const complete = histogramIsComplete(quality);
  const warnings = describeQualityWarnings(quality);
  const healthy = quality.poorCount === 0 && quality.nonPositiveVolumeCount === 0
    && quality.nonFiniteCount === 0;

  return (
    <div className="ml-10 py-1 pr-3 space-y-1">
      <div className="text-[11px] text-gray-400">{describeQualitySummary(quality)}</div>

      {/* 直方图：10 个等宽箱，高度按最大计数归一化 */}
      <div className="flex items-end gap-[2px] h-10" title="形状质量分布（0 差 → 1 好）">
        {quality.histogram.map((bin, index) => (
          <div
            key={index}
            className={`flex-1 rounded-sm ${
              bin.hi <= quality.poorThreshold ? 'bg-red-500/70' : 'bg-blue-500/70'
            }`}
            style={{ height: `${Math.max(2, barHeight(bin.count, ceiling) * 100)}%` }}
            title={`${bin.lo.toFixed(2)}–${bin.hi.toFixed(2)}：${bin.count} 个单元`}
          />
        ))}
      </div>
      <div className="flex justify-between text-[10px] text-gray-600">
        <span>0 差</span>
        <span>棱长比 ≤ {formatQuality(quality.edgeRatioMax)}</span>
        <span>1 好</span>
      </div>

      {!complete && (
        <div className="text-[11px] text-yellow-500 leading-snug">
          直方图与单元总数不一致，可能有单元未被统计，请报告此问题。
        </div>
      )}

      <div className="space-y-0.5">
        {warnings.map((text, index) => (
          <div
            key={index}
            className={`flex items-start text-[11px] leading-snug ${
              healthy ? 'text-green-500' : 'text-yellow-500'
            }`}
          >
            {healthy
              ? <CheckCircle2 size={11} className="mr-1 mt-0.5 flex-shrink-0" />
              : <AlertTriangle size={11} className="mr-1 mt-0.5 flex-shrink-0" />}
            <span>{text}</span>
          </div>
        ))}
      </div>

      {quality.worstElements.length > 0 && (
        <div className="text-[10px] text-gray-500 leading-snug">
          最差单元：
          {quality.worstElements.slice(0, 3).map((item, index) => (
            <span key={item.index}>
              {index > 0 ? '、' : ''}
              #{item.index} q={formatQuality(item.quality)}
            </span>
          ))}
        </div>
      )}

      {/* 后端结论原样显示：它带着"形状好≠够细"这句限定，改写会丢掉这个限定 */}
      {quality.verdict && (
        <div className="text-[11px] text-gray-500 leading-snug">{quality.verdict}</div>
      )}
    </div>
  );
}
