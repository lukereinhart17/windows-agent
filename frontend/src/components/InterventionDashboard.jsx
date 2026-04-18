import { useEffect, useRef, useState, useCallback } from 'react'
import { Paper, Text, Stack, Notification, SegmentedControl, Select, Grid, Divider } from '@mantine/core'
import ChatPanel from './ChatPanel'
import RecordTaskPanel from './RecordTaskPanel'

const getWsUrl = (apiBase) => {
  const apiUrl = apiBase
    ? new URL(apiBase, window.location.origin)
    : new URL(window.location.origin)
  const wsProtocol = apiUrl.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${wsProtocol}//${apiUrl.host}/ws/screen`
}

export default function InterventionDashboard({ apiBase, isBackendReachable }) {
  const imgRef = useRef(null)
  const [connected, setConnected] = useState(false)
  const [monitors, setMonitors] = useState([])
  const [selectedMonitor, setSelectedMonitor] = useState('1')
  const [actionMode, setActionMode] = useState('click')
  const [lastClick, setLastClick] = useState(null)
  const [feedback, setFeedback] = useState(null)

  useEffect(() => {
    if (!isBackendReachable) {
      setMonitors([])
      setSelectedMonitor('1')
      return
    }

    const fetchMonitors = async () => {
      try {
        const res = await fetch(`${apiBase}/api/monitors`)
        if (!res.ok) {
          throw new Error('Monitor fetch failed')
        }
        const data = await res.json()

        const options = (data.monitors || []).map((monitor) => ({
          value: String(monitor.mss_index),
          label: monitor.label,
        }))

        setMonitors(options)
        setSelectedMonitor(String(data.selected_monitor_index || 1))
      } catch {
        setFeedback({ type: 'error', message: 'Failed to load screen list' })
      }
    }

    fetchMonitors()
  }, [apiBase, isBackendReachable])

  const handleMonitorChange = useCallback(
    async (value) => {
      if (!value) return

      setSelectedMonitor(value)

      try {
        const res = await fetch(`${apiBase}/api/monitor`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ monitor_index: Number(value) }),
        })

        if (!res.ok) {
          throw new Error('Monitor selection failed')
        }

        setFeedback({ type: 'success', message: `Viewing screen ${value}` })
      } catch {
        setFeedback({ type: 'error', message: 'Failed to change screen' })
      }

      setTimeout(() => setFeedback(null), 3000)
    },
    [apiBase],
  )

  // -----------------------------------------------------------------------
  // WebSocket — live screen feed
  // -----------------------------------------------------------------------
  useEffect(() => {
    if (!isBackendReachable) {
      setConnected(false)
      return undefined
    }

    let ws
    let reconnectTimer
    let isUnmounted = false
    let retryDelayMs = 2000
    const wsUrl = getWsUrl(apiBase)

    const connect = () => {
      if (isUnmounted) return

      ws = new WebSocket(wsUrl)

      ws.onopen = () => {
        if (isUnmounted) return
        setConnected(true)
        retryDelayMs = 2000
      }

      ws.onmessage = (event) => {
        if (imgRef.current) {
          imgRef.current.src = `data:image/png;base64,${event.data}`
        }
      }

      ws.onclose = () => {
        if (isUnmounted) return
        setConnected(false)
        reconnectTimer = setTimeout(connect, retryDelayMs)
        retryDelayMs = Math.min(retryDelayMs * 2, 30000)
      }

      ws.onerror = () => {
        // Browser will emit onclose after onerror if the socket fails.
        // Avoid forcing close while CONNECTING to prevent noisy dev warnings.
      }
    }

    connect()

    return () => {
      isUnmounted = true
      clearTimeout(reconnectTimer)
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.close(1000, 'Component unmounted')
      }
    }
  }, [apiBase, isBackendReachable])

  // -----------------------------------------------------------------------
  // Click handler — capture relative (x, y) and send to backend
  // -----------------------------------------------------------------------
  const handleImageClick = useCallback(
    async (e) => {
      const img = imgRef.current
      if (!img) return

      const rect = img.getBoundingClientRect()

      // Calculate coordinates relative to the actual screen resolution
      const scaleX = img.naturalWidth / rect.width
      const scaleY = img.naturalHeight / rect.height

      const x = Math.round((e.clientX - rect.left) * scaleX)
      const y = Math.round((e.clientY - rect.top) * scaleY)

      setLastClick({ x, y })

      try {
        const res = await fetch(`${apiBase}/api/intervene`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            x,
            y,
            action: actionMode,
            monitor_index: Number(selectedMonitor),
          }),
        })
        const data = await res.json()
        setFeedback({ type: 'success', message: data.message })
      } catch {
        setFeedback({ type: 'error', message: 'Failed to send intervention' })
      }

      // Clear feedback after 3 seconds
      setTimeout(() => setFeedback(null), 3000)
    },
    [actionMode, apiBase, selectedMonitor],
  )

  return (
    <Grid gutter="md">
      <Grid.Col span={{ base: 12, md: 8 }}>
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            {!isBackendReachable
              ? 'Backend offline — start backend on port 8000'
              : connected
              ? `Connected — click on the screen feed to ${actionMode === 'move' ? 'move mouse' : 'click'}`
              : 'Connecting to screen feed…'}
          </Text>

          <Select
            label="Active Screen"
            value={selectedMonitor}
            data={monitors}
            onChange={handleMonitorChange}
            placeholder="Select screen"
          />

          <SegmentedControl
            value={actionMode}
            onChange={setActionMode}
            data={[
              { label: 'Click', value: 'click' },
              { label: 'Move Mouse', value: 'move' },
            ]}
            fullWidth
          />

          <Paper
            shadow="md"
            radius="md"
            style={{ position: 'relative', overflow: 'hidden', lineHeight: 0 }}
          >
            <img
              ref={imgRef}
              alt="Live screen feed"
              onClick={handleImageClick}
              style={{
                width: '100%',
                cursor: 'crosshair',
                display: 'block',
                backgroundColor: '#1a1a2e',
                minHeight: 300,
              }}
            />

            {lastClick && (
              <Text
                size="xs"
                style={{
                  position: 'absolute',
                  bottom: 8,
                  right: 8,
                  background: 'rgba(0,0,0,0.7)',
                  color: '#fff',
                  padding: '4px 8px',
                  borderRadius: 4,
                }}
              >
                Last action: {actionMode} @ ({lastClick.x}, {lastClick.y})
              </Text>
            )}
          </Paper>

          {feedback && (
            <Notification
              color={feedback.type === 'success' ? 'teal' : 'red'}
              title={feedback.type === 'success' ? 'Success' : 'Error'}
              onClose={() => setFeedback(null)}
            >
              {feedback.message}
            </Notification>
          )}
        </Stack>
      </Grid.Col>

      <Grid.Col span={{ base: 12, md: 4 }}>
        <Stack gap="md">
          <ChatPanel apiBase={apiBase} isBackendReachable={isBackendReachable} />

          <Divider />

          <RecordTaskPanel
            apiBase={apiBase}
            isBackendReachable={isBackendReachable}
            monitors={monitors}
            selectedMonitor={selectedMonitor}
          />
        </Stack>
      </Grid.Col>
    </Grid>
  )
}
