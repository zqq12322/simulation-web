import React from 'react';
import { AlertTriangle, Clock, Loader2, Trash2, TrendingUp } from 'lucide-react';
import {
  RunAnalysis,
  RunGroup,
  RunList,
  RunRecord,
  describeAnalysisType,
  describeRunMesh,
  describeRunWarnings,
  historyIsTrimmed,
  runQuantityNames,
  shouldShowGroup,
} from '../utils/runsApi';
import { QUANTITY_DISPLAY, formatQuantity, quantityLabel } from '../utils/convergenceStudy';
import { formatCreatedAt } from '../utils/projectsApi';

interface RunHistoryPanelProps {
  list: RunList | null;
  /** 跨运行对比（按配置签名分组）。取不到时为 null，界面只是不显示这一段。 */
  analysis?: RunAnalysis | null;
  loading?: boolean;
  error?: string | null;
  /** 能否删除（只有属主/editor 能写——与后端 `can_edit` 一致）。 */
  canEdit?: boolean;
  onDelete?: (runId: string) => void;
  /** 正在删除的那一条（按钮转圈）。 */
  deletingId?: string | null;
}

/**
 * 求解记录面板：这个项目跑过哪些算例、各自什么网格、结果多少。
 *
 * 三个刻意的取舍：
 *
 * 1. **数值用与收敛检查同一套格式化**（`convergenceStudy.formatQuantity`）：
 *    同一个量在两个面板里必须显示成同一个数，否则用户会怀疑哪个才是对的。
 * 2. **空历史是一句正常的话**，不是错误（"这个项目还没跑过求解"）。
 *    报红会让人以为功能坏了。
 * 3. **摘要损坏的记录要标出来**：库里那条 JSON 坏了（例如手工改过库）时，
 *    后端会返回 `summaryParseError`。不标出来，用户会把"空摘要"读成
 *    "这次什么都没算出来"。
 */
export default function RunHistoryPanel({
  list,
  analysis = null,
  loading = false,
  error = null,
  canEdit = false,
  onDelete,
  deletingId = null,
}: RunHistoryPanelProps) {
  if (loading) {
    return (
      <div className="ml-10 py-1 pr-3 flex items-center text-[11px] text-gray-500">
        <Loader2 size={11} className="mr-1 animate-spin" />
        正在读取求解记录…
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

  if (!list || list.runs.length === 0) {
    return (
      <div className="ml-10 py-1 pr-3 text-[11px] text-gray-500">
        这个项目还没有求解记录。跑一次求解后会自动记下来。
      </div>
    );
  }

  return (
    <div className="ml-10 py-1 pr-3 space-y-1">
      {/* 跨运行对比：同一套配置下的几次运行，结果收敛了吗 */}
      {(analysis?.groups || []).filter(shouldShowGroup).map((group: RunGroup) => (
        <div
          key={`${group.analysisType}-${group.setupSignature ?? 'none'}`}
          className="rounded border border-[#2b3040] bg-[#151a24] px-2 py-1 space-y-0.5"
        >
          <div className="flex items-center text-[10px] text-gray-400">
            <TrendingUp size={10} className="mr-1 flex-shrink-0" />
            <span>
              跨运行对比 · {describeAnalysisType(group.analysisType)} ·{' '}
              {quantityLabel(null, group.quantity)} · {group.distinctMeshCount} 个网格
            </span>
          </div>
          {group.comparable && group.values.length > 1 && (
            <div className="text-[10px] text-gray-500">
              粗 → 细：
              {group.values
                .map(value => formatQuantity(value, group.quantity))
                .join('  →  ')}
            </div>
          )}
          <div
            className={`text-[10px] leading-snug ${
              group.comparable === false
                ? 'text-gray-500'
                : group.converged ? 'text-green-500' : 'text-yellow-500'
            }`}
          >
            {group.verdict}
          </div>
        </div>
      ))}

      {list.runs.map((run: RunRecord) => {
        const meshLine = describeRunMesh(run);
        const warningLine = describeRunWarnings(run);
        const names = runQuantityNames(run);
        return (
          <div
            key={run.id}
            className="rounded border border-[#2b3040] bg-[#191d28] px-2 py-1 space-y-0.5"
          >
            <div className="flex items-center text-[10px] text-gray-500">
              <Clock size={10} className="mr-1 flex-shrink-0" />
              <span>{formatCreatedAt(run.createdAt)}</span>
              <span className="ml-2 text-gray-400">
                {describeAnalysisType(run.analysisType)}
              </span>
              {canEdit && onDelete && (
                <button
                  onClick={(e) => { e.stopPropagation(); onDelete(run.id); }}
                  disabled={deletingId === run.id}
                  className="ml-auto text-gray-500 hover:text-red-400 disabled:opacity-40"
                  title="删除这条记录"
                >
                  <Trash2 size={11} />
                </button>
              )}
            </div>

            {meshLine && (
              <div className="text-[10px] text-gray-500">{meshLine}</div>
            )}

            {run.summaryParseError ? (
              <div className="text-[10px] text-yellow-500">
                这条记录的摘要已损坏，无法显示数值（后端日志里有说明）。
              </div>
            ) : (
              <div className="text-[10px] text-gray-300 space-y-0.5">
                {names.map(name => (
                  <div key={name} className="flex justify-between">
                    <span className="text-gray-500">{quantityLabel(null, name)}</span>
                    <span>{formatQuantity(run.summary.quantities[name], name)}</span>
                  </div>
                ))}
              </div>
            )}

            {warningLine && (
              <div className="text-[10px] text-yellow-600">{warningLine}</div>
            )}
          </div>
        );
      })}

      {historyIsTrimmed(list) && (
        <div className="text-[10px] text-gray-500">
          记录数已达上限（{list.limit} 条），更早的记录已被自动裁掉。
        </div>
      )}
    </div>
  );
}
