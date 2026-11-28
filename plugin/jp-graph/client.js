/**
 * Client half of @local/jp-graph（日語學習 · 知識圖譜）。
 *
 * 一個左側欄入口 + 一塊主面板：
 *   - 左：分類面向（功能／語域／難度／類型），由本地資料庫的 tags 統計而來
 *   - 中：關係圖（語法＝方框、單詞＝圓點、例句＝虛線框），選一個節點就把它
 *         真實存在的關聯（例句、同功能語法、句中單詞、近義詞…）拉進來
 *   - 右：選中節點的完整資料與關聯清單
 *
 * 資料全部來自 Host 半邊的 /jp-graph/api，不從 CDN 取任何東西。
 */
window.__ModuleLoader__.load({
  id: '@local/jp-graph',
  factory(require) {
    const React = require('react')
    const h = React.createElement
    const { useState, useEffect, useRef, useMemo, useCallback } = React

    const API = '/jp-graph/api'
    // 送進快取鍵：改了引擎輸出而 Host 還沒重啟時，換這個值就能繞過舊快取。
    const BUILD = '3'

    const EN = {
      'nav.label': 'Japanese study',
      'panel.title': 'Japanese study',
      'panel.subtitle': 'Knowledge map of the local grammar, word and example databases.',
      'search.placeholder': 'Search a word, grammar point or sentence…',
      'search.empty': 'Nothing matched. Try Japanese, simplified Chinese or English.',
      'search.hint': 'Enter a word (学校), a grammar point (てしまう) or a Chinese gloss (睡觉).',
      'rail.title': 'Classification',
      'rail.hint': 'Facets counted from the local tags: function, register, level, type.',
      'rail.filter': 'Show in graph',
      'rail.collapse': 'Hide',
      'rail.open': 'Classification',
      'kind.grammar': 'Grammar',
      'kind.word': 'Word',
      'kind.sent': 'Example',
      'kind.gex': 'Grammar example',
      'action.home': 'Home',
      'action.clear': 'Clear',
      'action.reset': 'Fit',
      'action.zoomIn': 'Zoom in',
      'action.zoomOut': 'Zoom out',
      'action.expand': 'Expand neighbours',
      'action.addAll': 'Add all',
      'action.add': 'Add to graph',
      'action.more': 'more',
      'action.retry': 'Retry',
      'side.empty': 'Pick a node in the graph, or a search result, to read its data and connections.',
      'side.relations': 'Connections',
      'side.noRelations': 'No recorded connection for this node.',
      'side.meta': 'Facts',
      'legend.title': 'Legend',
      'legend.edges': 'Edges: same key / same function / shared word / example',
      'state.loading': 'Reading the local databases…',
      'state.error': 'Could not reach the Japanese data engine.',
      'state.facet': 'Showing the “{tag}” group.',
      'stats.grammar': 'grammar',
      'stats.words': 'words',
      'stats.sentences': 'sentences',
      'stats.examples': 'examples',
      'empty.graph': 'The graph is empty. Search something or open a facet on the left.',
    }

    const ZH = {
      'nav.label': '日語學習',
      'panel.title': '日語學習',
      'panel.subtitle': '本地語法、單詞、例句資料庫的關係圖。',
      'search.placeholder': '搜尋單詞、語法或例句…',
      'search.empty': '沒有結果：可以試日文、簡體中文或英文釋義。',
      'search.hint': '輸入單詞（学校）、語法（てしまう）或中文釋義（睡觉）。',
      'rail.title': '分類',
      'rail.hint': '依本地 tags 統計：功能、語域、難度、類型。',
      'rail.filter': '圖上顯示',
      'rail.collapse': '收起',
      'rail.open': '分類',
      'kind.grammar': '語法',
      'kind.word': '單詞',
      'kind.sent': '例句',
      'kind.gex': '語法例句',
      'action.home': '首頁',
      'action.clear': '清空',
      'action.reset': '重置視圖',
      'action.zoomIn': '放大',
      'action.zoomOut': '縮小',
      'action.expand': '展開鄰居',
      'action.addAll': '全部加入',
      'action.add': '加入圖中',
      'action.more': '更多',
      'action.retry': '重試',
      'side.empty': '在圖上或搜尋結果裡選一個節點，這裡會列出它的資料與關聯。',
      'side.relations': '關聯',
      'side.noRelations': '這個節點目前沒有記錄到關聯。',
      'side.meta': '資料',
      'legend.title': '圖例',
      'legend.edges': '連線：同檢索鍵／同功能／共用詞／例句',
      'state.loading': '正在讀取本地資料庫…',
      'state.error': '連不上日語資料引擎。',
      'state.facet': '正在看「{tag}」這一組。',
      'stats.grammar': '條語法',
      'stats.words': '個單詞',
      'stats.sentences': '句例句',
      'stats.examples': '條例句',
      'empty.graph': '圖是空的：搜尋一個詞，或點左邊的分類。',
    }

    const KIND_COLOR = {
      grammar: '#7c9cff',
      word: '#5ec9a5',
      sent: '#e0a54a',
      gex: '#a98be0',
    }

    const REL_COLOR = {
      similar: '#b07cff',
      tag: '#e0a54a',
      word: '#4a9ce0',
      example: '#5bb98c',
      sentence: '#5bb98c',
      gex: '#5bb98c',
      grammar: '#4ac9c0',
    }

    function callApi(payload) {
      return fetch(API, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(Object.assign({ v: BUILD }, payload)),
      }).then((response) => response.json()).then((data) => {
        if (!data || data.ok !== true) throw new Error((data && data.error) || 'request failed')
        return data
      })
    }

    function clipText(text, max) {
      const value = String(text || '')
      return value.length > max ? value.slice(0, max - 1) + '…' : value
    }

    function nodeKey(kind, id) {
      return kind + ':' + id
    }

    function nodeWidth(node) {
      const len = String(node.title || '').length
      if (node.kind === 'grammar') return Math.min(210, Math.max(52, len * 10.4 + 18))
      if (node.kind === 'word') return 44
      return Math.min(200, Math.max(46, len * 8.4 + 16))
    }

    function nodeRadius(node) {
      if (node.kind === 'word') return 34
      return Math.min(105, nodeWidth(node) / 2 + 12)
    }

    function GraphIcon(props) {
      const size = props && typeof props.size === 'number' ? props.size : 18
      const color = props && props.active ? 'var(--dsw-alias-brand-primary)' : 'currentColor'
      return h('svg', { viewBox: '0 0 24 24', width: size, height: size, 'aria-hidden': true, style: { color, display: 'block' } },
        h('path', { d: 'M7 8.4 12 5l5 3.4M7 8.4v7.2L12 19l5-3.4V8.4M12 5v14', stroke: color, strokeWidth: 1.4, fill: 'none', strokeLinejoin: 'round' }),
        h('circle', { cx: 12, cy: 5, r: 1.9, fill: color }),
        h('circle', { cx: 7, cy: 8.4, r: 1.7, fill: color }),
        h('circle', { cx: 17, cy: 8.4, r: 1.7, fill: color }),
        h('circle', { cx: 7, cy: 15.6, r: 1.7, fill: color }),
        h('circle', { cx: 17, cy: 15.6, r: 1.7, fill: color }),
        h('circle', { cx: 12, cy: 19, r: 1.9, fill: color }),
      )
    }

    // ------------------------------------------------------------------
    // 關係圖
    // ------------------------------------------------------------------

    function GraphCanvas(props) {
      const { nodesRef, edgesRef, frame, view, setView, selectedKey, filters, onSelect, onExpand, onKick, t } = props
      const hostRef = useRef(null)
      const svgRef = useRef(null)
      const [size, setSize] = useState({ w: 640, h: 420 })
      const dragRef = useRef(null)
      const userMovedRef = useRef(false)

      const stageSize = useCallback(() => {
        const svg = svgRef.current
        if (!svg) return { w: size.w, h: size.h }
        const rect = svg.getBoundingClientRect()
        return { w: rect.width || size.w, h: rect.height || size.h }
      }, [size])

      useEffect(() => {
        const host = hostRef.current
        if (!host) return undefined
        const measure = () => {
          const rect = host.getBoundingClientRect()
          setSize({ w: Math.max(200, Math.round(rect.width)), h: Math.max(160, Math.round(rect.height)) })
        }
        measure()
        let observer
        if (typeof ResizeObserver === 'function') {
          observer = new ResizeObserver(measure)
          observer.observe(host)
        }
        window.addEventListener('resize', measure)
        return () => {
          if (observer) observer.disconnect()
          window.removeEventListener('resize', measure)
        }
      }, [])

      // 使用者還沒自己平移／縮放之前，畫面尺寸變了就重新置中
      useEffect(() => {
        if (userMovedRef.current || size.w < 40) return
        setView((current) => {
          const box = stageSize()
          return { k: current.k, x: box.w / 2, y: box.h / 2 }
        })
      }, [size, setView, stageSize])

      // 換了內容（首頁／分類組）就重新置中
      useEffect(() => {
        if (!props.resetToken || size.w < 40) return
        userMovedRef.current = false
        const box = stageSize()
        setView({ k: 1, x: box.w / 2, y: box.h / 2 })
      }, [props.resetToken, setView, stageSize])

      const fitToContent = useCallback(() => {
        const items = nodesRef.current
        if (!items.length) return
        const box = stageSize()
        let minX = Infinity
        let minY = Infinity
        let maxX = -Infinity
        let maxY = -Infinity
        for (const item of items) {
          const r = nodeRadius(item)
          minX = Math.min(minX, item.x - r)
          maxX = Math.max(maxX, item.x + r)
          minY = Math.min(minY, item.y - r)
          maxY = Math.max(maxY, item.y + r)
        }
        const pad = 50
        const spanX = Math.max(140, maxX - minX)
        const spanY = Math.max(140, maxY - minY)
        const k = Math.min(1.6, Math.max(0.32, Math.min((box.w - pad * 2) / spanX, (box.h - pad * 2) / spanY)))
        userMovedRef.current = true
        setView({ k, x: box.w / 2 - ((minX + maxX) / 2) * k, y: box.h / 2 - ((minY + maxY) / 2) * k })
      }, [stageSize, setView])

      // 版面穩定之後自動貼合一次（使用者自己動過就不再自動）
      useEffect(() => {
        if (!props.resetToken) return undefined
        const timer = setTimeout(() => {
          userMovedRef.current = false
          fitToContent()
        }, 750)
        return () => clearTimeout(timer)
      }, [props.resetToken, fitToContent])

      useEffect(() => {
        const svg = svgRef.current
        if (!svg) return undefined
        const onWheel = (event) => {
          event.preventDefault()
          userMovedRef.current = true
          const rect = svg.getBoundingClientRect()
          const px = event.clientX - rect.left
          const py = event.clientY - rect.top
          setView((current) => {
            const k = Math.min(2.6, Math.max(0.3, current.k * Math.exp(-event.deltaY * 0.0013)))
            const wx = (px - current.x) / current.k
            const wy = (py - current.y) / current.k
            return { k, x: px - wx * k, y: py - wy * k }
          })
        }
        svg.addEventListener('wheel', onWheel, { passive: false })
        return () => svg.removeEventListener('wheel', onWheel)
      }, [setView])

      const toWorld = useCallback((clientX, clientY) => {
        const svg = svgRef.current
        if (!svg) return { x: 0, y: 0 }
        const rect = svg.getBoundingClientRect()
        const px = clientX - rect.left
        const py = clientY - rect.top
        return { x: (px - view.x) / view.k, y: (py - view.y) / view.k }
      }, [view])

      const beginDrag = (node, event) => {
        event.stopPropagation()
        const point = toWorld(event.clientX, event.clientY)
        dragRef.current = { node, dx: node.x - point.x, dy: node.y - point.y }
        node.fixed = true
        if (event.currentTarget.setPointerCapture) {
          try { event.currentTarget.setPointerCapture(event.pointerId) } catch (error) { /* ignore */ }
        }
      }

      const onPointerMove = (event) => {
        const drag = dragRef.current
        if (!drag) return
        if (drag.pan) {
          userMovedRef.current = true
          const origin = drag.origin
          setView({ x: origin.x + (event.clientX - drag.x), y: origin.y + (event.clientY - drag.y), k: origin.k })
          return
        }
        const point = toWorld(event.clientX, event.clientY)
        drag.node.x = point.x + drag.dx
        drag.node.y = point.y + drag.dy
        onKick(0.3)
      }

      const endDrag = () => {
        const drag = dragRef.current
        if (!drag) return
        if (drag.node) drag.node.fixed = false
        dragRef.current = null
        if (drag.node) onKick(0.45)
      }

      const viewport = { x: view.x, y: view.y, k: view.k }
      const nodes = nodesRef.current.filter((node) => filters[node.kind] !== false)
      const visible = new Set(nodes.map((node) => node.key))
      const edges = Array.from(edgesRef.current.values()).filter((edge) => visible.has(edge.source) && visible.has(edge.target))
      const adjacent = new Set()
      if (selectedKey) {
        for (const edge of edges) {
          if (edge.source === selectedKey) adjacent.add(edge.target)
          if (edge.target === selectedKey) adjacent.add(edge.source)
        }
      }
      const byKey = new Map()
      for (const node of nodes) byKey.set(node.key, node)

      return h('div', { className: 'jg-stage', ref: hostRef },
        h('svg', {
          className: 'jg-svg',
          ref: svgRef,
          width: size.w,
          height: size.h,
          onPointerMove,
          onPointerUp: endDrag,
          onPointerLeave: endDrag,
          onPointerDown: (event) => {
            if (event.target !== svgRef.current) return
            dragRef.current = { pan: true, x: event.clientX, y: event.clientY, origin: view }
            if (event.currentTarget.setPointerCapture) {
              try { event.currentTarget.setPointerCapture(event.pointerId) } catch (error) { /* ignore */ }
            }
          },
        },
          h('g', { transform: `translate(${viewport.x},${viewport.y}) scale(${viewport.k})` },
            edges.map((edge) => {
              const a = byKey.get(edge.source)
              const b = byKey.get(edge.target)
              if (!a || !b) return null
              const active = edge.source === selectedKey || edge.target === selectedKey
              return h('line', {
                key: 'e:' + edge.source + '>' + edge.target,
                x1: a.x, y1: a.y, x2: b.x, y2: b.y,
                stroke: REL_COLOR[edge.rel] || 'var(--dsw-alias-border-l2)',
                strokeWidth: active ? 2 : 1.1,
                strokeOpacity: active ? 0.9 : selectedKey ? 0.12 : 0.42,
              })
            }),
            nodes.map((node) => renderNode(node, {
              selected: node.key === selectedKey,
              dim: Boolean(selectedKey) && node.key !== selectedKey && !adjacent.has(node.key),
              onSelect,
              onExpand,
              beginDrag,
            })),
          ),
        ),
        h('div', { className: 'jg-canvas-tools' },
          h('button', {
            className: 'jg-tool',
            title: t('action.zoomIn'),
            onClick: () => {
              userMovedRef.current = true
              setView((current) => ({ k: Math.min(2.6, current.k * 1.25), x: current.x, y: current.y }))
            },
          }, '＋'),
          h('button', {
            className: 'jg-tool',
            title: t('action.zoomOut'),
            onClick: () => {
              userMovedRef.current = true
              setView((current) => ({ k: Math.max(0.3, current.k / 1.25), x: current.x, y: current.y }))
            },
          }, '－'),
          h('button', {
            className: 'jg-tool',
            title: t('action.reset'),
            onClick: fitToContent,
          }, '⤢'),
        ),
      )
    }

    function renderNode(node, options) {
      const { selected, dim, onSelect, onExpand, beginDrag } = options
      const color = KIND_COLOR[node.kind] || '#888'
      const opacity = dim ? 0.32 : 1
      const width = nodeWidth(node)
      const common = {
        'data-key': node.key,
        transform: `translate(${node.x.toFixed(1)},${node.y.toFixed(1)})`,
        style: { cursor: 'pointer', opacity },
        onPointerDown: (event) => beginDrag(node, event),
        onClick: (event) => { event.stopPropagation(); onSelect(node.key) },
        onDoubleClick: (event) => { event.stopPropagation(); onExpand(node.key) },
      }
      const label = clipText(node.title, node.kind === 'grammar' ? 20 : 16)
      let shape
      let text
      if (node.kind === 'word') {
        shape = h('circle', {
          r: 9,
          fill: selected ? color : 'var(--dsw-alias-bg-base)',
          stroke: color,
          strokeWidth: selected ? 2.4 : 1.6,
        })
        text = h('text', { y: 24, textAnchor: 'middle', fontSize: 10.5, fill: 'var(--dsw-alias-label-primary)' }, label)
      } else if (node.kind === 'grammar') {
        shape = h('rect', {
          x: -width / 2, y: -11, width, height: 22, rx: 6,
          fill: selected ? 'color-mix(in srgb, ' + color + ' 22%, var(--dsw-alias-bg-layer-1))' : 'var(--dsw-alias-bg-layer-1)',
          stroke: color,
          strokeWidth: selected ? 2.2 : 1.2,
        })
        text = h('text', { textAnchor: 'middle', dominantBaseline: 'middle', fontSize: 10.5, fill: 'var(--dsw-alias-label-primary)' }, label)
      } else {
        shape = h('rect', {
          x: -width / 2, y: -9, width, height: 18, rx: 4,
          fill: 'var(--dsw-alias-bg-base)',
          stroke: color,
          strokeWidth: selected ? 2 : 1,
          strokeDasharray: '3 2',
        })
        text = h('text', { textAnchor: 'middle', dominantBaseline: 'middle', fontSize: 9.5, fill: 'var(--dsw-alias-label-secondary)' }, label)
      }
      return h('g', Object.assign({ key: node.key }, common),
        h('title', null, node.title + (node.sub ? '\n' + node.sub : '')),
        shape,
        text,
      )
    }

    // ------------------------------------------------------------------
    // 主面板
    // ------------------------------------------------------------------

    function GraphPanel(props) {
      const t = props.t || ((key) => key)
      const [boot, setBoot] = useState(null)
      const [facets, setFacets] = useState([])
      const [error, setError] = useState(null)
      const [busy, setBusy] = useState(true)
      const [selectedKey, setSelectedKey] = useState(null)
      const [detail, setDetail] = useState(null)
      const [query, setQuery] = useState('')
      const [results, setResults] = useState([])
      const [searchState, setSearchState] = useState('idle')
      const [filters, setFilters] = useState({ grammar: true, word: true, sent: true, gex: true })
      const [railOpen, setRailOpen] = useState(true)
      const [note, setNote] = useState('')
      const [frame, setFrame] = useState(0)
      const [resetToken, setResetToken] = useState(0)
      const [openFacets, setOpenFacets] = useState({})

      const nodesRef = useRef([])
      const edgesRef = useRef(new Map())
      const viewRef = useRef({ x: 360, y: 260, k: 1 })
      const [view, setViewState] = useState(viewRef.current)
      const setView = useCallback((next) => {
        viewRef.current = typeof next === 'function' ? next(viewRef.current) : next
        setViewState(viewRef.current)
      }, [])
      const selectedRef = useRef(null)
      const simRef = useRef({ alpha: 0, raf: 0, running: false })

      const bump = useCallback(() => setFrame((value) => value + 1), [])

      const ensureNode = useCallback((compact, anchorKey) => {
        const key = compact.key || nodeKey(compact.kind, compact.id)
        const existing = nodesRef.current.find((node) => node.key === key)
        if (existing) {
          if (compact.sub && !existing.sub) existing.sub = compact.sub
          return existing
        }
        const anchor = anchorKey ? nodesRef.current.find((node) => node.key === anchorKey) : null
        const angle = Math.random() * Math.PI * 2
        const distance = 70 + Math.random() * 60
        const node = {
          key,
          kind: compact.kind,
          id: compact.id,
          title: compact.title || key,
          sub: compact.sub || '',
          x: (anchor ? anchor.x : 0) + Math.cos(angle) * distance,
          y: (anchor ? anchor.y : 0) + Math.sin(angle) * distance,
          vx: 0,
          vy: 0,
          fixed: false,
        }
        nodesRef.current.push(node)
        return node
      }, [])

      const addEdge = useCallback((source, target, rel, label) => {
        if (!source || !target || source === target) return
        const key = source < target ? source + '|' + target : target + '|' + source
        if (edgesRef.current.has(key)) return
        edgesRef.current.set(key, {
          source, target, rel: rel || 'word',
          label: label || rel,
        })
      }, [])

      const kick = useCallback((alpha) => {
        const sim = simRef.current
        sim.alpha = Math.max(sim.alpha, alpha ?? 1)
        if (sim.running) return
        sim.running = true
        let last = performance.now()
        const step = (now) => {
          const dt = Math.min(48, now - last) / 16.6
          last = now
          simulate(nodesRef.current, edgesRef.current, sim.alpha, Math.min(2, dt))
          sim.alpha *= 0.982
          bump()
          if (sim.alpha > 0.02) {
            sim.raf = requestAnimationFrame(step)
          } else {
            sim.running = false
            sim.raf = 0
          }
        }
        sim.raf = requestAnimationFrame(step)
      }, [bump])

      useEffect(() => () => {
        if (simRef.current.raf) cancelAnimationFrame(simRef.current.raf)
      }, [])

      const resetView = useCallback(() => {
        setView({ x: 360, y: 260, k: 1 })
      }, [setView])

      const mergeNeighbours = useCallback((detailData, anchorKey) => {
        if (!detailData || !detailData.node) return 0
        const anchor = ensureNode(detailData.node, anchorKey)
        const before = nodesRef.current.length
        for (const group of detailData.groups || []) {
          for (const item of group.items || []) {
            const node = ensureNode(item, anchor.key)
            addEdge(anchor.key, node.key, group.rel, group.label)
          }
        }
        kick(0.9)
        return nodesRef.current.length - before
      }, [ensureNode, addEdge, kick])

      const expand = useCallback((key) => {
        if (!key) return
        setBusy(true)
        callApi({ op: 'node', key }).then((data) => {
          mergeNeighbours(data, key)
          setSelectedKey(key)
        }).catch((cause) => setError(String(cause.message || cause))).finally(() => setBusy(false))
      }, [mergeNeighbours])

      const select = useCallback((key) => {
        setSelectedKey(key)
        selectedRef.current = key
        let compact = null
        if (key) {
          const existing = nodesRef.current.find((node) => node.key === key)
          if (!existing) {
            const parts = key.split(':')
            const kind = parts[0]
            const id = key.slice(kind.length + 1)
            compact = ensureNode({ key, kind, id, title: id, sub: '' }, null)
            kick(0.6)
          } else {
            compact = existing
          }
        }
        if (!key) {
          setDetail(null)
          return
        }
        setBusy(true)
        callApi({ op: 'node', key }).then((data) => {
          setDetail(data)
        }).catch((cause) => setError(String(cause.message || cause))).finally(() => setBusy(false))
        if (compact) bump()
      }, [ensureNode, kick, bump])

      const arrangeCircle = useCallback((radius) => {
        const nodes = nodesRef.current
        if (!nodes.length) return
        nodes[0].x = 0
        nodes[0].y = 0
        nodes[0].vx = 0
        nodes[0].vy = 0
        nodes[0].fixed = false
        const rest = nodes.slice(1)
        const count = Math.max(1, rest.length)
        rest.forEach((node, index) => {
          const angle = (Math.PI * 2 * index) / count - Math.PI / 2
          node.x = Math.cos(angle) * radius
          node.y = Math.sin(angle) * radius
          node.vx = 0
          node.vy = 0
          node.fixed = false
        })
      }, [])

      const arrangeGrid = useCallback(() => {
        const nodes = nodesRef.current
        const count = nodes.length
        if (!count) return
        const columns = Math.max(1, Math.min(4, Math.ceil(Math.sqrt(count * 1.35))))
        const rows = Math.ceil(count / columns)
        const gapX = 218
        const gapY = 64
        nodes.forEach((node, index) => {
          const column = index % columns
          const row = Math.floor(index / columns)
          node.x = (column - (columns - 1) / 2) * gapX
          node.y = (row - (rows - 1) / 2) * gapY
          node.vx = 0
          node.vy = 0
          node.fixed = false
        })
      }, [])

      const loadHome = useCallback(() => {
        setBusy(true)
        setError(null)
        setNote('')
        callApi({ op: 'home' }).then((data) => {
          nodesRef.current = []
          edgesRef.current = new Map()
          for (const node of data.nodes || []) ensureNode(node, null)
          for (const edge of data.edges || []) addEdge(edge.source, edge.target, edge.rel, edge.label)
          arrangeCircle(Math.max(130, nodesRef.current.length * 16))
          kick(1)
          setResetToken((value) => value + 1)
          setNote(data.hint || '')
          setSelectedKey(null)
          setDetail(null)
        }).catch((cause) => setError(String(cause.message || cause))).finally(() => setBusy(false))
      }, [ensureNode, addEdge, kick, arrangeCircle])

      useEffect(() => {
        let cancelled = false
        Promise.all([
          callApi({ op: 'hello' }),
          callApi({ op: 'facets' }),
        ]).then((data) => {
          if (cancelled) return
          setBoot(data[0].counts || null)
          setFacets(data[1].groups || [])
          loadHome()
        }).catch((cause) => {
          if (!cancelled) setError(String(cause.message || cause))
        }).finally(() => {
          if (!cancelled) setBusy(false)
        })
        return () => { cancelled = true }
      }, [loadHome])

      useEffect(() => {
        if (!query.trim()) {
          setResults([])
          setSearchState('idle')
          return undefined
        }
        setSearchState('loading')
        const timer = setTimeout(() => {
          callApi({ op: 'search', q: query.trim() }).then((data) => {
            setResults(data.results || [])
            setSearchState('done')
          }).catch(() => setSearchState('done'))
        }, 280)
        return () => clearTimeout(timer)
      }, [query])

      const openFacet = useCallback((group, tag, label) => {
        setBusy(true)
        setNote('')
        callApi({ op: 'facet', group, tag, limit: 26 }).then((data) => {
          nodesRef.current = []
          edgesRef.current = new Map()
          for (const node of data.nodes || []) ensureNode(node, null)
          arrangeGrid()
          setResetToken((value) => value + 1)
          setNote(t('state.facet').replace('{tag}', label) + ' · ' + (data.total || 0))
          setSelectedKey(null)
          setDetail(null)
        }).catch((cause) => setError(String(cause.message || cause))).finally(() => setBusy(false))
      }, [ensureNode, kick, t, arrangeGrid])

      const addFromSearch = useCallback((item) => {
        const node = ensureNode(item, selectedRef.current)
        if (selectedRef.current && selectedRef.current !== item.key) {
          addEdge(selectedRef.current, node.key, 'word', '')
        }
        kick(0.8)
        setQuery('')
        select(node.key)
      }, [ensureNode, addEdge, kick, select])

      const addAllFromDetail = useCallback(() => {
        if (!detail) return
        mergeNeighbours(detail, detail.node.key)
      }, [detail, mergeNeighbours])

      const selected = detail && detail.node ? detail.node : null
      const tagLabel = useMemo(() => {
        const map = new Map()
        for (const group of facets) {
          for (const item of group.tags || []) map.set(item.tag, item.label)
        }
        return map
      }, [facets])

      return h('div', { className: 'jg-page' },
        h('header', { className: 'jg-top' },
          h('div', { className: 'jg-brand' },
            h(GraphIcon, { size: 20, active: true }),
            h('div', { className: 'jg-brand-text' },
              h('div', { className: 'jg-title' }, t('panel.title')),
              h('div', { className: 'jg-subtitle' }, t('panel.subtitle')),
            ),
          ),
          h('div', { className: 'jg-search' },
            h('input', {
              className: 'jg-input',
              value: query,
              placeholder: t('search.placeholder'),
              onChange: (event) => setQuery(event.target.value),
              onKeyDown: (event) => {
                if (event.key === 'Enter' && results.length) addFromSearch(results[0])
                if (event.key === 'Escape') setQuery('')
              },
            }),
            query.trim()
              ? h('div', { className: 'jg-results' },
                  searchState === 'loading'
                    ? h('div', { className: 'jg-results-empty' }, t('state.loading'))
                    : results.length === 0
                      ? h('div', { className: 'jg-results-empty' }, t('search.empty'))
                      : results.slice(0, 12).map((item) => h('button', {
                          key: item.key,
                          className: 'jg-result',
                          onClick: () => addFromSearch(item),
                        },
                          h('span', { className: 'jg-dot', style: { background: KIND_COLOR[item.kind] } }),
                          h('span', { className: 'jg-result-text' },
                            h('span', { className: 'jg-result-title' }, item.title),
                            h('span', { className: 'jg-result-sub' }, item.sub),
                          ),
                          h('span', { className: 'jg-result-kind' }, t('kind.' + item.kind)),
                        )),
                )
              : null,
          ),
          h('div', { className: 'jg-top-right' },
            boot ? h('span', { className: 'jg-stat' }, boot.grammar.toLocaleString() + ' ' + t('stats.grammar')) : null,
            boot ? h('span', { className: 'jg-stat' }, boot.words.toLocaleString() + ' ' + t('stats.words')) : null,
            boot ? h('span', { className: 'jg-stat' }, boot.sentences.toLocaleString() + ' ' + t('stats.sentences')) : null,
            h('button', { className: 'jg-btn', onClick: () => setRailOpen((value) => !value) },
              railOpen ? t('rail.collapse') : t('rail.open')),
          ),
        ),
        h('div', { className: 'jg-body' },
          railOpen ? h('aside', { className: 'jg-rail' },
            h('div', { className: 'jg-rail-head' },
              h('span', { className: 'jg-rail-title' }, t('rail.title')),
            ),
            h('p', { className: 'jg-hint' }, t('rail.hint')),
            h('div', { className: 'jg-filter' },
              h('div', { className: 'jg-filter-label' }, t('rail.filter')),
              h('div', { className: 'jg-chips' },
                ['grammar', 'word', 'sent', 'gex'].map((kind) => h('button', {
                  key: kind,
                  className: 'jg-chip' + (filters[kind] ? ' on' : ''),
                  onClick: () => setFilters((current) => Object.assign({}, current, { [kind]: !current[kind] })),
                }, h('span', { className: 'jg-dot', style: { background: KIND_COLOR[kind] } }), t('kind.' + kind))),
              ),
            ),
            facets.map((group) => {
              const chips = group.tags || []
              const expanded = openFacets[group.id] === true
              const visible = expanded ? chips : chips.slice(0, 9)
              return h('section', { key: group.id, className: 'jg-facet-group' },
                h('div', { className: 'jg-facet-head' }, group.label),
                h('div', { className: 'jg-chips' },
                  visible.map((item) => h('button', {
                    key: item.tag,
                    className: 'jg-chip tag',
                    title: item.tag,
                    onClick: () => openFacet(group.id, item.tag, item.label),
                  }, item.label, h('span', { className: 'jg-count' }, item.count)),
                  ),
                  chips.length > 9
                    ? h('button', {
                        className: 'jg-chip more',
                        onClick: () => setOpenFacets((current) => Object.assign({}, current, { [group.id]: !expanded })),
                      }, expanded ? '−' : t('action.more') + ' ' + (chips.length - 9))
                    : null,
                ),
              )
            }),
          ) : null,
          h(GraphCanvas, {
            nodesRef,
            edgesRef,
            frame,
            view,
            setView,
            selectedKey,
            filters,
            resetToken,
            onSelect: select,
            onExpand: expand,
            onKick: kick,
            t,
          }),
          h('aside', { className: 'jg-side' },
            error
              ? h('div', { className: 'jg-error' },
                  h('div', null, t('state.error')),
                  h('div', { className: 'jg-error-text' }, error),
                  h('button', { className: 'jg-btn', onClick: () => { setError(null); loadHome() } }, t('action.retry')),
                )
              : null,
            !selected
              ? h('div', { className: 'jg-side-empty' },
                  h('p', null, t('side.empty')),
                  h('div', { className: 'jg-legend' },
                    h('div', { className: 'jg-legend-title' }, t('legend.title')),
                    ['grammar', 'word', 'sent'].map((kind) => h('div', { key: kind, className: 'jg-legend-row' },
                      h('span', { className: 'jg-dot', style: { background: KIND_COLOR[kind] } }),
                      t('kind.' + kind))),
                    h('div', { className: 'jg-legend-note' }, t('legend.edges')),
                  ),
                  note ? h('p', { className: 'jg-note' }, note) : null,
                  nodesRef.current.length === 0 ? h('p', { className: 'jg-note' }, t('empty.graph')) : null,
                )
              : h('div', { className: 'jg-detail' },
                  h('div', { className: 'jg-detail-head' },
                    h('div', { className: 'jg-detail-badge', style: { color: KIND_COLOR[selected.kind], borderColor: KIND_COLOR[selected.kind] } }, t('kind.' + selected.kind)),
                    h('h2', { className: 'jg-detail-title' }, selected.title),
                  ),
                  h('div', { className: 'jg-detail-sub' }, selected.sub || ''),
                  h('div', { className: 'jg-detail-actions' },
                    h('button', { className: 'jg-btn primary', onClick: () => expand(selected.key) }, t('action.expand')),
                    h('button', { className: 'jg-btn', onClick: () => { setSelectedKey(null); setDetail(null) } }, t('action.clear')),
                  ),
                  (selected.badges || []).length
                    ? h('div', { className: 'jg-chips' },
                        selected.badges.map((badge) => h('span', { key: badge, className: 'jg-badge' }, tagLabel.get(badge) || badge)),
                      )
                    : null,
                  (selected.meta || []).length
                    ? h('div', { className: 'jg-meta' },
                        selected.meta.map((row) => h('div', { key: row.label, className: 'jg-meta-row' },
                          h('span', { className: 'jg-meta-label' }, row.label),
                          h('span', { className: 'jg-meta-value' }, row.value),
                        )),
                      )
                    : null,
                  (selected.sections || []).map((section) => h('div', { key: section.label, className: 'jg-section' },
                    h('div', { className: 'jg-section-label' }, section.label),
                    h('div', { className: 'jg-prose' }, section.text),
                  )),
                  h('div', { className: 'jg-relations' },
                    h('div', { className: 'jg-relations-title' }, t('side.relations')),
                    (detail.groups || []).length === 0
                      ? h('div', { className: 'jg-note' }, t('side.noRelations'))
                      : (detail.groups || []).map((group) => h('div', { key: group.label + group.rel, className: 'jg-group' },
                          h('div', { className: 'jg-group-head' },
                            h('span', { className: 'jg-group-label' }, group.label),
                            h('button', {
                              className: 'jg-link-btn',
                              onClick: addAllFromDetail,
                            }, t('action.addAll')),
                          ),
                          h('div', { className: 'jg-rel-list' },
                            group.items.map((item) => h('button', {
                              key: item.key,
                              className: 'jg-rel',
                              onClick: () => {
                                const node = ensureNode(item, selected.key)
                                addEdge(selected.key, node.key, group.rel, group.label)
                                kick(0.7)
                                select(item.key)
                              },
                            },
                              h('span', { className: 'jg-dot', style: { background: KIND_COLOR[item.kind] } }),
                              h('span', { className: 'jg-rel-title' }, clipText(item.title, 30)),
                              h('span', { className: 'jg-rel-sub' }, clipText(item.sub, 26)),
                            )),
                          ),
                        )),
                  ),
                ),
            busy ? h('div', { className: 'jg-busy' }, t('state.loading')) : null,
          ),
        ),
      )
    }

    // ------------------------------------------------------------------
    // 力導向布局
    // ------------------------------------------------------------------

    function simulate(nodes, edgeMap, alpha, dt) {
      const count = nodes.length
      if (count === 0) return
      const k = 0.9 * dt
      for (let i = 0; i < count; i += 1) {
        const a = nodes[i]
        for (let j = i + 1; j < count; j += 1) {
          const b = nodes[j]
          let dx = b.x - a.x
          let dy = b.y - a.y
          let dist2 = dx * dx + dy * dy
          if (dist2 < 1) {
            dx = Math.random() - 0.5
            dy = Math.random() - 0.5
            dist2 = 1
          }
          const minDist = nodeRadius(a) + nodeRadius(b)
          const dist = Math.sqrt(dist2)
          if (dist < minDist) {
            const push = (minDist - dist) * 0.12
            dx = dx / dist
            dy = dy / dist
            a.vx -= dx * push
            a.vy -= dy * push
            b.vx += dx * push
            b.vy += dy * push
          }
          const force = Math.min(26, 2600 / dist2) * alpha
          const fx = (dx / dist) * force
          const fy = (dy / dist) * force
          a.vx -= fx * k
          a.vy -= fy * k
          b.vx += fx * k
          b.vy += fy * k
        }
        a.vx -= a.x * 0.004 * alpha * dt
        a.vy -= a.y * 0.004 * alpha * dt
      }
      for (const edge of edgeMap.values()) {
        const a = nodes.find((node) => node.key === edge.source)
        const b = nodes.find((node) => node.key === edge.target)
        if (!a || !b) continue
        const dx = b.x - a.x
        const dy = b.y - a.y
        const dist = Math.max(1, Math.sqrt(dx * dx + dy * dy))
        const rest = 120
        const force = (dist - rest) * 0.016 * alpha * dt
        const fx = (dx / dist) * force
        const fy = (dy / dist) * force
        a.vx += fx
        a.vy += fy
        b.vx -= fx
        b.vy -= fy
      }
      for (const node of nodes) {
        if (node.fixed) {
          node.vx = 0
          node.vy = 0
          continue
        }
        node.vx *= 0.84
        node.vy *= 0.84
        node.x += node.vx * dt
        node.y += node.vy * dt
      }
    }

    // ------------------------------------------------------------------
    // 樣式
    // ------------------------------------------------------------------

    const STYLE = `
.jg-page{position:relative;display:flex;flex-direction:column;height:100%;min-height:0;background:var(--dsw-alias-bg-base);color:var(--dsw-alias-label-primary);font-family:inherit;font-size:13px;line-height:1.5}
.jg-top{display:flex;align-items:center;gap:14px;padding:10px 18px;border-bottom:1px solid var(--dsw-alias-border-l1);flex:none;min-height:56px}
.jg-brand{display:flex;align-items:center;gap:9px;min-width:0;flex:none}
.jg-brand-text{min-width:0}
.jg-title{font-size:14.5px;font-weight:650;line-height:1.25;white-space:nowrap}
.jg-subtitle{font-size:11px;color:var(--dsw-alias-label-secondary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:360px}
.jg-search{position:relative;flex:1;min-width:160px;max-width:460px}
.jg-input{width:100%;box-sizing:border-box;border:1px solid var(--dsw-alias-border-l2);border-radius:8px;background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-primary);font:inherit;font-size:12.5px;padding:6px 10px}
.jg-input:focus{outline:none;border-color:var(--dsw-alias-brand-primary)}
.jg-results{position:absolute;z-index:20;top:calc(100% + 6px);left:0;right:0;max-height:360px;overflow:auto;border:1px solid var(--dsw-alias-border-l2);border-radius:10px;background:var(--dsw-alias-bg-layer-1);box-shadow:0 10px 28px rgba(0,0,0,.18);padding:4px}
.jg-results-empty{padding:10px 12px;font-size:11.5px;color:var(--dsw-alias-label-secondary)}
.jg-result{display:flex;align-items:center;gap:8px;width:100%;border:none;background:none;color:inherit;font:inherit;text-align:left;padding:6px 8px;border-radius:7px;cursor:pointer}
.jg-result:hover{background:var(--dsw-alias-bg-layer-2)}
.jg-result-text{display:flex;flex-direction:column;min-width:0;flex:1}
.jg-result-title{font-size:12.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.jg-result-sub{font-size:10.5px;color:var(--dsw-alias-label-secondary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.jg-result-kind{font-size:10px;color:var(--dsw-alias-label-secondary);flex:none}
.jg-dot{width:7px;height:7px;border-radius:50%;display:inline-block;flex:none}
.jg-top-right{display:flex;align-items:center;gap:10px;margin-left:auto;flex:none}
.jg-stat{font-size:10.5px;color:var(--dsw-alias-label-secondary);font-variant-numeric:tabular-nums;white-space:nowrap}
.jg-body{flex:1;min-height:0;display:flex}
.jg-rail{width:238px;flex:none;border-right:1px solid var(--dsw-alias-border-l1);overflow:auto;padding:12px 12px 24px}
.jg-rail-title{font-size:11px;font-weight:650;letter-spacing:.08em;text-transform:uppercase;color:var(--dsw-alias-label-secondary)}
.jg-hint{margin:6px 0 12px;font-size:10.5px;line-height:1.55;color:var(--dsw-alias-label-secondary)}
.jg-filter{margin-bottom:6px}
.jg-filter-label{font-size:10.5px;color:var(--dsw-alias-label-secondary);margin-bottom:5px}
.jg-chips{display:flex;flex-wrap:wrap;gap:5px}
.jg-chip{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--dsw-alias-border-l2);border-radius:999px;background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-primary);font:inherit;font-size:11px;padding:2px 9px;cursor:pointer;white-space:nowrap}
.jg-chip:hover{border-color:var(--dsw-alias-brand-primary);color:var(--dsw-alias-brand-primary)}
.jg-chip.on{border-color:var(--dsw-alias-brand-primary);color:var(--dsw-alias-brand-primary);background:color-mix(in srgb, var(--dsw-alias-brand-primary) 12%, transparent)}
.jg-chip.tag{font-size:10.5px;padding:1px 8px}
.jg-chip.more{font-size:10px;color:var(--dsw-alias-label-secondary);border-style:dashed}
.jg-count{font-size:9.5px;color:var(--dsw-alias-label-secondary);font-variant-numeric:tabular-nums}
.jg-facet-group{margin-top:14px}
.jg-facet-head{font-size:10.5px;font-weight:600;color:var(--dsw-alias-label-secondary);margin-bottom:5px}
.jg-stage{flex:1;min-width:0;position:relative;overflow:hidden;background:radial-gradient(circle at 50% 45%, var(--dsw-alias-bg-layer-1) 0%, var(--dsw-alias-bg-base) 70%)}
.jg-svg{display:block;width:100%;height:100%;touch-action:none;user-select:none}
.jg-canvas-tools{position:absolute;right:12px;bottom:12px;display:flex;flex-direction:column;gap:4px}
.jg-tool{width:26px;height:26px;display:flex;align-items:center;justify-content:center;border:1px solid var(--dsw-alias-border-l2);border-radius:7px;background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-secondary);font:inherit;font-size:13px;line-height:1;cursor:pointer}
.jg-tool:hover{color:var(--dsw-alias-brand-primary);border-color:var(--dsw-alias-brand-primary)}
.jg-side{width:300px;flex:none;border-left:1px solid var(--dsw-alias-border-l1);overflow:auto;padding:14px 14px 28px;display:flex;flex-direction:column;gap:10px}
.jg-side-empty{display:flex;flex-direction:column;gap:10px;font-size:12px;color:var(--dsw-alias-label-secondary)}
.jg-legend{border:1px solid var(--dsw-alias-border-l1);border-radius:10px;background:var(--dsw-alias-bg-layer-1);padding:10px 12px}
.jg-legend-title{font-size:10.5px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;color:var(--dsw-alias-label-secondary);margin-bottom:6px}
.jg-legend-row{display:flex;align-items:center;gap:7px;font-size:11.5px;margin-top:3px}
.jg-legend-note{margin-top:8px;font-size:10.5px;line-height:1.5;color:var(--dsw-alias-label-secondary)}
.jg-note{margin:4px 0 0;font-size:11px;line-height:1.55;color:var(--dsw-alias-label-secondary)}
.jg-error{border:1px solid var(--dsw-alias-state-error-primary);border-radius:10px;padding:10px 12px;font-size:12px;color:var(--dsw-alias-state-error-primary)}
.jg-error-text{margin:6px 0 8px;font-size:10.5px;opacity:.85;word-break:break-word}
.jg-detail{display:flex;flex-direction:column;gap:10px}
.jg-detail-head{display:flex;align-items:flex-start;gap:8px}
.jg-detail-badge{flex:none;margin-top:2px;border:1px solid currentColor;border-radius:999px;font-size:9.5px;font-weight:650;padding:0 7px;line-height:1.6}
.jg-detail-title{margin:0;font-size:15.5px;font-weight:650;line-height:1.35;overflow-wrap:anywhere}
.jg-detail-sub{font-size:11.5px;color:var(--dsw-alias-label-secondary);overflow-wrap:anywhere}
.jg-detail-actions{display:flex;gap:7px;flex-wrap:wrap}
.jg-badge{border:1px solid var(--dsw-alias-border-l2);border-radius:999px;padding:0 7px;font-size:10px;line-height:1.7;color:var(--dsw-alias-label-secondary)}
.jg-meta{border:1px solid var(--dsw-alias-border-l1);border-radius:10px;overflow:hidden;background:var(--dsw-alias-bg-layer-1)}
.jg-meta-row{display:flex;gap:10px;padding:5px 10px;font-size:11.5px;border-top:1px solid var(--dsw-alias-border-l1)}
.jg-meta-row:first-child{border-top:none}
.jg-meta-label{flex:none;width:64px;color:var(--dsw-alias-label-secondary)}
.jg-meta-value{min-width:0;overflow-wrap:anywhere}
.jg-section{display:flex;flex-direction:column;gap:4px}
.jg-section-label{font-size:10.5px;font-weight:650;letter-spacing:.05em;text-transform:uppercase;color:var(--dsw-alias-label-secondary)}
.jg-prose{font-size:12px;line-height:1.65;white-space:pre-wrap;overflow-wrap:anywhere;max-height:260px;overflow:auto}
.jg-relations{display:flex;flex-direction:column;gap:9px;margin-top:2px}
.jg-relations-title{font-size:10.5px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;color:var(--dsw-alias-label-secondary)}
.jg-group{border:1px solid var(--dsw-alias-border-l1);border-radius:10px;background:var(--dsw-alias-bg-layer-1);padding:8px 10px}
.jg-group-head{display:flex;align-items:center;gap:8px;margin-bottom:5px}
.jg-group-label{font-size:11.5px;font-weight:600}
.jg-link-btn{margin-left:auto;border:none;background:none;color:var(--dsw-alias-brand-primary);font:inherit;font-size:10.5px;cursor:pointer;padding:0}
.jg-link-btn:hover{text-decoration:underline}
.jg-rel-list{display:flex;flex-direction:column;gap:2px}
.jg-rel{display:flex;align-items:center;gap:7px;width:100%;border:none;background:none;color:inherit;font:inherit;text-align:left;padding:3px 4px;border-radius:6px;cursor:pointer;min-width:0}
.jg-rel:hover{background:var(--dsw-alias-bg-layer-2)}
.jg-rel-title{font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:none;max-width:52%}
.jg-rel-sub{font-size:10px;color:var(--dsw-alias-label-secondary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.jg-busy{font-size:10.5px;color:var(--dsw-alias-label-secondary);padding:2px 0}
.jg-btn{display:inline-flex;align-items:center;justify-content:center;border:1px solid var(--dsw-alias-border-l2);border-radius:7px;background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-primary);font:inherit;font-size:11.5px;font-weight:550;padding:3px 10px;cursor:pointer;white-space:nowrap}
.jg-btn:hover:not(:disabled){border-color:var(--dsw-alias-brand-primary);color:var(--dsw-alias-brand-primary)}
.jg-btn.primary{border-color:var(--dsw-alias-brand-primary);background:var(--dsw-alias-brand-primary);color:var(--dsw-alias-bg-base)}
.jg-btn.primary:hover:not(:disabled){color:var(--dsw-alias-bg-base);opacity:.9}
@media (max-width: 1000px){.jg-side{width:250px}.jg-rail{width:200px}.jg-stat{display:none}}
@media (max-width: 820px){.jg-side{display:none}}
`

    function apply(ctx) {
      let t = (key) => key
      const locale = ctx.get('locale')
      if (locale) {
        ctx.effect(() => locale.register('jp-graph', 'en', EN))
        ctx.effect(() => locale.register('jp-graph', 'zh', ZH))
        t = locale.bind('jp-graph')
      }
      const slots = ctx.get('slots')
      if (slots === undefined) return

      ctx.effect(() => {
        const style = document.createElement('style')
        style.setAttribute('data-jp-graph', '')
        style.textContent = STYLE
        document.head.appendChild(style)
        return () => style.remove()
      })

      ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({
        name: 'sidebar.panellist',
        id: 'jp-graph',
        order: 60,
        label: () => t('nav.label'),
      }, GraphIcon))

      ctx.slots.inject('main', () => ctx.slots.register({
        name: 'main',
        key: 'jp-graph',
      }, (slotProps) => h(GraphPanel, Object.assign({}, slotProps, { t }))))
    }

    return {
      inject: ['slots', 'locale'],
      apply,
      __components: { GraphPanel, GraphIcon, simulate },
    }
  },
})
