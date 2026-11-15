$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendPath = Join-Path $projectRoot "frontend"
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

# 原生命令失败时显式停止，不能把安装失败误报为启动成功。
if (-not (Test-Path $python)) {
    python -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11+ required'"
    if ($LASTEXITCODE -ne 0) { throw "请安装 Python 3.11 或更高版本。" }
    python -m venv (Join-Path $projectRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "创建 Python 虚拟环境失败。" }
}

# 已装好依赖后不重复联网，便于使用本地快照；首次安装沿用用户 pip 镜像配置。
& $python -c "import importlib.util, sys; sys.exit(0 if all(importlib.util.find_spec(name) for name in ('fastapi', 'uvicorn', 'pydantic', 'tzdata')) else 1)"
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -e $projectRoot
    if ($LASTEXITCODE -ne 0) { throw "安装后端依赖失败，请检查网络和 pip 配置。" }
}
# 拉取新代码可能带来新依赖：依赖目录缺失，或锁定文件比已装依赖更新时重新安装。
$modulesPath = Join-Path $frontendPath "node_modules"
$lockPath = Join-Path $frontendPath "package-lock.json"
$needsInstall = -not (Test-Path $modulesPath)
if ((-not $needsInstall) -and (Test-Path $lockPath)) {
    $needsInstall = (Get-Item $lockPath).LastWriteTime -gt (Get-Item $modulesPath).LastWriteTime
}
if ($needsInstall) {
    npm ci --prefix $frontendPath
    if ($LASTEXITCODE -ne 0) { throw "安装前端依赖失败。" }
}
Write-Host "正在构建前端..."
npm run build --prefix $frontendPath
if ($LASTEXITCODE -ne 0) { throw "前端构建失败。" }

# 使用绝对应用目录，允许从任意 PowerShell 工作目录调用。
Write-Host "正在启动铁路活动范围地图：http://127.0.0.1:8000"
& $python -m uvicorn backend.app.main:app --app-dir $projectRoot --host 127.0.0.1 --port 8000
if ($LASTEXITCODE -ne 0) { throw "服务启动失败，请查看上方错误（依赖、数据目录权限或端口占用）。" }
