---
name: gpu-jobs
description: 在使用团队 GPU 工作台的项目中，凡是即将运行的步骤需要 GPU，使用此技能提交、等待和查询任务。包括 Agent 修 bug 时自行追加的 CUDA smoke test、pytest、评估、推理、训练，以及脚本间接启动的 GPU 子进程。仅编辑代码或运行 CPU 测试时可直接在工作区完成。
---

在执行命令的当下判断其设备需求，即使原始请求没有提到 GPU。对会自动选择设备的测试，先确认本次要验证的是哪条路径；验证 CUDA 时显式选择 CUDA 或检查实际张量设备，防止 CPU 回退掩盖失败。对包装脚本检查入口、配置和子进程；将需要 GPU 的完整命令作为一个平台任务提交。

使用当前用户的个人 token 通过平台提交任务；CLI 和 MCP 自动从凭据文件读取。

使用服务器上的绝对工作目录、项目解释器和原始参数列表。远程客户端的当前目录和 Python 环境可能与服务器不同。由平台选卡和调度。

- 已连接 gpu-platform MCP 时，用 `submit_gpu_job(cwd, argv, gpu_count=1, wait_seconds=0)`。短测试可设 `wait_seconds=20`。用 `wait_for_task` 继续等待、`read_task_logs` 继续读取、`get_task` 查看退出状态。
- 有 shell 且 MCP 未连接时，用 `gpu-jobs submit --cwd /server/project -- /server/env/bin/python train.py`。短测试例如 `gpu-jobs submit --json --wait --timeout 30 --cwd /server/project -- /server/env/bin/python -m pytest tests/test_cuda.py -q`。
- 一次执行只提交一次。CLI 等待返回 124 时检查结果中的 `done`，MCP 超时检查 `done=false`。继续查询同一任务 ID，等待超时或中断不会取消任务。日志按字节分页，使用返回的 `next_offset` 读取下一页。任务退出码与平台错误字段一起判断结果。
- 提交已发送但结果未知时，先用 `list_my_tasks` 或 `gpu-jobs list --json` 核对目录、命令、时间与任务 ID；无法唯一确认时说明不确定性，避免再次创建任务。平台不可达时保留待执行命令并说明原因。需要取消时按目标任务 ID 调用 `cancel_task` 或 `gpu-jobs cancel`。
- 短测试向用户报告任务 ID、实际状态和关键输出；长任务先返回任务 ID，按用户目标继续跟踪。
