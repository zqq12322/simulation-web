/**
 * 结果导出（CSV / VTK）——纯函数，把浏览器里已有的数据变成文本。
 *
 * 为什么导出值得先做（而不是先做剖切/动画）
 * ----------------------------------------
 * 它有一个**可以精确验证**的契约：**导出的数值必须与求解结果逐位一致**。
 * 这一条能用两种独立方式钉住：
 *
 * 1. 在本层用 node 直接断言生成的文本（精确到字符）；
 * 2. 把文本送回 Python 解析回来，与**真实的求解结果**逐位比较
 *    （见 `tools/tasks.py` 的 verify 检查）——VTK 是 ParaView 也认的格式，
 *    所以这一步同时验证了"文件是合法的"。
 *
 * 而"剖切面好看不好看"是没有这种判据的。所以先把数据能带出去这件事做对。
 *
 * 为什么在前端做，而不是后端加一个 /export 端点
 * --------------------------------------------
 * 数据本来就已经在浏览器里（求解结果是前端持有的），导出只是把内存里的数组
 * 写成文本。多一次服务端往返既慢又要求把结果重新传上去，还会让"结果在内存里、
 * 重启即丢"这个限制变成"导出也一起丢"。纯函数 + Blob 下载没有任何这些毛病。
 *
 * 精度：用 `String(x)` 而不是 `toFixed`
 * ----------------------------------
 * JavaScript 的 `String(0.1)` 给出**最短且能精确还原**的表示（与 Python 的
 * `repr` 同源算法），所以 `Number(String(x)) === x` 对任意双精度数成立。
 * 写成 `toFixed(6)` 会**静默丢精度**——那正好是"导出看起来没问题、数值已经
 * 不对"这类最难发现的错误。这一条有测试。
 *
 * 行尾：CSV 用 CRLF，VTK 用 LF
 * --------------------------
 * CSV 按 RFC 4180 用 `\r\n`（Excel 在 Windows 上对 LF 的兼容性时好时坏）；
 * VTK 用 `\n`（ParaView 两种都认，而 LF 便于在 Git 里看 diff）。两者都有测试
 * 明确钉住，免得以后有人"顺手统一"成一个而没意识到原因。
 */

/** 导出需要的网格（`elements` 是 **0 基**节点索引，与 VTK 一致）。 */
export interface ExportMesh {
  nodes: number[][];
  elements: number[][];
}

/** 一个标量场（逐节点一个数）。`name` 里请自带单位，例如 `von_mises_Pa`。 */
export interface ExportScalar {
  name: string;
  values: number[];
}

/** 一个矢量场（逐节点一个三分量向量）。`name` 里请自带单位。 */
export interface ExportVector {
  name: string;
  values: number[][];
}

export interface ExportFields {
  scalars?: ExportScalar[];
  vectors?: ExportVector[];
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function isVector3(value: unknown): value is number[] {
  return Array.isArray(value) && value.length >= 3 && value.every(isFiniteNumber);
}

/**
 * 把浮点数写成**能精确还原**的文本。
 *
 * `String()` 给出最短往返表示；`NaN` / `Infinity` 会被拒绝（返回 `null`），
 * 因为它们在 CSV/VTK 里都没有合法写法——写个 `NaN` 进去只会让下游解析器报错，
 * 而且看不出是哪一行的问题。
 */
function numberText(value: unknown): string | null {
  if (!isFiniteNumber(value)) return null;
  // -0 与 0 在数值上等价，但写成 "-0" 会让人以为有符号含义
  if (value === 0) return '0';
  return String(value);
}

/** 校验网格：节点与单元都得是有限数，单元索引必须落在节点范围内。 */
export function validateExportMesh(mesh: ExportMesh | null | undefined): string | null {
  if (!mesh || !Array.isArray(mesh.nodes) || !Array.isArray(mesh.elements)) {
    return '网格数据缺失';
  }
  if (mesh.nodes.length === 0) return '网格里没有节点';
  if (mesh.elements.length === 0) return '网格里没有单元';
  for (const node of mesh.nodes) {
    if (!isVector3(node)) return '节点坐标里有非法数值';
  }
  for (const cell of mesh.elements) {
    if (!Array.isArray(cell) || cell.length < 4) return '单元不是四面体';
    for (const index of cell) {
      if (!Number.isInteger(index) || index < 0 || index >= mesh.nodes.length) {
        return `单元引用了不存在的节点：${index}`;
      }
    }
  }
  return null;
}

/**
 * 校验所有场与网格**长度对得上**。
 *
 * 这条不能省：节点数一旦与场长度不一致，导出的文件会错位——位移被贴到别的
 * 节点上，而文件本身完全合法、能被 ParaView 打开、看起来就是一张云图。
 */
export function validateExportFields(
  mesh: ExportMesh,
  fields: ExportFields | null | undefined,
): string | null {
  if (!fields) return null;
  for (const scalar of fields.scalars || []) {
    if (!Array.isArray(scalar.values) || scalar.values.length !== mesh.nodes.length) {
      return `标量场 ${scalar.name} 的长度（${scalar.values?.length}）与节点数（${mesh.nodes.length}）不一致`;
    }
    for (const value of scalar.values) {
      if (!isFiniteNumber(value)) return `标量场 ${scalar.name} 里有非法数值`;
    }
  }
  for (const vector of fields.vectors || []) {
    if (!Array.isArray(vector.values) || vector.values.length !== mesh.nodes.length) {
      return `矢量场 ${vector.name} 的长度（${vector.values?.length}）与节点数（${mesh.nodes.length}）不一致`;
    }
    for (const value of vector.values) {
      if (!isVector3(value)) return `矢量场 ${vector.name} 里有非法向量`;
    }
  }
  return null;
}

/** CSV 单元格转义（RFC 4180）：含逗号/引号/换行时用双引号包起来。 */
export function csvCell(text: string): string {
  if (/[",\r\n]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

/**
 * 生成逐节点结果表（CSV，RFC 4180 行尾 `\r\n`）。
 *
 * 列顺序：节点索引、坐标、各矢量场分量、各标量场。表头里带单位
 * （`ux_m`、`von_mises_Pa`），因为导出文件最常见的用途就是给别人看，
 * 而"这个数是米还是毫米"必须写在文件里，不能靠口头约定。
 *
 * 返回 `null` 表示数据不可用（调用方负责提示），而不是返回半张表。
 */
export function buildResultCsv(
  mesh: ExportMesh | null | undefined,
  fields: ExportFields | null | undefined,
): string | null {
  const meshError = validateExportMesh(mesh);
  if (meshError || !mesh) return null;
  const fieldsError = validateExportFields(mesh, fields);
  if (fieldsError) return null;

  const header = ['node', 'x', 'y', 'z'];
  for (const vector of fields?.vectors || []) {
    // 三分量场展开成三列：ux/uy/uz 好写也好解析
    header.push(`${vector.name}_x`, `${vector.name}_y`, `${vector.name}_z`);
  }
  for (const scalar of fields?.scalars || []) {
    header.push(scalar.name);
  }

  const lines: string[] = [header.map(csvCell).join(',')];
  for (let index = 0; index < mesh.nodes.length; index += 1) {
    const node = mesh.nodes[index];
    const row: Array<string | null> = [
      String(index),
      numberText(node[0]),
      numberText(node[1]),
      numberText(node[2]),
    ];
    for (const vector of fields?.vectors || []) {
      const value = vector.values[index];
      row.push(numberText(value[0]), numberText(value[1]), numberText(value[2]));
    }
    for (const scalar of fields?.scalars || []) {
      row.push(numberText(scalar.values[index]));
    }
    // 任一格写不出来就整体放弃——不生成"缺了几格"的文件
    if (row.some(cell => cell === null)) return null;
    lines.push((row as string[]).join(','));
  }
  // 末尾补一个换行：很多工具（Excel、pandas）对没有收尾换行的文件会报"意外结束"
  return `${lines.join('\r\n')}\r\n`;
}

/**
 * 生成 VTK legacy（ASCII，UNSTRUCTURED_GRID）文件。
 *
 * 选 legacy ASCII 而不是 XML/VTU 的理由：**人可读、可 diff、可手写解析**，
 * 所以 verify 里能用几十行 Python 把它读回来逐位比对。ParaView 也直接认。
 *
 * 标题行用 ASCII（`SimCloud AI result export`）而不是中文：VTK 的标题是
 * 定长字节字段，某些读取器遇到非 ASCII 会移位——这里不值得冒险。
 */
export function buildLegacyVtk(
  mesh: ExportMesh | null | undefined,
  fields: ExportFields | null | undefined,
  title = 'SimCloud AI result export',
): string | null {
  const meshError = validateExportMesh(mesh);
  if (meshError || !mesh) return null;
  const fieldsError = validateExportFields(mesh, fields);
  if (fieldsError) return null;

  const scalars = fields?.scalars || [];
  const vectors = fields?.vectors || [];

  const lines: string[] = [
    '# vtk DataFile Version 3.0',
    title,
    'ASCII',
    'DATASET UNSTRUCTURED_GRID',
    `POINTS ${mesh.nodes.length} double`,
  ];
  for (const node of mesh.nodes) {
    lines.push(`${numberText(node[0])} ${numberText(node[1])} ${numberText(node[2])}`);
  }

  // CELLS 的第二列是"连接表里一共有多少个数"（每行 1 + 4）
  lines.push(`CELLS ${mesh.elements.length} ${mesh.elements.length * 5}`);
  for (const cell of mesh.elements) {
    lines.push(`4 ${cell[0]} ${cell[1]} ${cell[2]} ${cell[3]}`);
  }

  // 10 = VTK_TETRA。四个节点就是四面体，这里不做退化判断——
  // 网格质量由 /api/mesh-quality 负责，导出只负责不撒谎。
  lines.push(`CELL_TYPES ${mesh.elements.length}`);
  for (let index = 0; index < mesh.elements.length; index += 1) {
    lines.push('10');
  }

  if (scalars.length > 0 || vectors.length > 0) {
    lines.push(`POINT_DATA ${mesh.nodes.length}`);
    for (const vector of vectors) {
      lines.push(`VECTORS ${vector.name} double`);
      for (const value of vector.values) {
        lines.push(`${numberText(value[0])} ${numberText(value[1])} ${numberText(value[2])}`);
      }
    }
    for (const scalar of scalars) {
      // 尾部的 1 是"每个元组的分量数"；LOOKUP_TABLE 行是 legacy 格式的硬性要求
      lines.push(`SCALARS ${scalar.name} double 1`);
      lines.push('LOOKUP_TABLE default');
      for (const value of scalar.values) {
        lines.push(String(numberText(value)));
      }
    }
  }

  return `${lines.join('\n')}\n`;
}

/**
 * 导出文件名：`<stem>_<suffix>.<ext>`，并把非法字符换成 `_`。
 *
 * 文件名来自用户的几何名（可能是 `零件1.STEP`、也可能被人改得很奇怪），
 * 所以必须清洗：路径分隔符、盘符、控制字符都不能进文件名。
 * 中文保留——它是合法且常见的，抹掉只会让用户对不上文件。
 */
export function exportFilename(stem: unknown, suffix: unknown, extension: unknown): string {
  const cleanStem = String(stem ?? '')
    // 先砍掉目录部分（含 Windows 反斜杠），避免把路径写进文件名
    .split(/[\\/]/).pop() || '';
  const base = cleanStem.replace(/\.[A-Za-z0-9]{1,8}$/, '') || 'result';
  const cleanSuffix = String(suffix ?? '').replace(/[^0-9A-Za-z_-]/g, '');
  const cleanExtension = String(extension ?? 'txt').replace(/[^0-9A-Za-z]/g, '') || 'txt';
  const safeBase = base.replace(/[^\w\u4e00-\u9fff.-]/g, '_').replace(/^[.\-]+/, '') || 'result';
  const parts = [safeBase];
  if (cleanSuffix) parts.push(cleanSuffix);
  return `${parts.join('_')}.${cleanExtension}`;
}

/** 一个稳定的时间戳片段（用于文件名），例如 `20260318-142530`。 */
export function filenameStamp(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, '0');
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
    '-',
    pad(date.getHours()),
    pad(date.getMinutes()),
    pad(date.getSeconds()),
  ].join('');
}

/** 可直接显示的导出失败原因（拿不到数据时提示用户，而不是静默失败）。 */
export function describeExportProblem(
  mesh: ExportMesh | null | undefined,
  fields: ExportFields | null | undefined,
): string | null {
  const meshError = validateExportMesh(mesh);
  if (meshError) return `无法导出：${meshError}。请先生成网格并求解。`;
  if (!mesh) return '无法导出：网格数据缺失。';
  const fieldsError = validateExportFields(mesh, fields);
  if (fieldsError) return `无法导出：${fieldsError}。`;
  if (!fields || ((fields.scalars?.length || 0) === 0 && (fields.vectors?.length || 0) === 0)) {
    return '无法导出：没有任何结果场（请先求解）。';
  }
  return null;
}
