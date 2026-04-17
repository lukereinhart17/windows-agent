import { useEffect, useRef, useState, useCallback } from 'react'
import { Paper, Text, Stack, Notification } from '@mantine/core'

const getWsUrl = (apiBase) => {
  const apiUrl = new URL(apiBase)
  const wsProtocol = apiUrl.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${wsProtocol}//${apiUrl.host}/ws/screen`
}

export default function InterventionDashboard({ apiBase }) {
  const imgRef = useRef(null)
  const [connected, setConnected] = useState(false)
  const [lastClick, setLastClick] = useState(null)
  const [feedback, setFeedback] = useState(null)

  // -----------------------------------------------------------------------
  // WebSocket — live screen feed
  // -----------------------------------------------------------------------
  useEffect(() => {
    let ws
    let reconnectTimer
    let isUnmounted = false
    const wsUrl = getWsUrl(apiBase)

    const connect = () => {
      if (isUnmounted) return

      ws = new WebSocket(wsUrl)

      ws.onopen = () => {
        if (isUnmounted) return
        setConnected(true)
      }

      ws.onmessage = (event) => {
        if (imgRef.current) {
          imgRef.current.src = `data:image/png;base64,${event.data}`
        }
      }

      ws.onclose = () => {
        if (isUnmounted) return
        setConnected(false)
        // Attempt to reconnect after 2 seconds
        reconnectTimer = setTimeout(connect, 2000)
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
  }, [apiBase])

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
          body: JSON.stringify({ x, y }),
        })
        const data = await res.json()
        setFeedback({ type: 'success', message: data.message })
      } catch {
        setFeedback({ type: 'error', message: 'Failed to send intervention' })
      }

      // Clear feedback after 3 seconds
      setTimeout(() => setFeedback(null), 3000)
    },
    [apiBase],
  )

  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed">
        {connected
          ? 'Connected — click on the screen feed to intervene'
          : 'Connecting to screen feed…'}
      </Text>

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
            Last click: ({lastClick.x}, {lastClick.y})
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
  )
}
