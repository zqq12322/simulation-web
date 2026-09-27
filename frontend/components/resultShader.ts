/**
 * 结果云图的着色器：按标量场（应力 / 温度）着彩虹色，**并显示真实变形**。
 *
 * 为什么单独成文件
 * ----------------
 * 这段 GLSL 曾经内联在 `Scene3D.tsx` 里，而且有一个不发声的错误：
 *
 * ```glsl
 * uniform float deformationScale;   // 声明了
 * // Apply deformation (placeholder - needs displacement attribute)
 * vec3 deformedPosition = position; // 但从来没被用过
 * ```
 *
 * 于是"求解完成"的云图其实画的是**未变形的几何**：悬臂梁受载后看起来和空载
 * 一模一样，只有颜色在变。用户无法从图上判断变形方向和量级，
 * 而这恰恰是结构分析最主要的输出之一。更糟的是它不报错、不影响任何数值，
 * 只做静态检查根本发现不了。
 *
 * 抽出来之后，`tools/tasks.py verify` 会直接检查这个文件里的契约
 * （见 `_check_result_shader_contract`）：位移属性必须存在、必须参与
 * 顶点位置的最终计算，且 **`deformedPosition` 不允许等于 `position`**。
 */

export interface ResultShader {
  vertexShader: string;
  fragmentShader: string;
}

export const SimulationResultShader: ResultShader = {
  vertexShader: `
    varying vec3 vPosition;
    varying vec3 vNormal;
    varying float vStress;
    attribute float stress;
    attribute vec3 displacement;
    uniform float deformationScale;

    void main() {
      vPosition = position;
      vStress = stress;

      // 变形显示：position 是几何坐标，displacement 是后端解出的节点位移
      // （与几何同一长度单位），deformationScale 是**无量纲放大系数**。
      // 真实位移通常远小于模型尺寸（钢材 1e-5 量级），必须放大才看得见；
      // 系数由 utils/deformation.ts 按"模型尺度的固定比例"算出，
      // 因此不会出现"小零件看不见、大零件夸张到离谱"。
      vec3 deformedPosition = position + displacement * deformationScale;

      // 法向仍用未变形的值：小变形下法向变化是二阶小量，而按变形梯度
      // 精确变换需要在顶点着色器里重建局部雅可比，代价与收益不成比例。
      // 这一点只影响光照观感，不影响颜色映射与几何形状。
      vNormal = normal;

      gl_Position = projectionMatrix * modelViewMatrix * vec4(deformedPosition, 1.0);
    }
  `,
  fragmentShader: `
    varying vec3 vPosition;
    varying vec3 vNormal;
    varying float vStress;
    uniform float minVal;
    uniform float maxVal;
    uniform vec3 color1; // Blue
    uniform vec3 color2; // Cyan
    uniform vec3 color3; // Green
    uniform vec3 color4; // Yellow
    uniform vec3 color5; // Red

    // Function to map value to rainbow color
    vec3 getRainbowColor(float value, float min, float max) {
      float t = clamp((value - min) / (max - min), 0.0, 1.0);

      if (t < 0.25) return mix(color1, color2, t * 4.0);
      if (t < 0.5) return mix(color2, color3, (t - 0.25) * 4.0);
      if (t < 0.75) return mix(color3, color4, (t - 0.5) * 4.0);
      return mix(color4, color5, (t - 0.75) * 4.0);
    }

    void main() {
      // Use the actual stress value passed from vertex shader
      vec3 color = getRainbowColor(vStress, minVal, maxVal);

      // Add simple lighting
      vec3 lightDir = normalize(vec3(1.0, 1.0, 1.0));
      float diff = max(dot(normalize(vNormal), lightDir), 0.3);

      gl_FragColor = vec4(color * diff, 1.0);
    }
  `,
};

export default SimulationResultShader;
