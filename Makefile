# SimCloud AI — 跨平台任务入口（Linux / macOS / Windows-with-make）
#
# 所有任务都委托给 tools/tasks.py（纯标准库 Python），因此 Windows 用户
# 也可以直接用 `python tools/tasks.py <task>` 而不需要 make。
#
#   make doctor   环境体检
#   make setup    安装依赖（venv + pip + npm）
#   make dev      同时启动前后端（Ctrl+C 一起停）
#   make test     后端回归测试（不需要启动服务）
#   make verify   端到端验证 + 解析解校准（需要服务已启动）
#   make build    前端类型检查 + 生产构建
#   make clean    清理 dist 与 .msh 缓存

PYTHON ?= python3

.PHONY: help doctor setup dev test verify build clean

help:
	@echo "可用任务："
	@echo "  make doctor   环境体检"
	@echo "  make setup    安装依赖（venv + pip + npm）"
	@echo "  make dev      同时启动前后端"
	@echo "  make test     后端回归测试（无需服务）"
	@echo "  make verify   端到端验证 + 解析解校准（需服务已启动）"
	@echo "  make build    前端类型检查 + 生产构建"
	@echo "  make clean    清理 dist 与 .msh 缓存"

doctor:
	$(PYTHON) tools/tasks.py doctor

setup:
	$(PYTHON) tools/tasks.py setup

dev:
	$(PYTHON) tools/tasks.py dev

test:
	$(PYTHON) tools/tasks.py test

verify:
	$(PYTHON) tools/tasks.py verify

build:
	$(PYTHON) tools/tasks.py build

clean:
	$(PYTHON) tools/tasks.py clean
