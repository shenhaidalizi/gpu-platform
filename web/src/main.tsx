import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Activity,
  ArrowUpRight,
  ChevronRight,
  CircleAlert,
  Cpu,
  CircuitBoard,
  Clock3,
  FileCode2,
  Download,
  FolderOpen,
  PcCase as GpuIcon,
  Layers3,
  LayoutDashboard,
  ListOrdered,
  LoaderCircle,
  LogOut,
  Menu,
  Plus,
  RefreshCw,
  Server,
  SquareTerminal,
  UserRound,
  X,
} from 'lucide-react'
import {
  api,
  allocationLabel,
  memoryLabel,
  ended,
  info,
  kindNames,
  label,
  type Gpu,
  type Kind,
  type Task,
  type User,
} from './api/client'
import Workspace from './features/Workspace'
import ServerStatus from './features/ServerStatus'
import Submit from './features/Submit'
import TaskDetails from './features/TaskDetails'
import './style.css'

const key = 'gpu-platform.access-token'
function Brand() {
  return (
    <div className="brand">
      <div className="brand-symbol">
        <Layers3 size={25} strokeWidth={2.3} />
      </div>
      <span>算研工作台</span>
    </div>
  )
}
function Login({ signIn }: { signIn: (t: string, user: User) => void }) {
  const [value, setValue] = useState(''),
    [error, setError] = useState(''),
    [loading, setLoading] = useState(false)
  async function connect() {
    setLoading(true)
    setError('')
    try {
      const token = value.trim()
      const user = await api<User>(token, '/me')
      signIn(token, user)
    } catch (e) {
      setError('连接失败 · ' + info(e))
    } finally {
      setLoading(false)
    }
  }
  return (
    <main className="login-page">
      <div className="login-shell">
        <section className="login-identity">
          <Brand />
          <div className="login-title">
            <h1>GPU 算力工作台</h1>
            <p>在服务器上编辑代码、打开终端、提交训练。</p>
          </div>
          <div className="board-art" aria-hidden="true">
            <div className="board-lines"></div>
            <div className="chip">
              <Cpu strokeWidth={1} size={68} />
            </div>
          </div>
        </section>
        <section className="login-form">
          <div>
            <SquareTerminal className="title-icon" size={26} />
            <h2>连接工作台</h2>
            <p>使用分配给你的访问令牌登录。</p>
            <form
              onSubmit={(e) => {
                e.preventDefault()
                void connect()
              }}
            >
              <label>
                访问令牌
                <input
                  autoFocus
                  type="password"
                  autoComplete="current-password"
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  required
                  placeholder="输入访问令牌"
                />
              </label>
              {error && (
                <div className="inline-error" role="alert">
                  {error}
                </div>
              )}
              <button className="primary" disabled={loading}>
                {loading ? '正在连接…' : '进入工作台'}
                <ArrowUpRight size={16} />
              </button>
            </form>
          </div>
        </section>
      </div>
    </main>
  )
}
type Card = Gpu & {
  jupyter_runs?: string[]
  jupyter_used_mib?: number
  external_pids?: number[]
  conflict?: string
  memory_total_mib?: number
  memory_used_mib?: number
  available?: boolean
}
type QueueEntry = { position: number; mine: boolean; id: string | null }
function gpuState(g: Card) {
  if (g.conflict) return 'conflict'
  if (g.external_pids?.length) return 'external'
  if ((g.run_ids?.length ?? 0) > 1) return 'shared'
  if (g.run_ids?.length || g.run_id) return 'allocated'
  return g.state || g.status || (g.available === false ? 'busy' : 'idle')
}
const navs = [
  { id: 'workspace', name: '工作台', icon: LayoutDashboard },
  { id: 'tasks', name: '任务队列', icon: ListOrdered },
  { id: 'gpus', name: 'GPU 资源', icon: CircuitBoard },
  { id: 'server', name: '服务器状态', icon: Activity },
] as const
type Page = (typeof navs)[number]['id']
function timeLabel(value: string | number) {
  const d = new Date(typeof value === 'number' && value < 1e11 ? value * 1000 : value)
  return Number.isNaN(d.valueOf())
    ? '—'
    : d.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
}
function Workbench({ user, token, logout }: { user: User; token: string; logout: () => void }) {
  const [page, setPage] = useState<Page>('workspace'),
    [menu, setMenu] = useState(false),
    [tasks, setTasks] = useState<Task[]>([]),
    [gpus, setGpus] = useState<Card[]>([]),
    [queue, setQueue] = useState<QueueEntry[]>([])
  const [directory, setDirectory] = useState(user.home),
    [error, setError] = useState(''),
    [connectionError, setConnectionError] = useState(''),
    [checked, setChecked] = useState('')
  const [submit, setSubmit] = useState<Kind | 'cpu' | null>(null),
    [focus, setFocus] = useState<string | null>(null),
    [filter, setFilter] = useState('all')
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout>
    async function sync() {
      try {
        const [jobs, cards, positions] = await Promise.all([
          api<Task[]>(token, '/tasks'),
          api<Card[]>(token, '/gpus'),
          api<QueueEntry[]>(token, '/queue'),
        ])
        if (!active) return
        // The API returns submission order, oldest first.
        setTasks(jobs.reverse())
        setGpus(cards)
        setQueue(positions)
        setConnectionError('')
        setChecked(
          new Date().toLocaleTimeString('zh-CN', {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
          }),
        )
      } catch (e) {
        if (active) setConnectionError(info(e))
      }
      if (active) timer = setTimeout(sync, 2000)
    }
    void sync()
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [token, refresh])
  const running = tasks.filter((t) => ['running', 'starting', 'stopping'].includes(t.state)),
    queued = tasks.filter((t) => t.state === 'queued')
  function waitReason(task: Task) {
    if (task.state !== 'queued') return ''
    if (task.gpu_on_demand) return '等待可访问的设备或启动会话'
    const position = queue.find((q) => q.mine && q.id === task.id)?.position
    if (position !== undefined && position > 1) return '前方 ' + (position - 1) + ' 项任务'
    if (task.gpu_count && gpus.length && gpus.every((g) => g.conflict))
      return '等待设备占用冲突解除'
    return task.gpu_count ? '等待调度器分配 ' + task.gpu_count + ' 张 GPU' : '等待开启终端'
  }
  const selected = tasks.find((t) => t.id === focus),
    pageName = navs.find((n) => n.id === page)!.name
  const list =
    filter === 'running'
      ? running
      : filter === 'queued'
        ? queued
        : filter === 'ended'
          ? tasks.filter((t) => ended(t.state))
          : tasks
  async function cancel(t: Task) {
    if (!window.confirm('确认结束此任务？运行中的程序和子进程将停止。')) return
    try {
      await api(token, '/tasks/' + encodeURIComponent(t.id) + '/cancel', { method: 'POST' })
      setRefresh((v) => v + 1)
    } catch (e) {
      setError(info(e))
    }
  }
  function navigate(p: Page) {
    setMenu(false)
    setPage(p)
  }
  return (
    <div className="app-shell">
      {menu && (
        <button className="mobile-backdrop" aria-label="关闭导航" onClick={() => setMenu(false)} />
      )}
      <aside className={'sidebar' + (menu ? ' visible' : '')}>
        <Brand />
        <nav>
          {navs.map((n) => (
            <button
              key={n.id}
              className={page === n.id ? 'current' : ''}
              onClick={() => navigate(n.id)}
            >
              <n.icon size={19} />
              {n.name}
              {n.id === 'tasks' && queued.length > 0 && <b>{queued.length}</b>}
            </button>
          ))}
          <a className="client-downloads" href="/downloads/"><Download size={19} />安装客户端</a>
        </nav>
        <div className="account">
          <span className="avatar">
            <UserRound size={18} />
          </span>
          <div>
            <strong>{user.user}</strong>
            <small>服务器账号</small>
          </div>
          <button
            className="icon-button"
            aria-label="退出登录"
            title="退出登录"
            onClick={() => {
              if (window.confirm('退出工作台？运行中的任务会继续。')) logout()
            }}
          >
            <LogOut size={16} />
          </button>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <button
            className="icon-button menu-btn"
            onClick={() => setMenu(!menu)}
            aria-label="打开导航"
          >
            <Menu size={20} />
          </button>
          <Server size={16} />
          <span>算力平台</span>
          <ChevronRight size={15} />
          <strong>{pageName}</strong>
          <span className={'online ' + (connectionError ? 'bad' : '')}>
            <i />
            {connectionError ? '连接异常' : checked ? '服务已连接' : '连接中'}
          </span>
        </header>
        <div className="content">
          <div className="page-heading">
            <div>
              <h1>{pageName}</h1>
              <p>
                {page === 'workspace'
                  ? '服务器文件、交互环境与后台训练'
                  : page === 'tasks'
                    ? '查看任务状态、运行日志和交互会话'
                    : page === 'gpus' ? '查看已纳管设备与当前分配情况' : '整机负载与存储容量'}
              </p>
            </div>
            {page === 'server' ? <button className="button" onClick={() => setRefresh(v => v + 1)}>
              <RefreshCw size={16} />刷新状态
            </button> : <button className="primary" onClick={() => setSubmit('batch')}>
              <Plus size={17} />
              新建运行
            </button>}
          </div>
          {(error || connectionError) && (
            <div className="notice" role="alert">
              <CircleAlert size={17} />
              <span>{error || connectionError}</span>
              <button
                className="icon-button"
                aria-label="关闭提示"
                onClick={() => {
                  setError('')
                  setRefresh((v) => v + 1)
                }}
              >
                <X size={16} />
              </button>
            </div>
          )}
          <div hidden={page !== 'workspace'}>
            <section className="metrics">
              {[
                {
                  name: '可用整卡',
                  n: gpus.filter((g) => gpuState(g) === 'idle' || gpuState(g) === 'available')
                    .length,
                  of: gpus.length,
                  icon: GpuIcon,
                  page: 'gpus',
                  filter: 'all',
                },
                {
                  name: '运行中',
                  n: running.length,
                  icon: Activity,
                  page: 'tasks',
                  filter: 'running',
                },
                {
                  name: '等待资源',
                  n: queued.length,
                  icon: Clock3,
                  page: 'tasks',
                  filter: 'queued',
                },
              ].map((m) => (
                <button
                  className="metric"
                  key={m.name}
                  onClick={() => {
                    setFilter(m.filter)
                    navigate(m.page as Page)
                  }}
                >
                  <div className="metric-top">
                    <m.icon size={19} />
                    <span>{m.name}</span>
                    <ArrowUpRight className="right" size={14} />
                  </div>
                  <b>
                    {m.n}
                    {m.of !== undefined && <small> / {m.of}</small>}
                  </b>
                </button>
              ))}
            </section>
            <Workspace token={token} home={user.home} onPath={setDirectory} onError={setError} />
            <section className="panel sessions">
              <div className="panel-heading">
                <h2>
                  <SquareTerminal size={19} />
                  交互会话
                </h2>
              </div>
              <div className="session-buttons">
                <button className="button" onClick={() => setSubmit('cpu')}>
                  <SquareTerminal size={16} />
                  普通终端
                </button>
                <button className="button" onClick={() => setSubmit('terminal')}>
                  <Cpu size={16} />
                  GPU 终端
                </button>
                <button className="button" onClick={() => setSubmit('jupyter')}>
                  <FileCode2 size={16} />
                  Jupyter
                </button>
              </div>
            </section>
          </div>
          {page === 'tasks' && (
            <section className="panel">
              <div className="panel-heading">
                <h2>
                  <ListOrdered size={19} />
                  我的任务
                </h2>
                <div className="panel-controls">
                  <select
                    aria-label="筛选任务"
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                  >
                    <option value="all">全部状态</option>
                    <option value="running">运行中</option>
                    <option value="queued">排队中</option>
                    <option value="ended">已结束</option>
                  </select>
                  <button
                    className="icon-button"
                    aria-label="刷新任务"
                    onClick={() => setRefresh((v) => v + 1)}
                  >
                    <RefreshCw size={16} />
                  </button>
                </div>
              </div>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>任务 / 命令</th>
                      <th>状态</th>
                      <th>资源申请</th>
                      <th aria-sort="descending">提交时间</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {list.map((t) => (
                      <tr key={t.id}>
                        <td>
                          <strong>{kindNames[t.kind]}</strong>
                          <code title={t.argv.join(' ')}>{t.argv.join(' ')}</code>
                        </td>
                        <td>
                          <span className={'status ' + t.state}>{label(t.state)}</span>
                          {t.state === 'queued' && (
                            <small className="queue-reason">{waitReason(t)}</small>
                          )}
                        </td>
                        <td>{allocationLabel(t)}</td>
                        <td>{timeLabel(t.created)}</td>
                        <td>
                          <button className="text-button" onClick={() => setFocus(t.id)}>
                            查看
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!list.length && (
                  <div className="empty-table">
                    <ListOrdered size={30} />
                    <strong>暂无符合条件的任务</strong>
                  </div>
                )}
              </div>
            </section>
          )}
          {page === 'gpus' && (
            <section className="panel">
              <div className="panel-heading">
                <h2>
                  <GpuIcon size={19} />
                  本机设备
                </h2>
                <button
                  className="icon-button"
                  aria-label="刷新设备"
                  onClick={() => setRefresh((v) => v + 1)}
                >
                  <RefreshCw size={16} />
                </button>
              </div>
              <div className="gpu-list">
                {gpus.map((g) => (
                  <article className="gpu-card" key={g.uuid}>
                    <div className="gpu-badge">
                      <GpuIcon size={32} />
                    </div>
                    <div>
                      <h3>
                        GPU {g.index ?? '—'} · {g.name || 'GPU'}
                      </h3>
                      <code>{g.uuid}</code>
                      {g.memory_total_mib !== undefined && (
                        <p className="gpu-memory">
                          显存 {((g.memory_used_mib || 0) / 1024).toFixed(1)} /{' '}
                          {(g.memory_total_mib / 1024).toFixed(1)} GiB
                        </p>
                      )}
                      {g.run_ids && g.shared_limit !== undefined && (
                        <p className="gpu-memory">
                          当前任务 {g.run_ids.length} 项 · 共享并发上限 {g.shared_limit} 项
                        </p>
                      )}
                      {!!g.jupyter_runs?.length && (
                        <p className="gpu-memory">
                          Jupyter 会话 {g.jupyter_runs.length} 个 · 当前显存 {memoryLabel(g.jupyter_used_mib ?? 0)}
                        </p>
                      )}
                      {g.shared_reserved_mib !== undefined && (
                        <p className="gpu-memory">
                          调度已预留显存 {memoryLabel(g.shared_reserved_mib)}
                        </p>
                      )}
                      {g.shared_free_mib !== undefined && g.shared_headroom_mib !== undefined && (
                        <p className="gpu-memory">
                          剩余共享显存预算 {memoryLabel(g.shared_free_mib)} · 系统安全余量{' '}
                          {memoryLabel(g.shared_headroom_mib)}
                        </p>
                      )}
                      {g.conflict && <p className="queue-reason">{g.conflict}</p>}
                      {(g.run_ids ?? (g.run_id ? [g.run_id] : [])).map((runId) => (
                        <p key={runId}>
                          当前运行 <code>{runId}</code>
                        </p>
                      ))}
                      {!!g.external_pids?.length && <p>外部进程 {g.external_pids.join(', ')}</p>}
                    </div>
                    <span className={'status ' + gpuState(g)}>{label(gpuState(g))}</span>
                  </article>
                ))}
                {!gpus.length && <div className="empty-table">暂无纳管设备</div>}
              </div>
            </section>
          )}
          {page === 'server' && <ServerStatus token={token} refresh={refresh} />}
          {page !== 'server' && <footer>
            <span>
              <FolderOpen size={13} />
              {directory}
            </span>
            <span>更新于 {checked || '—'}</span>
          </footer>}
        </div>
      </main>
      {submit && (
        <Submit
          initial={submit}
          cwd={directory}
          token={token}
          capacity={gpus.length}
          user={user}
          onClose={() => setSubmit(null)}
          onCreated={(t) => {
            setSubmit(null)
            setTasks((old) => [t, ...old.filter((x) => x.id !== t.id)])
            setFocus(t.id)
            setRefresh((v) => v + 1)
          }}
        />
      )}
      {selected && (
        <TaskDetails
          key={selected.id}
          task={selected}
          reason={waitReason(selected)}
          token={token}
          onClose={() => setFocus(null)}
          onCancel={cancel}
        />
      )}
    </div>
  )
}
function App() {
  const [token, setToken] = useState(() => sessionStorage.getItem(key) || ''),
    [user, setUser] = useState<User | null>(null),
    [checking, setChecking] = useState(!!token)
  useEffect(() => {
    if (!token || user) return
    let active = true
    api<User>(token, '/me')
      .then((u) => {
        if (active) setUser(u)
      })
      .catch(() => {
        if (active) {
          setToken('')
          sessionStorage.removeItem(key)
        }
      })
      .finally(() => {
        if (active) setChecking(false)
      })
    return () => {
      active = false
    }
  }, [token, user])
  if (checking && !user)
    return (
      <main className="loading">
        <LoaderCircle size={22} />
        连接工作台…
      </main>
    )
  return user && token ? (
    <Workbench key={token}
      user={user}
      token={token}
      logout={() => {
        setUser(null)
        setToken('')
        sessionStorage.removeItem(key)
      }}
    />
  ) : (
    <Login
      signIn={(t, u) => {
        sessionStorage.setItem(key, t)
        setUser(u)
        setToken(t)
        setChecking(false)
      }}
    />
  )
}
createRoot(document.getElementById('root')!).render(<App />)
