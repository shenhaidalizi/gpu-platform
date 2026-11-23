import { useEffect, useRef, useState } from 'react'
import { Terminal as Xterm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

export default function Terminal({ id, token }: { id: string; token: string }) {
  const host = useRef<HTMLDivElement>(null)
  const [revision, setRevision] = useState(0)
  const [closed, setClosed] = useState(false)
  useEffect(() => {
    if (!host.current) return
    let active = true
    setClosed(false)
    const term = new Xterm({
      cursorBlink: true,
      fontSize: 14,
      scrollback: 2500,
      fontFamily: '"IBM Plex Mono", Consolas, monospace',
      theme: { background: '#152624', foreground: '#e4ece8', cursor: '#b0db80' },
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(host.current)
    const socket = new WebSocket(
      (location.protocol === 'https:' ? 'wss://' : 'ws://') +
        location.host +
        '/api/terminal/' +
        encodeURIComponent(id),
    )
    socket.binaryType = 'arraybuffer'
    function resize() {
      if (!active || !host.current?.clientWidth) return
      fit.fit()
      if (socket.readyState === WebSocket.OPEN)
        socket.send(JSON.stringify({ resize: [term.cols, term.rows] }))
    }
    socket.onopen = () => {
      socket.send(JSON.stringify({ token }))
      resize()
      term.focus()
    }
    socket.onmessage = (e) => {
      if (active) term.write(typeof e.data === 'string' ? e.data : new Uint8Array(e.data))
    }
    socket.onclose = () => {
      if (active) setClosed(true)
    }
    const input = term.onData((data) => {
      if (socket.readyState === WebSocket.OPEN) socket.send(data)
    })
    const observer = new ResizeObserver(resize)
    observer.observe(host.current)
    resize()
    return () => {
      active = false
      observer.disconnect()
      input.dispose()
      socket.close()
      term.dispose()
    }
  }, [id, token, revision])
  return (
    <div className="terminal-container">
      <div ref={host} className="terminal-screen" />
      {closed && (
        <div className="terminal-reconnect">
          终端连接已断开 <button onClick={() => setRevision(v => v + 1)}>重新连接</button>
        </div>
      )}
    </div>
  )
}
