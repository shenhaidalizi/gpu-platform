# GPU 工作台

在一台 Linux GPU 服务器上编辑代码、打开终端、启动 JupyterLab 和排队运行训练。浏览器与 Agent 共用任务 API。当前配置只纳管 haigpu9 的 GPU 7，资源标识为 `GPU-0fb4b2c7-a39e-2032-c5c9-3eabf97816ba`。

## 打开已运行的工作台

可访问 haigpu9 实验室内网的电脑直接打开 <http://10.249.41.63:8765/>。服务通过配置项 `lan_host` 监听内网接口，并保留本机入口。此地址提供 HTTP，适用于受信内网。当前部署已启用 23 个 Linux 账号。成员通过 SSH 登录自己的账号，在个人终端读取 `~/.config/gpu-platform/access.token`，使用个人凭据登录网页。使用 SSH 隧道可加密凭据传输。

通过 SSH 访问时，也可以在自己的电脑上建立 SSH 转发，然后打开 <http://127.0.0.1:8765>。在 VS Code Remote SSH 中也可以通过“端口”面板转发服务器的 8765 端口。

```bash
ssh -N -L 8765:127.0.0.1:8765 zhousy@haigpu9
```

首次初始化会将当前用户的登录凭据写入服务器上的 `~/.config/gpu-platform/access.token`，文件权限为 0600。网页以该凭据登录。普通终端申请 0 张卡，GPU 终端按申请卡数排队。新建 Jupyter 直接获得纳管 GPU 的访问权，无需训练队列名额。Jupyter 启动后，会话详情提供其本机地址和对应的 SSH 转发命令。关闭标签页可以重连，点「结束」会终止这个会话的内核和子进程。

团队成员可在工作台左侧选择「安装客户端」，或从实验室网络打开[服务器下载页](http://10.249.41.63:8765/downloads/)。按需下载 CLI、MCP 或 Skill，然后在自己的电脑上从本地文件安装。

## 已部署的多用户服务

haigpu9 使用系统级 `gpu-platform-api.service` 和 `gpu-platform-runner.service`。API 以独立的 `gpu-platform-api` 系统账号运行，任务和文件操作以提交者自己的 Linux 身份执行。运行配置在 `/etc/gpu-platform/config.toml`，安装目录为 `/opt/gpu-platform`。可用 `systemctl status gpu-platform-api gpu-platform-runner` 查看服务状态。历史任务保存在 `/var/lib/gpu-platform`，运行收据和日志保存在 `/var/lib/gpu-platform-runner`。

文件编辑器允许访问本人拥有的 `/d/<用户名>` 和 `/e/<用户名>`。任务的文件系统视图在这两块磁盘下仅挂载本人的目录。分配给任务的 GPU 设备在其运行环境中可用。系统服务定义位于 `deploy/system/`。

「服务器状态 → 磁盘存储」默认按文件所有者显示 `/d`、`/e` 的用量。各账号按文件实际分配的磁盘块计量，硬链接在同一盘只计一次。图例可展开全部账号，同时列出可写空间、系统预留和未归属容量。用户视图显示扫描完成时的快照；「按空间」可查看当前容量。

系统级 `gpu-platform-disk-usage.timer` 在上次扫描结束 6 小时后再次采集。低优先级服务只读取文件元数据，每盘完成后原子更新 `/var/lib/gpu-platform-disk-usage/{d,e}.json`。部署时将 `deploy/disk_owners.py` 安装到 `/opt/gpu-platform/disk_owners.py`，将 `deploy/system/gpu-platform-disk-usage.{service,timer}` 安装到 `/etc/systemd/system/`，再运行 `systemctl daemon-reload` 和 `systemctl enable --now gpu-platform-disk-usage.timer`。

## 从源码运行单账号开发实例

本机已验证系统 Python 3.10、systemd 249、cgroup v2、NVIDIA 驱动和 Node 24。平台虚拟环境和用户训练环境分别配置。按以下步骤进行首次安装；已有配置时跳过 init。

```bash
cd ~/projects/gpu-platform
/usr/bin/python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
npm --prefix web ci
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python deploy/build_client_downloads.py
npm --prefix web run build
.venv/bin/gpu-platform init
systemctl --user link "$PWD/deploy/systemd/gpu-platform-runner.service" "$PWD/deploy/systemd/gpu-platform-api.service"
systemctl --user daemon-reload
systemctl --user start gpu-platform-api.service
curl --noproxy '*' http://127.0.0.1:8765/api/health
```

两个用户级 unit 的默认源码路径是 `%h/projects/gpu-platform`。配置文件默认位于 `~/.config/gpu-platform/config.toml`，也可通过 `GPU_PLATFORM_CONFIG` 指定。修改后重启两个服务使配置生效。状态库、运行日志和退出收据由 `state_dir` 指定。保留日志以供用户按任务 ID 查看。

```bash
journalctl --user -u gpu-platform-api -u gpu-platform-runner -n 50
systemctl --user restart gpu-platform-api
.venv/bin/pytest -q
```

## 一张 GPU 运行多个任务

网页和 Agent 只提交命令、目录和 GPU 张数。首次遇到的单卡 Python 训练按队列单独启动，平台采集任务显存和整卡计算利用率。任务成功结束、采样连续、具有至少三个间隔采样点且采样期间仅观察到该任务使用 GPU 时，平台按用户、工作目录、解释器、命令、脚本和显式配置文件的内容指纹保存该卡的历史峰值。记录存放在 `state_dir/platform.db` 的 `gpu_profiles` 表，以任务指纹和 GPU UUID 为主键，保留有效采样中的最高值。任务记录中的 `tasks.profile_key` 可关联该表。同一目录中复制或改名的入口脚本，只要内容、解释器、参数和显式配置相同，就复用指纹。旧版记录在再次识别到原命令和原内容时迁移。修改脚本或显式配置后重新采样。导入的依赖和运行时加载的数据可能变化。复杂 Shell 命令、GPU 终端、多卡训练和采样不足的任务按整卡排队。Jupyter 按需访问，其实际显存消耗计入设备用量。旧版客户端传入的显存预算会被忽略。

默认每卡最多同时分配两个普通任务。按历史采样峰值的 1.25 倍再加 512 MiB 估计每个任务的显存，整卡另留 2048 MiB。双任务共享要求每个任务的历史采样计算利用率峰值严格低于 45%。启动第二个任务还要求当时整卡计算利用率不超过 80%，且显存余量足够。调度器预留后，runner 在启动前复核。管理员可调整 `shared_limit` 和 `shared_headroom_mib`。历史估计用于准入，无法限制 CUDA 程序后续的显存申请。若按预算启动的任务失败，平台作废其估计并重新收集独立运行记录。GPU 队首等待时后续训练继续排队。任务退出后仅释放本次分配，调度器立即检查队列。

### 全机 GPU 用量观测

工作台约每 5 秒对本机所有 GPU 做一次只读采样。调度范围由 `gpu_uuids` 配置。SQLite 表 `gpu_usage_devices` 存每卡最新显存、计算利用率、客户端数量和采样时间。`gpu_usage_processes` 按系统启动标识、GPU UUID、PID 和进程启动时刻累计峰值，长期保留每个进程在每张卡上的汇总记录。重复采样更新同一条记录的最后观测时间和峰值。平台进程通过 `run` 关联任务。平台外进程在能够以已配置用户身份确认入口脚本时关联指纹。采集库仅保存平台外程序的指纹和用量。

`peak_sm` 是驱动返回的进程计算利用率峰值，`peak_solo_util` 是只观察到一个单卡进程时的整卡利用率峰值。缺失读数为空。完全落在两次采样之间的短任务或瞬时显存峰值可能遗漏。具有匹配指纹的单卡工作按指纹、GPU 型号和容量更新 `gpu_usage_peaks`。调度取已验证单独运行记录与同型号同容量观测记录中的较高值。仅有平台外进程的部分观测时，首次提交仍安排单独运行。

## CLI、MCP 和 Agent Skill

工作台「安装客户端」提供独立发行包。先把需要的文件保存到同一目录，再从本地 wheel 安装；Skill 直接复制到技能目录。也可使用自己平时登录 GPU 服务器的 SSH 别名，通过 scp 从 `/opt/gpu-platform/web/downloads/` 取包。连接服务时单独设置 `GPU_PLATFORM_URL`，填写实际运行客户端的电脑可达的工作台根地址。

| 使用方式 | 安装内容 |
|---|---|
| 终端 | 安装 CLI wheel，激活客户端虚拟环境后运行 `gpu-jobs platform` |
| MCP | 同时安装 MCP wheel 和共享客户端 wheel。以 `gpu-platform-mcp` 的绝对路径注册 stdio 服务 |
| Skill | 将独立的 `SKILL.md` 保存到 `~/.agents/skills/gpu-jobs/SKILL.md`，新开 Agent 会话。执行任务依赖已连接的 MCP 或 PATH 中的 `gpu-jobs` |

默认连接 `http://127.0.0.1:8765`，从客户端机器的 `~/.config/gpu-platform/access.token` 读取当前用户凭据。自定义地址或凭据文件可设置 `GPU_PLATFORM_URL`（平台根地址）和 `GPU_PLATFORM_TOKEN_FILE`，远程访问使用 SSH 转发或 HTTPS。MCP 在启动进程的客户端环境中读取这些设置。任务的工作目录、解释器和数据路径始终按 GPU 服务器上的路径解释。

### 提交和等待

以下命令中的路径替换为服务器上的项目目录和环境。命令各参数在 `--` 后逐个传递。同一个任务可跨入口查询。

```bash
gpu-jobs submit --cwd /srv/project -- /srv/env/bin/python train.py
gpu-jobs submit --wait --timeout 30 --json --cwd /srv/project -- /srv/env/bin/python -m pytest -q tests/test_cuda.py
gpu-jobs wait TASK_ID --timeout 30 --json
gpu-jobs logs TASK_ID --offset 0 --json
gpu-jobs status TASK_ID
gpu-jobs list --json
gpu-jobs platform
gpu-jobs cancel TASK_ID
```

MCP 提供 `submit_gpu_job`、`wait_for_task`、`get_task`、`list_my_tasks`、`read_task_logs`、`get_platform_status` 和 `cancel_task`。提交短测试时传 `wait_seconds=20`，长任务默认立即返回 ID。等待返回 `task`、`done` 和一页 `logs`。日志可使用 `next_offset` 继续读取。CLI 等待成功返回 0，任务失败时返回进程可用的非零退出码，缺失时返回 1。等待到期时返回 124 且 `done=false`，排队和运行继续。提交响应丢失时先查看自己的任务并核对创建时间、命令和目录；重复提交会创建新的任务。

在使用平台的科研项目里，把本仓库 [AGENTS.md](AGENTS.md) 中的 GPU 规则合入该项目的 Agent 指令。规则适用于 Agent 自行追加的 CUDA smoke test 或 pytest。Skill 的触发依赖 Agent 在执行前识别设备需求；普通 shell 命令不会被入口自动拦截。

维护者从源码构建下载包时，在仓库根目录执行 `python deploy/build_client_downloads.py`（使用带新版 pip 的 Python 环境），已有与线上 API 配套的 wheel 时，可用 `--wheel-dir <发行包目录>` 打包该版本，再构建前端。下载命令使用发布时的 `--platform-url`（本部署默认 `http://10.249.41.63:8765`），与浏览器的本地转发端口无关。将 `web/dist/` 连同生成的 `downloads/` 一起部署。将来在 GitHub 发布时，可将同版本的两个 wheel 和 `skills/gpu-jobs/SKILL.md` 作为 Release 附件上传。成员下载后使用本地文件安装，工作台连接地址仍单独配置。

分别验证服务端与独立客户端包：

```bash
.venv/bin/pytest -q
tools_bin="$HOME/.local/share/gpu-workbench/venv/bin"
"$tools_bin/python" -m unittest discover -s packages/gpu-platform-client/tests
"$tools_bin/python" -m unittest discover -s packages/gpu-platform-mcp/tests
```

## 平台认证与 HTTP API

网页、CLI 和 MCP 都使用个人 token 标识提交者。使用 `Authorization: Bearer <token>` 请求 `POST /api/tasks` 即可提交任务。工作区文件、终端、Jupyter 和日志均按账号或任务归属授权。系统模式下文件编辑器只开放本人在 /d、/e 的工作目录，并限制访问 .ssh。曾安装 0.2 版客户端的成员请从下载页升级 CLI 和 MCP 到 0.3 或更高版本。

| 路径 | 用途 |
|---|---|
| `GET /api/health` | 查询调度器是否就绪，恢复过程中返回 503 |
| `GET /api/me` | 查询当前账号、主目录和首选解释器 |
| `POST /api/tasks`、`GET /api/tasks` | 提交任务、列出自己的任务 |
| `GET /api/tasks/{id}`、`POST /api/tasks/{id}/cancel` | 查询和取消任务 |
| `GET /api/tasks/{id}/logs?offset=0` | 增量读取日志，使用响应中的 next_offset 读取下一段 |
| `WS /api/tasks/{id}/logs` | 首帧发送 token 和可选字节偏移，接收增量日志及完成通知 |
| `GET /api/server-status` | 「服务器状态」查看整机 CPU、内存、全部 GPU 及 /d、/e 磁盘，约每 5 秒刷新 |
| `GET /api/queue`、`GET /api/gpus` | 查看排队位置、设备分配和占用冲突 |
| `GET /api/tasks/{id}/connect` | 获取自己的 Jupyter 会话入口和转发信息 |
| `WS /api/terminal/{id}` | 连接持久终端，首帧发送带 token 的 JSON |
| `GET /api/files?path=...`、`PUT /api/files` | 以用户身份浏览或保存本机文本文件 |

## 架构和恢复

前端在 `web/`，后端位于 `src/gpu_platform/`。API 和调度器同属一个普通用户进程。runner 通过私有 Unix socket 接收指令，用独立的 systemd transient service 运行每个任务。网页关闭、API 重启和 runner 重启均与运行单元生命周期分离。

调度器在内存预留设备，将分配提交到 SQLite，记录发送启动请求的意图，然后调用 runner。单实例锁与数据库唯一约束防止重复分配。GPU 请求严格 FIFO，CPU 终端可独立启动。新提交、任务退出和取消唤醒调度器，周期对账用于发现外部进程和订阅断连。任务记录中的 created、reserved、started、finished 为 UTC 时间，可用于检查等待和交接延迟。

恢复时重建分配并核对运行收据与本机单元。已发送启动请求且退出结果未知的任务记为失败，以免重复执行用户代码。停止任务时由 systemd 清理整个 cgroup，进程清空后才允许再次分卡。新 Jupyter 会话的内核默认持续空闲 30 分钟后自动关闭，检查间隔最多 15 秒，连接着的空闲内核也适用。正在执行单元格的 busy 内核继续运行；单元格返回后启动的后台线程不一定反映在 busy 状态里，长时间无人值守的任务应作为后台训练提交。回收会清空内核变量，已保存的文件可重新打开。管理员可设置顶层配置 `jupyter_idle_seconds`。没有活动内核或终端、且服务本身也持续无活动达到两倍超时后，Jupyter 服务自动退出，systemd 清理所属进程。用户也可以随时停止内核，或在工作台结束整个会话。

Jupyter 与训练可同时访问同一 GPU，包括训练已经获得调度器独占名额时。训练之间仍遵守独占或预算共享策略；未知或外部占用仍阻止新分配。并发 CUDA 程序竞争物理算力与显存，无法保证任意后续显存申请成功。已启动的旧会话按原有分配完成。

## 设备和账号边界

用户级试点以当前 Linux 用户运行，沿用 GPU 7 现有的设备权限。runner 核对所有卡上的计算和图形占用，仅对确认属于平台外、只在纳管单卡运行、且操作系统允许管理的普通用户计算进程发送终止信号。跨卡、受保护或归属不清的进程阻止分配。GPU 低利用率不会触发终止。

修改 `gpu_uuids` 即可登记更多卡，每项任务通过 `gpu_count` 请求同机整组设备。原子多卡分配已由自动化测试覆盖，本次硬件验收使用 GPU 7。

多用户系统模式要求独立的普通 API 服务账号、由 root 管理的只读安装路径和配置、私有 runner socket，以及真实 Linux 用户的设备访问策略。系统模式将工作进程降权到提交者并设置设备 cgroup；用户已有的文件和 GPU 设备权限仍然适用。当前提供的用户级 unit 面向单账号试点。
