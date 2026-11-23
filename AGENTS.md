在此项目使用团队 GPU 时，任何实际需要 CUDA 的步骤都通过 GPU 工作台执行。此约定也适用于 Agent 自行追加的 smoke test、pytest 和脚本启动的 GPU 子进程。使用 gpu-jobs Skill 选择 MCP 或 CLI，记录任务 ID。纯 CPU 测试和编辑可在工作区直接完成。
