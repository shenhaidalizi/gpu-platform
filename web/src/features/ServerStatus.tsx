import { useEffect, useState } from 'react'
import { CircuitBoard, Cpu, HardDrive, RefreshCw, Server } from 'lucide-react'
import { api, info } from '../api/client'
import { DiskOwners, type Ownership } from './DiskOwners'

type Memory = { total: number; used: number; available: number }
type Disk = { mount: string } & ({ error: string } | (Memory & { reserved: number; ownership?: Ownership | null }))
type Device = { index: number; name: string; util: number | null; used_mib: number; total_mib: number; managed: boolean }
type Snapshot = {
  hostname: string; sampled: number; cpu: { percent: number | null; logical: number | null }
  memory: Memory; disks: Disk[]; gpu_sampled: number | null; gpus: Device[]
}

const capacity = (bytes: number) => {
  const tib = bytes >= 1024 ** 4
  return (bytes / 1024 ** (tib ? 4 : 3)).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + (tib ? ' TiB' : ' GiB')
}
const ratio = (used: number, total: number) => total > 0 ? 100 * used / total : null
const percent = (n: number | null) => n === null ? '—' : Number(n.toFixed(1)) + '%'
const clock = (n: number) => new Date(n * 1000).toLocaleTimeString('zh-CN', { hour12: false })

function Ring({ value, label, reserve = 0 }: { value: number | null; label: string; reserve?: number }) {
  const used = Math.max(0, Math.min(100, value ?? 0))
  const end = Math.min(100, used + reserve)
  const background = `conic-gradient(var(--sector) 0 ${used}%, var(--reserve) ${used}% ${end}%, var(--track) ${end}% 100%)`
  return <div className="host-ring" style={{ background }} role="img" aria-label={label + ' ' + percent(value)}>
    <div aria-hidden="true"><strong>{percent(value)}</strong><small>{label}</small></div>
  </div>
}

function Amount({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return <div className="host-amount"><dt>{tone && <i className={'swatch ' + tone} />}{label}</dt><dd>{value}</dd></div>
}

export default function ServerStatus({ token, refresh }: { token: string; refresh: number }) {
  const [data, setData] = useState<Snapshot | null>(null)
  const [error, setError] = useState('')
  const [byOwner, setByOwner] = useState(true)
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function update() {
      try {
        const next = await api<Snapshot>(token, '/server-status', { signal: controller.signal })
        if (!controller.signal.aborted) { setData(next); setError('') }
      } catch (e) {
        if (!controller.signal.aborted) setError(info(e))
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(update, 5000)
      }
    }
    void update()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [token, refresh])

  const staleGpu = data?.gpu_sampled != null && data.sampled - data.gpu_sampled > 15
  return <div className="host-status">
    {error && <div className="notice" role="alert">服务器状态刷新失败 · {error}</div>}
    {!data && !error && <p role="status">正在读取服务器状态…</p>}
    {data && <>
      <div className="host-meta"><strong><Server size={16} />{data.hostname}</strong>
        <span><RefreshCw size={13} /> 每 5 秒刷新 · 最近采样 {clock(data.sampled)}</span>
      </div>
      <div className="host-panels">
        <section className="panel">
          <div className="panel-heading"><h2><Cpu size={18} />CPU 与内存</h2></div>
          <div className="host-row">
            <Ring value={data.cpu.percent} label="利用率" />
            <div><h3>CPU</h3><dl>
              <Amount label="逻辑处理器" value={String(data.cpu.logical ?? '—')} />
              <Amount label="忙碌" value={percent(data.cpu.percent)} tone="used" />
              <Amount label="空闲及 I/O 等待" value={percent(data.cpu.percent === null ? null : 100 - data.cpu.percent)} tone="free" />
            </dl></div>
          </div>
          <div className="host-row host-memory">
            <Ring value={ratio(data.memory.used, data.memory.total)} label="占用率" />
            <div><h3>内存 <small>{capacity(data.memory.total)}</small></h3><dl>
              <Amount label="已用" value={capacity(data.memory.used)} tone="used" />
              <Amount label="可用" value={capacity(data.memory.available)} tone="free" />
            </dl></div>
          </div>
        </section>
        <section className="panel">
          <div className="panel-heading"><h2><HardDrive size={18} />磁盘存储</h2>
            <div className="disk-view" aria-label="磁盘统计方式">
              <button aria-pressed={byOwner} onClick={() => setByOwner(true)}>按用户</button>
              <button aria-pressed={!byOwner} onClick={() => setByOwner(false)}>按空间</button>
            </div>
          </div>
          {data.disks.map(disk => byOwner && !('error' in disk) ?
            <DiskOwners key={disk.mount} mount={disk.mount} ownership={disk.ownership} total={disk.total} /> :
            <div className="host-row host-disk" key={disk.mount}>
            {'error' in disk ? <><Ring value={null} label="占用率" /><div><h3>{disk.mount}</h3><p>{disk.error}</p></div></> : <>
              <Ring value={ratio(disk.used, disk.total)} reserve={ratio(disk.reserved, disk.total) ?? 0} label="占用率" />
              <div><h3>{disk.mount} <small>{capacity(disk.total)}</small></h3><dl>
                <Amount label="已用" value={capacity(disk.used)} tone="used" />
                <Amount label="可写空间" value={capacity(disk.available)} tone="free" />
                <Amount label="系统预留" value={capacity(disk.reserved)} tone="reserve" />
              </dl></div>
            </>}
          </div>)}
        </section>
      </div>
      <section className="panel">
        <div className="panel-heading"><h2><CircuitBoard size={18} />GPU</h2>
          <small>{data.gpu_sampled ? (staleGpu ? '采样已延迟 · ' : '采样于 ') + clock(data.gpu_sampled) : '等待采样'}</small>
        </div>
        {!data.gpus.length && <div className="empty-table">{data.gpu_sampled ? '未观测到 GPU' : '等待 GPU 数据'}</div>}
        <div className="host-gpus">
          {data.gpus.map(gpu => <article className="host-gpu" key={gpu.index}>
            <div className="host-gpu-title"><h3>GPU {gpu.index}</h3>{gpu.managed && <small>平台纳管</small>}</div>
            <p>{gpu.name}</p>
            <Ring value={gpu.util} label="计算利用率" />
            <div className="host-vram"><span>显存</span><b>{capacity(gpu.used_mib * 1024 ** 2)} / {capacity(gpu.total_mib * 1024 ** 2)}</b></div>
            <div className="host-bar" aria-hidden="true"><i style={{ width: (ratio(gpu.used_mib, gpu.total_mib) ?? 0) + '%' }} /></div>
          </article>)}
        </div>
      </section>
    </>}
  </div>
}
