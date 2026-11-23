import { useState } from 'react'
import { X, TerminalSquare, NotebookPen, SquarePlay } from 'lucide-react'
import { api, info, kindNames, type Kind, type Task, type User } from '../api/client'

export default function Submit({
  initial,
  user,
  cwd,
  token,
  capacity,
  onClose,
  onCreated,
}: {
  user: User
  initial: Kind | 'cpu'
  cwd: string
  token: string
  capacity: number
  onClose: () => void
  onCreated: (t: Task) => void
}) {
  const [kind, setKind] = useState<Kind>(initial === 'cpu' ? 'terminal' : initial)
  const [path, setPath] = useState(cwd),
    [command, setCommand] = useState(''),
    [interpreter, setInterpreter] = useState(
      () => sessionStorage.getItem('gpu-platform.python.' + user.user) || user.jupyter_python || '',
    )
  const [count, setCount] = useState(initial === 'cpu' ? 0 : 1),
    [error, setError] = useState(''),
    [sending, setSending] = useState(false)
  function choose(k: Kind) {
    setKind(k)
    if (k !== 'terminal' && count < 1) setCount(1)
  }
  async function submit() {
    if (sending) return
    setError('')
    setSending(true)
    try {
      const argv =
        kind === 'batch'
          ? ['/bin/bash', '-c', command]
          : kind === 'terminal'
            ? ['/bin/bash', '--noprofile', '--norc', '-i']
            : [interpreter, '-m', 'jupyterlab']
      const spec = { cwd: path, argv, kind, gpu_count: kind === 'jupyter' ? 1 : count }
      const task = await api<Task>(token, '/tasks', {
        method: 'POST', body: JSON.stringify(spec),
      })
      if (kind === 'jupyter')
        sessionStorage.setItem('gpu-platform.python.' + user.user, interpreter)
      onCreated(task)
    } catch (e) {
      setError(info(e))
    } finally {
      setSending(false)
    }
  }
  return (
    <div
      className="overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !sending) onClose()
      }}
    >
      <section className="dialog" role="dialog" aria-modal="true" aria-label="新建运行">
        <div className="dialog-heading">
          <h2>新建运行</h2>
          <button className="icon-button" aria-label="关闭" onClick={onClose}>
            <X size={19} />
          </button>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            void submit()
          }}
        >
          <div className="kind-switch">
            {(['batch', 'terminal', 'jupyter'] as Kind[]).map((k) => (
              <button
                type="button"
                className={k === kind ? 'active' : ''}
                key={k}
                onClick={() => choose(k)}
              >
                {k === 'batch' ? (
                  <SquarePlay size={18} />
                ) : k === 'terminal' ? (
                  <TerminalSquare size={18} />
                ) : (
                  <NotebookPen size={18} />
                )}{' '}
                {kindNames[k]}
              </button>
            ))}
          </div>
          <label>
            服务器工作目录
            <input
              required
              value={path}
              onChange={(e) => setPath(e.target.value)}
              spellCheck={false}
            />
          </label>
          {kind === 'batch' && (
            <label>
              启动命令
              <textarea
                autoFocus
                required
                placeholder="python train.py --epochs 20"
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                spellCheck={false}
              />
            </label>
          )}
          {kind === 'jupyter' && (
            <label>
              已安装 JupyterLab 的 Python 解释器
              <input
                required
                pattern="/.*"
                title="请输入 Python 解释器的绝对路径"
                value={interpreter}
                onChange={(e) => setInterpreter(e.target.value)}
                placeholder="/path/to/env/bin/python"
                spellCheck={false}
              />
            </label>
          )}
          {kind !== 'jupyter' && (
            <label>
              GPU 数量
              <select value={count} onChange={(e) => setCount(Number(e.target.value))}>
                {kind === 'terminal' && <option value={0}>0 · CPU 终端</option>}
                {Array.from({ length: Math.max(capacity, 1) }, (_, i) => (
                  <option key={i + 1} value={i + 1}>
                    {i + 1} 张 GPU
                  </option>
                ))}
              </select>
            </label>
          )}
          <p className="hint">
            {kind === 'batch'
              ? '任务启动时读取目录里的文件，关闭网页后训练继续。'
              : kind === 'jupyter'
                ? `内核可直接使用 GPU。内核持续空闲 ${Math.ceil((user.jupyter_idle_seconds ?? 1800) / 60)} 分钟后会自动关闭，内存变量将清空；请保存需要的结果。`
                : '关闭网页可以重连。结束会话后将释放资源。' }
          </p>
          {error && (
            <p role="alert" className="inline-error">
              {error}
            </p>
          )}
          <div className="dialog-actions">
            <button type="button" className="button" onClick={onClose}>
              取消
            </button>
            <button className="primary" disabled={sending}>
              {sending ? '提交中…' : kind === 'batch' ? '提交训练' : '开启会话'}
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}
