import { useEffect, useRef } from 'react'
import { basicSetup } from 'codemirror'
import { EditorView } from '@codemirror/view'
import { python } from '@codemirror/lang-python'

export default function Editor({
  initial,
  onChange,
  onSave,
}: {
  initial: string
  onChange: (v: string) => void
  onSave: () => void
}) {
  const host = useRef<HTMLDivElement>(null)
  const callbacks = useRef({ onChange, onSave })
  callbacks.current = { onChange, onSave }
  useEffect(() => {
    if (!host.current) return
    const view = new EditorView({
      parent: host.current,
      doc: initial,
      extensions: [
        basicSetup,
        python(),
        EditorView.contentAttributes.of({ 'aria-label': '文件编辑器' }),
        EditorView.updateListener.of((update) => {
          if (update.docChanged) callbacks.current.onChange(update.state.doc.toString())
        }),
        EditorView.domEventHandlers({
          keydown(event) {
            if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
              event.preventDefault()
              callbacks.current.onSave()
              return true
            }
            return false
          },
        }),
      ],
    })
    return () => view.destroy()
  }, []) // The parent keys editors by the loaded file revision.
  return <div className="editor-host" ref={host} />
}
