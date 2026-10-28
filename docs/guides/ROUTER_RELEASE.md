# 发布最终工具路由适配器

已发布：[router-v1.0.0 实验预发布](https://github.com/mutesniper/PaperScout/releases/tag/router-v1.0.0)。ZIP和SHA256附件均已上传，GitHub返回的文件哈希与本地一致。本地未提交的业务代码及文档没有随此发布上传；下方步骤供后续版本参考。

2026-10-28本地核验完成：从ZIP解压到独立目录，使用已有固定版本基座，包内和基座SHA256均通过，36条参考原始输出全部一致。没有执行重新下载基座或全新环境安装测试；后续恢复仍需能够获取同版本基座与依赖。

## 上传哪些文件

本地发布目录：`outputs/router/releases/`。

- `paperscout-router-v1.0.0.zip`：完整适配器恢复包，约21MiB。
- `paperscout-router-v1.0.0.zip.sha256`：压缩包SHA256。

发布说明正文在`outputs/router/releases/paperscout-router-v1.0.0/RELEASE_NOTES.md`，也可在压缩包内找到。不要上传完整基座、`.env`、其他训练检查点或整个`outputs`目录。

## GitHub网页操作

1. 先提交并推送准备随版本发布的代码和文档，确认目标提交包含最终训练与评测说明。发布包本身位于Git忽略目录，通过Release附件上传即可，不必强制加入Git。
2. 打开[PaperScout Releases](https://github.com/mutesniper/PaperScout/releases)，点击 **Draft a new release**。
3. 在 **Choose a tag** 中输入 `router-v1.0.0`，选择创建新标签；**Target**选择上一步已推送的提交或包含该提交的分支。若标签已存在且用于其他版本，应换一个新标签。
4. 标题填写：`PaperScout Router v1.0.0 — 最终实验适配器`。
5. 将`RELEASE_NOTES.md`内容粘贴到说明区域。
6. 在附件区上传ZIP和SHA256两个文件，等待上传完成。建议将此独立模型实验标记为 **pre-release**，与应用正式版本区分。
7. 核对附件、标签及说明，点击 **Publish release**。也可以先点 **Save draft**保留草稿。

GitHub自动生成的“Source code (zip)”和“Source code (tar.gz)”是该标签的源码快照，不包含Git忽略的模型权重；恢复时应下载手动上传的`paperscout-router-v1.0.0.zip`。

## 发布后核验

从Release页面重新下载ZIP和SHA256文件。Windows PowerShell可计算：

```powershell
Get-FileHash .\paperscout-router-v1.0.0.zip -Algorithm SHA256
Get-Content .\paperscout-router-v1.0.0.zip.sha256
```

两处哈希应一致。解压后先执行`python restore.py --verify-only`；准备好同版本基座后执行包内README中的`--self-test`。确认下载完整、能够恢复后，再决定是否删除本地模型。恢复包不含完整基座，长期离线存档需另行备份基座。

操作依据：[GitHub官方Release管理说明](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)。
