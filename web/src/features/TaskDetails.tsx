import { lazy, Suspense, useEffect, useState } from 'react'
import { Copy, ExternalLink, SquareTerminal, X } from 'lucide-react'
import { api, info, kindNames, label, ended, allocationLabel, type Task } from '../api/client'
const Terminal = lazy(() => import('./Terminal'))

export default function TaskDetails({
  task,
  reason,
  token,
  onClose,
  onCancel,
}: {
  task: Task
  reason: string
  token: string
  onClose: () => void
  onCancel: (task: Task) => void
}) {
  const [logs, setLogs] = useState(''),
    [error, setError] = useState(''),
    [conn, setConn] = useState<{ url: string; tunnel?: string } | null>(null)
  const terminal = task.kind === 'terminal' && task.state === 'running'
  useEffect(() => {
    let live = true
    let timer: ReturnType<typeof setTimeout>
    let offset = 0
    setLogs('')
    async function tick() {
      let more = false
      try {
        const data = await api<{ text: string; next_offset?: number }>(
          token,
          '/tasks/' + encodeURIComponent(task.id) + '/logs?offset=' + offset,
        )
        if (!live) return
        more = data.next_offset !== undefined && data.next_offset > offset
        if (more) offset = data.next_offset!
        setLogs((previous) =>
          (data.next_offset === undefined ? data.text : previous + data.text).slice(-262144),
        )
        setError('')
      } catch (e) {
        if (live) setError(info(e))
      }
      if (live && (more || !ended(task.state))) timer = setTimeout(tick, more ? 50 : 1500)
    }
    let stream: WebSocket | undefined
    let complete = false
    if (!terminal) {
      const scheme = location.protocol === 'https:' ? 'wss://' : 'ws://'
      stream = new WebSocket(
        scheme + location.host + '/api/tasks/' + encodeURIComponent(task.id) + '/logs',
      )
      stream.onopen = () => stream?.send(JSON.stringify({ token, offset }))
      stream.onmessage = (event) => {
        if (!live) return
        try {
          const item = JSON.parse(event.data) as {
            text: string
            next_offset: number
            done: boolean
          }
          if (
            typeof item.text !== 'string' ||
            !Number.isSafeInteger(item.next_offset) ||
            item.next_offset < offset
          )
            throw new Error()
          offset = item.next_offset
          setLogs((previous) => (previous + item.text).slice(-262144))
          setError('')
          complete = item.done
        } catch {
          stream?.close()
        }
      }
      stream.onclose = () => {
        if (live && !complete) void tick()
      }
    }
    return () => {
      live = false
      clearTimeout(timer)
      stream?.close()
    }
  }, [task.id, task.state, task.kind, terminal, token])
  async function connect() {
    try {
      const link = await api<{ url: string; tunnel?: string }>(
        token,
        '/tasks/' + encodeURIComponent(task.id) + '/connect',
      )
      setConn(link)
    } catch (e) {
      setError(info(e))
    }
  }
  return (
    <div className="overlay">
      <section className="dialog wide" role="dialog" aria-modal="true" aria-label="运行详情">
        <div className="dialog-heading">
          <h2>
            <SquareTerminal size={20} /> {kindNames[task.kind]}
          </h2>
          <button className="icon-button" aria-label="关闭详情" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
        <div className="detail-body">
          <div className="detail-meta">
            <span className={'status ' + task.state}>{label(task.state)}</span>
            <code title={task.id}>{task.id}</code>
          </div>
          {reason && <p className="queue-reason">{reason}</p>}
          <div className="detail-data">
            <span>资源申请</span>
            <code>{allocationLabel(task)}</code>
            <span>工作目录</span>
            <code>{task.cwd}</code>
            <span>启动命令</span>
            <code>{task.argv.join(' ')}</code>
          </div>
          {task.error && <p className="inline-error">{task.error}</p>}
          {terminal ? (
            <Suspense fallback={<p>正在连接终端…</p>}>
              <Terminal id={task.id} token={token} />
            </Suspense>
          ) : (
            <>
              <h3>运行日志</h3>
              <pre className="log-content">{logs || '等待运行输出…'}</pre>
            </>
          )}
          {error && (
            <p className="inline-error" role="alert">
              {error}
            </p>
          )}
          {task.kind === 'jupyter' && task.state === 'running' && (
            <div className="jupyter-connection">
              {conn ? (
                <>
                  {conn.tunnel && (
                    <>
                      <p>远程浏览器请先在本地终端运行 SSH 转发命令。</p>
                      <div className="tunnel">
                        <code>{conn.tunnel}</code>
                        <button
                          className="icon-button"
                          title="复制转发命令"
                          onClick={() =>
                            navigator.clipboard
                              .writeText(conn.tunnel!)
                              .catch(() => setError('请选中命令后复制'))
                          }
                        >
                          <Copy size={15} />
                        </button>
                      </div>
                    </>
                  )}
                  <a className="primary" href={conn.url} target="_blank" rel="noopener noreferrer">
                    打开 Jupyter <ExternalLink size={15} />
                  </a>
                </>
              ) : (
                <button className="primary" onClick={connect}>
                  连接 Jupyter
                </button>
              )}
            </div>
          )}
        </div>
        <div className="dialog-actions">
          {!ended(task.state) && task.state !== 'stopping' && (
            <button className="button danger" onClick={() => onCancel(task)}>
              {task.state === 'queued' ? '取消排队' : '结束运行'}
            </button>
          )}
          <button className="button" onClick={onClose}>
            关闭
          </button>
        </div>
      </section>
    </div>
  )
}
