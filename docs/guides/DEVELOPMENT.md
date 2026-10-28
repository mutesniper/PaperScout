# 开发与验证

[返回项目首页](../../README.md) · [文档导航](../README.md)

以下命令默认从项目根目录执行。后端测试与前端浏览器测试使用模拟服务，不要求配置真实密钥。

```powershell
.\.venv\Scripts\python -m pytest backend\tests -q
cd frontend
npm run build
```

后端测试通过 `pytest.ini` 定位模块。单元和接口测试使用明确的模拟来源；真实接口验证情况见 [进度记录](../plans/PROGRESS.md)。

前端浏览器测试（需要已安装 Chrome；先执行 `npm run build`）：

```powershell
npx playwright test
```

浏览器测试加载真实生产构建，并只在测试内部模拟 API 响应，不依赖本地端口。若未安装 Chrome，可安装 Playwright Chromium 并调整 `playwright.config.ts` 的浏览器配置。

真实来源冒烟测试（项目根目录；会实际访问两个论文来源）：

```powershell
$env:PYTHONPATH='backend'
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python backend\tests\live_smoke.py
```

`requirements-lock.txt` 保存本次验证的具体 Python 依赖版本，`requirements.txt` 表示允许的版本范围；前端通过 `package-lock.json` 固定依赖。

## 运行边界

本项目面向本地单用户，使用单后端进程。不要启用多个 worker：来源限流、后台任务与取消状态都依赖当前进程。

架构与数据职责见 [目录与架构](../architecture/README.md)。固定样本和真实模型评测见 [评测说明](../evaluation/EVALUATION.md)。
