export type User = { user: string; home: string; jupyter_idle_seconds?: number; jupyter_python?: string }
export type Kind = 'batch' | 'terminal' | 'jupyter'
export type Task = {
  id: string
  user: string
  kind: Kind
  state: string
  cwd: string
  argv: string[]
  gpu_on_demand?: number
  gpu_count: number
  gpu_memory_mib?: number | null
  created: string | number
  error?: string
  gpus?: string[]
}
export type Gpu = {
  uuid: string
  name?: string
  index?: number
  state?: string
  status?: string
  run_id?: string | null
  run_ids?: string[]
  shared_limit?: number
  shared_free_mib?: number
  shared_headroom_mib?: number
  shared_reserved_mib?: number
  task_id?: string
}
export type Entry = { name: string; path: string; is_dir: boolean }
export type Browse = { entries: Entry[] } | { text: string }
export const stateNames: Record<string, string> = {
  queued: '排队中',
  pending: '排队中',
  reserved: '已预留',
  starting: '启动中',
  running: '运行中',
  stopping: '停止中',
  cancelling: '取消中',
  succeeded: '已完成',
  completed: '已完成',
  finished: '已结束',
  failed: '失败',
  cancelled: '已取消',
  canceled: '已取消',
  lost: '已失联',
  idle: '可分配',
  free: '可分配',
  available: '可分配',
  occupied: '使用中',
  busy: '使用中',
  allocated: '使用中',
  shared: '共享中',
  conflict: '占用冲突',
  blocked: '暂不可用',
  unknown: '待核对',
  external: '外部占用',
  offline: '离线',
}
export const kindNames: Record<Kind, string> = {
  batch: '后台训练',
  terminal: '终端',
  jupyter: 'Jupyter',
}
export const ended = (value: string) =>
  ['succeeded', 'completed', 'finished', 'failed', 'cancelled', 'canceled', 'lost'].includes(value)
export function label(state: string) {
  return stateNames[state] || state || '待核对'
}
export function memoryLabel(mib: number) {
  if (mib < 1024) return mib + ' MiB'
  return (mib / 1024).toLocaleString('zh-CN', { maximumFractionDigits: 3 }) + ' GiB'
}
export function allocationLabel(task: Task) {
  if (task.gpu_on_demand) return 'GPU 按需使用'
  if (task.gpu_count === 0) return 'CPU'
  const count = task.gpu_count + ' 张 GPU'
  if (task.state === 'queued' || task.state === 'pending') return count + ' · 等待自动调度'
  if (!task.gpus?.length) return count + ' · 未分配'
  if (task.gpu_memory_mib) return count + ' · 调度预算 ' + memoryLabel(task.gpu_memory_mib)
  return count + ' · 整卡分配'
}
export function info(err: unknown) {
  return err instanceof Error ? err.message : String(err)
}
export async function api<T>(token: string, path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch('/api' + path, {
    ...options,
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      Authorization: 'Bearer ' + token,
      ...options.headers,
    },
  })
  if (!response.ok) {
    const raw = await response.text()
    let message = response.status + ' ' + response.statusText
    try {
      const reason = JSON.parse(raw).detail
      if (typeof reason === 'string') message = reason
      else if (Array.isArray(reason))
        message = reason.map((e: { msg?: string }) => e.msg || '请求无效').join('；')
    } catch {
      /* Non-JSON errors retain their HTTP status. */
    }
    throw new Error(message)
  }
  return response.status === 204 ? (undefined as T) : response.json()
}
