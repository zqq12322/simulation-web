# ============================================================
#  SimCloud AI - 回归验证脚本
#  静态检查 + 构建 + API 冒烟 + 物理校准（解析解对照）
#  用法: powershell -ExecutionPolicy Bypass -File scripts\verify.ps1
#  前提: 后端已在 8000 端口运行 (scripts\dev.ps1)
# ============================================================
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $root 'frontend'
$API = 'http://127.0.0.1:8000'

$script:failed = 0
function Check($name, $condition, $detail) {
    if ($condition) {
        Write-Host ("  [PASS] {0,-42} {1}" -f $name, $detail) -ForegroundColor Green
    } else {
        Write-Host ("  [FAIL] {0,-42} {1}" -f $name, $detail) -ForegroundColor Red
        $script:failed++
    }
}

Write-Host '=== 1/4 前端静态检查 (tsc --noEmit) ===' -ForegroundColor Cyan
Push-Location $frontend
node node_modules\typescript\bin\tsc --noEmit 2>&1 | Out-Null
Check 'TypeScript 类型检查' ($LASTEXITCODE -eq 0) "exit=$LASTEXITCODE"
Pop-Location

Write-Host "`n=== 2/4 后端可达性 ===" -ForegroundColor Cyan
try {
    $health = Invoke-RestMethod "$API/" -TimeoutSec 5
    Check 'GET /' ($null -ne $health.message) $health.message
    $apiUp = $true
} catch {
    Check 'GET /' $false '后端未启动，请先运行 scripts\dev.ps1'
    $apiUp = $false
}

if ($apiUp) {
    Write-Host "`n=== 3/4 API 冒烟测试 ===" -ForegroundColor Cyan

    # 材料库（含 type 字段，前端分类筛选依赖它）
    $mats = Invoke-RestMethod "$API/api/materials"
    Check '材料库' ($mats.Count -gt 0 -and $null -ne $mats[0].type) "$($mats.Count) 种，type=$($mats[0].type)"

    # 几何元数据：面积与法向必须真实（历史上曾全为 null / 0）
    $md = Invoke-RestMethod "$API/api/geometry/test_part.step/metadata"
    $withNormal = @($md.faces | Where-Object { $_.normal }).Count
    $withArea = @($md.faces | Where-Object { $_.area -gt 0 }).Count
    Check 'B-Rep 面数量' ($md.faces.Count -ge 7) "$($md.faces.Count) 个面"
    Check '面法向已提取' ($withNormal -eq $md.faces.Count) "$withNormal/$($md.faces.Count)"
    Check '面面积已提取' ($withArea -eq $md.faces.Count) "$withArea/$($md.faces.Count)"

    # 圆柱面面积 = 2*pi*r*h = 2*pi*3*10
    $cyl = $md.faces | Where-Object { $_.type -eq 'Cylinder' } | Select-Object -First 1
    if ($cyl) {
        $expected = 2 * [math]::PI * 3 * 10
        $err = [math]::Abs($cyl.area - $expected) / $expected
        Check '圆柱面面积解析对照' ($err -lt 0.01) "calc=$([math]::Round($cyl.area,2)) expect=$([math]::Round($expected,2))"
    }

    Write-Host "`n=== 4/4 求解器物理校准 ===" -ForegroundColor Cyan

    # ---- 4a. 全局平衡: 支反力合力 = -施加载荷 ----
    $mesh = Invoke-RestMethod -Method Post "$API/api/generate-mesh?filename=test_part.step&mesh_size=1.2"
    Check '网格生成 (带孔方块)' ($mesh.elements.Count -gt 0) "$($mesh.nodes.Count) 节点 / $($mesh.elements.Count) 单元"

    $fx  = ($mesh.faces | Where-Object { $_.normal[0] -lt -0.9 } | Select-Object -First 1).id
    $fxp = ($mesh.faces | Where-Object { $_.normal[0] -gt  0.9 } | Select-Object -First 1).id
    $bcs = @(
        @{ id='bc1'; name='fixed'; type='fixed'; applicationType='face'; entityIndex=$fx },
        @{ id='bc2'; name='pull';  type='force'; applicationType='face'; entityIndex=$fxp; force=@{ x=1000; y=0; z=0 } }
    )
    $body = @{ geometry_filename='test_part.step'; material_id='structural_steel'; boundary_conditions=$bcs; faces=$mesh.faces } | ConvertTo-Json -Depth 8
    $res = Invoke-RestMethod -Method Post "$API/api/solve" -Body $body -ContentType 'application/json'
    Check '求解返回' ($res.status -eq 'solved') "status=$($res.status)"

    $sum = 0.0
    foreach ($p in $res.reaction_forces.PSObject.Properties) { $sum += $p.Value[0] }
    Check '支反力与载荷守恒' ([math]::Abs($sum + 1000) -lt 1.0) "sum_x=$([math]::Round($sum,3)) (应为 -1000)"

    $nan = @($res.stresses | Where-Object { [double]::IsNaN([double]$_) }).Count
    Check '应力无 NaN' ($nan -eq 0) "NaN=$nan, max=$([math]::Round($res.max_stress,2))"

    # ---- 4b. 解析解对照: 立方体单轴拉伸, 加载面中心位移 vs FL/AE ----
    $cube = Invoke-RestMethod -Method Post "$API/api/generate-mesh?filename=default_cube.step&mesh_size=1.5"
    # 关键: 必须按真实法向挑面, 不能假设 face 1/6 是 ±X (曾经因此误判)
    $minusX = ($cube.faces | Where-Object { $_.normal[0] -lt -0.9 } | Select-Object -First 1).id
    $plusX  = ($cube.faces | Where-Object { $_.normal[0] -gt  0.9 } | Select-Object -First 1).id
    $bcs2 = @(
        @{ id='bc1'; name='fixed'; type='fixed'; applicationType='face'; entityIndex=$minusX },
        @{ id='bc2'; name='pull';  type='force'; applicationType='face'; entityIndex=$plusX; force=@{ x=1000; y=0; z=0 } }
    )
    $body2 = @{ geometry_filename='default_cube.step'; material_id='structural_steel'; boundary_conditions=$bcs2; faces=$cube.faces } | ConvertTo-Json -Depth 8
    $res2 = Invoke-RestMethod -Method Post "$API/api/solve" -Body $body2 -ContentType 'application/json'

    # 找最接近加载面中心 (5,0,0) 的节点
    $best = -1; $bd = [double]::MaxValue
    for ($i = 0; $i -lt $cube.nodes.Count; $i++) {
        $n = $cube.nodes[$i]
        $d = [math]::Sqrt([math]::Pow($n[0]-5,2) + [math]::Pow($n[1],2) + [math]::Pow($n[2],2))
        if ($d -lt $bd) { $bd = $d; $best = $i }
    }
    $ux = [double]$res2.displacements[$best][0]
    $analytic = 1000 * 10 / (100 * 2.0e11)   # FL/AE
    $ratio = $ux / $analytic
    # 全约束端使结构略刚于自由杆 => 比值应落在 (0.5, 1.0]; 允许离散误差
    Check '立方体拉伸 vs 解析解 FL/AE' ($ratio -gt 0.5 -and $ratio -lt 1.05) "ratio=$([math]::Round($ratio,3)) (期望 0.5~1.0)"
}

Write-Host "`n============================================" -ForegroundColor Cyan
if ($script:failed -eq 0) {
    Write-Host '  全部通过 ✅' -ForegroundColor Green
} else {
    Write-Host "  $($script:failed) 项失败 ❌" -ForegroundColor Red
}
Write-Host '============================================' -ForegroundColor Cyan
exit $script:failed
