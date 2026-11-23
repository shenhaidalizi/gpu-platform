import { useState } from 'react'

type Owner = { uid: number; name: string; bytes: number }
export type Ownership = {
  sampled: number; total: number; used: number; available: number; reserved: number
  users: Owner[]; partial: boolean
}
type Slice = { label: string; bytes: number; color: string }

function size(bytes: number) {
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let unit = 0
  while (bytes >= 1024 && unit < units.length - 1) { bytes /= 1024; unit++ }
  return bytes.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + ' ' + units[unit]
}

const percentage = (bytes: number, total: number) =>
  bytes > 0 && bytes / total < 0.001 ? '<0.1%' : Number((100 * bytes / total).toFixed(1)) + '%'

function Row({ slice, total }: { slice: Slice; total: number }) {
  return <li><span><i style={{ background: slice.color }} />{slice.label}</span>
    <strong>{size(slice.bytes)}</strong><small>{percentage(slice.bytes, total)}</small></li>
}

export function DiskOwners({ mount, ownership, total }: {
  mount: string; ownership?: Ownership | null; total: number
}) {
  const [expanded, setExpanded] = useState(false)
  if (!ownership) return <article className="disk-owners"><h3>{mount} <small>{size(total)}</small></h3>
    <p>等待读取用户用量统计</p></article>
  const rows: Slice[] = ownership.users.map(u => ({
    label: u.name, bytes: u.bytes, color: `hsl(${Math.round((u.uid * 137.508 + 158) % 360)} 42% 43%)`,
  }))
  const sum = rows.reduce((value, row) => value + row.bytes, 0)
  const remainder = Math.max(0, ownership.used - sum)
  const space: Slice[] = [
    { label: '未归属', bytes: remainder, color: '#829095' },
    { label: '系统预留', bytes: ownership.reserved, color: 'var(--reserve)' },
    { label: '可写空间', bytes: ownership.available, color: 'var(--track)' },
  ]
  const chartTotal = sum + space.reduce((n, s) => n + s.bytes, 0)
  if (chartTotal <= 0) return <article className="disk-owners"><h3>{mount}</h3><p>暂无容量数据</p></article>
  let offset = 0
  const stops = [...rows, ...space].filter(s => s.bytes > 0).map(s => {
    const start = offset
    offset += 100 * s.bytes / chartTotal
    return `${s.color} ${start}% ${offset}%`
  })
  return <article className="disk-owners">
    <h3>{mount} <small>{size(ownership.total)}</small></h3>
    <div className="disk-users-body">
      <div className="host-ring owner-ring" role="img" aria-label={mount + ' 各用户磁盘用量占比'} style={{
        background: 'conic-gradient(' + stops.join(', ') + ')',
      }}><div><strong>{rows.length}</strong><small>个账号</small></div></div>
      <div>
        <ul className="disk-legend">{(expanded ? rows : rows.slice(0, 5)).map(row => <Row key={row.label} slice={row} total={chartTotal} />)}</ul>
        {rows.length > 5 && <button className="text-button owner-more" aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}>{expanded ? '收起用户列表' : '查看全部 ' + rows.length + ' 个账号'}</button>}
        <ul className="disk-legend disk-space">{space.map(row => <Row key={row.label} slice={row} total={chartTotal} />)}</ul>
      </div>
    </div>
    <p>按文件所有者统计 · {new Date(ownership.sampled * 1000).toLocaleString('zh-CN', {hour12:false})}</p>
    {ownership.partial && <p>部分文件未能读取，其空间计入未归属。</p>}
    {sum > ownership.used && <p>扫描期间占用发生变化，占比按采集到的容量合计计算。</p>}
  </article>
}
