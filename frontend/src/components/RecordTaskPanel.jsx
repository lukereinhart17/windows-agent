import { useState, useCallback, useRef, useEffect } from 'react'
import {
  Stack,
  Button,
  Text,
  Paper,
  Select,
  Group,
  Badge,
  Image,
  ScrollArea,
  Notification,
} from '@mantine/core'
import { IconPlayerRecord, IconPlayerStop, IconTrash } from '@tabler/icons-react'

export default function RecordTaskPanel({ apiBase, isBackendReachable, monitors, selectedMonitor }) {
  const [isRecording, setIsRecording] = useState(false)
  const [recordMonitor, setRecordMonitor] = useState(selectedMonitor)
  const [recordedSteps, setRecordedSteps] = useState([])
  const [feedback, setFeedback] = useState(null)
  const imgRef = useRef(null)
  const wsRef = useRef(null)
  const viewportRef = useRef(null)

  // Sync monitor selection when parent changes
  useEffect(() => {
    if (!isRecording) {
      setRecordMonitor(selectedMonitor)
    }
  }, [selectedMonitor, isRecording])

  // Start/stop recording WebSocket feed on the recording monitor
  useEffect(() => {
    if (!isRecording || !isBackendReachable) {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.close(1000, 'Recording stopped')
      }
      wsRef.current = null
      return
    }

    const apiUrl = new URL(apiBase)
    const wsProtocol = apiUrl.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${wsProtocol}//${apiUrl.host}/ws/screen`
    const ws = new WebSocket(wsUrl)

    ws.onmessage = (event) => {
      if (imgRef.current) {
        imgRef.current.src = `data:image/png;base64,${event.data}`
      }
    }

    ws.onerror = () => {}
    ws.onclose = () => {}

    wsRef.current = ws

    return () => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.close(1000, 'Cleanup')
      }
    }
  }, [isRecording, isBackendReachable, apiBase])

  const startRecording = useCallback(async () => {
    // Set the backend monitor to the recording one
    if (recordMonitor) {
      try {
        await fetch(`${apiBase}/api/monitor`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ monitor_index: Number(recordMonitor) }),
        })
      } catch {
        // ignore
      }
    }

    setRecordedSteps([])
    setIsRecording(true)
    setFeedback({ type: 'success', message: 'Recording started — click on the screen to capture actions' })
    setTimeout(() => setFeedback(null), 3000)
  }, [apiBase, recordMonitor])

  const stopRecording = useCallback(() => {
    setIsRecording(false)
    setFeedback({
      type: 'success',
      message: `Recording stopped — ${recordedSteps.length} action(s) captured`,
    })
    setTimeout(() => setFeedback(null), 3000)
  }, [recordedSteps.length])

  const uploadToChat = useCallback(async () => {
    if (recordedSteps.length === 0) return

    // Actions are already in chat history via /api/record-action.
    // Just notify the user.
    setFeedback({
      type: 'success',
      message: `${recordedSteps.length} action(s) already added to chat context`,
    })
    setTimeout(() => setFeedback(null), 3000)
  }, [recordedSteps])

  // Click handler during recording — capture action + screenshot
  const handleRecordClick = useCallback(
    async (e) => {
      const img = imgRef.current
      if (!img || !isRecording) return

      const rect = img.getBoundingClientRect()
      const scaleX = img.naturalWidth / rect.width
      const scaleY = img.naturalHeight / rect.height
      const x = Math.round((e.clientX - rect.left) * scaleX)
      const y = Math.round((e.clientY - rect.top) * scaleY)

      try {
        const res = await fetch(`${apiBase}/api/record-action`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            x,
            y,
            action: 'click',
            monitor_index: Number(recordMonitor),
          }),
        })
        if (!res.ok) throw new Error('Record failed')

        const data = await res.json()
        setRecordedSteps((prev) => [...prev, data.recorded_action])
        setFeedback({ type: 'success', message: `Recorded click at (${x}, ${y})` })
        setTimeout(() => setFeedback(null), 2000)
      } catch {
        setFeedback({ type: 'error', message: 'Failed to record action' })
        setTimeout(() => setFeedback(null), 3000)
      }
    },
    [isRecording, apiBase, recordMonitor],
  )

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Text fw={600} size="sm">
          Record Task
        </Text>
        {isRecording && (
          <Badge color="red" variant="filled" size="sm">
            ● Recording
          </Badge>
        )}
      </Group>

      {!isRecording && (
        <Select
          label="Screen to record"
          value={recordMonitor}
          data={monitors}
          onChange={setRecordMonitor}
          placeholder="Select screen"
          size="xs"
        />
      )}

      <Group gap="xs">
        {!isRecording ? (
          <Button
            leftSection={<IconPlayerRecord size={16} />}
            color="red"
            variant="light"
            size="xs"
            onClick={startRecording}
            disabled={!isBackendReachable}
          >
            Start Recording
          </Button>
        ) : (
          <Button
            leftSection={<IconPlayerStop size={16} />}
            color="gray"
            variant="light"
            size="xs"
            onClick={stopRecording}
          >
            Stop Recording
          </Button>
        )}

        {!isRecording && recordedSteps.length > 0 && (
          <>
            <Button
              size="xs"
              variant="light"
              onClick={uploadToChat}
            >
              Upload to Chat ({recordedSteps.length})
            </Button>
            <Button
              size="xs"
              variant="subtle"
              color="gray"
              leftSection={<IconTrash size={14} />}
              onClick={() => setRecordedSteps([])}
            >
              Clear
            </Button>
          </>
        )}
      </Group>

      {isRecording && (
        <Paper
          shadow="md"
          radius="md"
          style={{ position: 'relative', overflow: 'hidden', lineHeight: 0 }}
        >
          <img
            ref={imgRef}
            alt="Recording screen feed"
            onClick={handleRecordClick}
            style={{
              width: '100%',
              cursor: 'crosshair',
              display: 'block',
              backgroundColor: '#1a1a2e',
              minHeight: 200,
              border: '2px solid var(--mantine-color-red-6)',
              borderRadius: 'var(--mantine-radius-md)',
            }}
          />
        </Paper>
      )}

      {recordedSteps.length > 0 && (
        <ScrollArea style={{ maxHeight: 200 }} viewportRef={viewportRef}>
          <Stack gap={4}>
            {recordedSteps.map((step, i) => (
              <Paper key={i} p="xs" radius="sm" withBorder>
                <Group gap="xs">
                  <Badge size="xs" variant="outline">
                    {i + 1}
                  </Badge>
                  <Text size="xs">
                    {step.action} at ({step.x}, {step.y})
                  </Text>
                  {step.screenshot && (
                    <Image
                      src={`data:image/png;base64,${step.screenshot}`}
                      alt={`Step ${i + 1}`}
                      h={40}
                      w="auto"
                      fit="contain"
                      radius="xs"
                    />
                  )}
                </Group>
              </Paper>
            ))}
          </Stack>
        </ScrollArea>
      )}

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
