import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import {
  ArrowUp,
  ChevronRight,
  FileCode2,
  FilePlus2,
  FileText,
  Folder,
  FolderOpen,
  FolderSearch,
  RefreshCw,
  Save,
} from 'lucide-react'
import { api, info, type Browse, type Entry } from '../api/client'
const Editor = lazy(() => import('./Editor'))

const base = (s: string) => s.split('/').filter(Boolean).pop() || '/'
const parent = (s: string) => s.replace(/\/+$/, '').split('/').slice(0, -1).join('/') || '/'
type FileDoc = { path: string; initial: string; text: string; revision: number }
export default function Workspace({
  token,
  home,
  onPath,
  onError,
}: {
  token: string
  home: string
  onPath: (p: string) => void
  onError: (s: string) => void
}) {
  const [folder, setFolder] = useState(home),
    [dirInput, setDirInput] = useState(home)
  const [entries, setEntries] = useState<Entry[]>([]),
    [file, setFile] = useState<FileDoc | null>(null),
    [loading, setLoading] = useState(false)
  const [newFile, setNewFile] = useState(false),
    [name, setName] = useState(''),
    [saving, setSaving] = useState(false)
  const folderSeq = useRef(0),
    fileSeq = useRef(0)
  const dirty = !!file && file.text !== file.initial
  async function loadFolder(path: string) {
    const seq = ++folderSeq.current
    setDirInput(path)
    setLoading(true)
    try {
      const data = await api<Browse>(token, '/files?path=' + encodeURIComponent(path))
      if (seq !== folderSeq.current) return
      if (!('entries' in data)) throw new Error('请输入目录路径')
      setFolder(path)
      setEntries(
        [...data.entries].sort(
          (a, b) => Number(b.is_dir) - Number(a.is_dir) || a.name.localeCompare(b.name),
        ),
      )
      onPath(path)
    } catch (e) {
      if (seq === folderSeq.current) onError(info(e))
    } finally {
      if (seq === folderSeq.current) setLoading(false)
    }
  }
  useEffect(() => {
    void loadFolder(home)
    return () => {
      folderSeq.current++
      fileSeq.current++
    }
  }, [home, token])
  useEffect(() => {
    function guard(e: BeforeUnloadEvent) {
      if (dirty) {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [dirty])
  function allowSwitch() {
    return !dirty || window.confirm('当前文件尚未保存，放弃这些修改？')
  }
  async function openFile(path: string) {
    if (file?.path === path || !allowSwitch()) return
    const seq = ++fileSeq.current
    try {
      const data = await api<Browse>(token, '/files?path=' + encodeURIComponent(path))
      if (seq !== fileSeq.current) return
      if (!('text' in data)) throw new Error('请选择文件')
      setFile({ path, text: data.text, initial: data.text, revision: seq })
    } catch (e) {
      if (seq === fileSeq.current) onError(info(e))
    }
  }
  async function save() {
    if (!file || saving) return
    const { path, text, revision } = file
    setSaving(true)
    try {
      await api(token, '/files', { method: 'PUT', body: JSON.stringify({ path, text }) })
      setFile((f) => (f?.revision === revision ? { ...f, initial: text } : f))
      await loadFolder(folder)
    } catch (e) {
      onError(info(e))
    } finally {
      setSaving(false)
    }
  }
  function createFile() {
    const value = name.trim()
    if (!value || value.includes('/') || value === '.' || value === '..') {
      onError('请输入不含斜杠的文件名')
      return
    }
    if (entries.some((e) => e.name === value)) {
      onError('目录中已存在同名文件')
      return
    }
    if (!allowSwitch()) return
    const path = folder.replace(/\/$/, '') + '/' + value
    setFile({ path, text: '', initial: '', revision: ++fileSeq.current })
    setNewFile(false)
    setName('')
  }
  return (
    <section className="editor-pane panel">
      <div className="panel-heading">
        <h2>
          <FolderOpen size={19} />
          服务器文件
        </h2>
        <button
          className="icon-button"
          title="刷新目录"
          aria-label="刷新目录"
          onClick={() => loadFolder(folder)}
        >
          <RefreshCw size={16} />
        </button>
      </div>
      <form
        className="path-bar"
        onSubmit={(e) => {
          e.preventDefault()
          void loadFolder(dirInput.trim())
        }}
      >
        <FolderSearch size={16} />
        <input
          value={dirInput}
          spellCheck={false}
          aria-label="服务器目录"
          onChange={(e) => setDirInput(e.target.value)}
        />
        <button type="submit">打开目录</button>
      </form>
      <div className="editor-layout">
        <aside className="tree">
          <div className="tree-heading">
            <span>文件</span>
            <div>
              <button
                className="icon-button"
                title="上一级目录"
                aria-label="上一级目录"
                onClick={() => loadFolder(parent(folder))}
              >
                <ArrowUp size={15} />
              </button>
              <button
                className="icon-button"
                title="新建文件"
                aria-label="新建文件"
                onClick={() => setNewFile((v) => !v)}
              >
                <FilePlus2 size={15} />
              </button>
            </div>
          </div>
          {newFile && (
            <form
              className="new-file"
              onSubmit={(e) => {
                e.preventDefault()
                createFile()
              }}
            >
              <input
                autoFocus
                placeholder="文件名"
                aria-label="新文件名"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
              <button title="创建文件">
                <ChevronRight size={15} />
              </button>
            </form>
          )}
          <div className="file-list">
            {loading && <p className="tree-note">读取中…</p>}
            {!loading && !entries.length && <p className="tree-note">目录为空</p>}
            {entries.map((entry) => (
              <button
                key={entry.path}
                title={entry.name}
                className={file?.path === entry.path ? 'selected' : ''}
                onClick={() => (entry.is_dir ? loadFolder(entry.path) : openFile(entry.path))}
              >
                {entry.is_dir ? <Folder size={15} /> : <FileText size={15} />}
                <span>{entry.name}</span>
              </button>
            ))}
          </div>
        </aside>
        <div className="file-editor">
          {file ? (
            <>
              <div className="file-tab">
                <span title={file.path}>
                  <FileCode2 size={16} />
                  {base(file.path)}
                  {dirty ? ' •' : ''}
                </span>
                <button className="button small" disabled={saving} onClick={save}>
                  <Save size={14} />
                  {saving ? '保存中' : '保存'}
                </button>
              </div>
              <Suspense fallback={<p>正在加载编辑器…</p>}>
                <Editor
                  key={file.revision}
                  initial={file.text}
                  onChange={(text) => setFile((f) => (f ? { ...f, text } : f))}
                  onSave={save}
                />
              </Suspense>
            </>
          ) : (
            <div className="empty-editor">
              <FileCode2 size={38} strokeWidth={1.3} />
              <strong>选择文件开始编辑</strong>
              <span>从左侧目录打开服务器上的文本文件</span>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
