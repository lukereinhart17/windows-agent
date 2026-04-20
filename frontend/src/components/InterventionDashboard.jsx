import { useEffect, useRef, useState, useCallback } from 'react'
import { Paper, Text, Stack, Notification, SegmentedControl, Select, Grid, Divider } from '@mantine/core'
import ChatPanel from './ChatPanel'
import RecordTaskPanel from './RecordTaskPanel'

const MODEL_CAPABILITIES = {
  gemini: 'Planning + UI reasoning',
  yolo: 'Real-time detection',
  'faster-rcnn': 'High-accuracy detection',
  'mobilenet-shufflenet': 'Fast classification (edge/low-latency)',
  'resnet-efficientnet': 'High-accuracy classification',
  cnnparted: 'Partitioned CNN experiments',
}

const modelLabel = (name) => {
  const note = MODEL_CAPABILITIES[name] || 'General vision model'
  return `${name} - ${note}`
}

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
  const [recordMonitor, setRecordMonitor] = useState('1')
  const [actionMode, setActionMode] = useState('click')
  const [lastClick, setLastClick] = useState(null)
  const [feedback, setFeedback] = useState(null)
  const [availableModels, setAvailableModels] = useState([])
  const [activeModel, setActiveModel] = useState(null)
  const [pipelineMode, setPipelineMode] = useState('single')
  const [detectorModel, setDetectorModel] = useState('yolo')
  const [classifierModel, setClassifierModel] = useState('mobilenet-shufflenet')
  const [plannerModel, setPlannerModel] = useState('gemini')

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
        const initialMonitor = String(data.selected_monitor_index || 1)
        setSelectedMonitor(initialMonitor)
        setRecordMonitor(initialMonitor)
      } catch {
        setFeedback({ type: 'error', message: 'Failed to load screen list' })
      }
    }

    fetchMonitors()
  }, [apiBase, isBackendReachable])

  // Fetch available vision models
  useEffect(() => {
    if (!isBackendReachable) {
      setAvailableModels([])
      setActiveModel(null)
      return
    }

    const fetchModels = async () => {
      try {
        const res = await fetch(`${apiBase}/api/models`)
        if (!res.ok) return
        const data = await res.json()
        const models = (data.models || []).map((m) => ({
          value: m.name,
          label: modelLabel(m.name),
        }))
        setAvailableModels(models)
        const active = (data.models || []).find((m) => m.active)
        setActiveModel(active ? active.name : null)
      } catch {
        // silently ignore
      }
    }

    fetchModels()
  }, [apiBase, isBackendReachable])

  useEffect(() => {
    if (!isBackendReachable) return

    const fetchPipeline = async () => {
      try {
        const res = await fetch(`${apiBase}/api/pipeline`)
        if (!res.ok) return
        const data = await res.json()
        const pipeline = data.pipeline || {}
        setPipelineMode(pipeline.mode || 'single')
        setDetectorModel(pipeline.detector_model || 'yolo')
        setClassifierModel(pipeline.classifier_model || 'mobilenet-shufflenet')
        setPlannerModel(pipeline.planner_model || 'gemini')
      } catch {
        // ignore
      }
    }

    fetchPipeline()
  }, [apiBase, isBackendReachable])

  const handleModelChange = useCallback(
    async (value) => {
      if (!value) return
      setActiveModel(value)

      try {
        const res = await fetch(`${apiBase}/api/models/set`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: value }),
        })

        if (!res.ok) throw new Error('Model switch failed')
        setFeedback({ type: 'success', message: `Model switched to ${value}` })
      } catch {
        setFeedback({ type: 'error', message: 'Failed to switch model' })
      }

      setTimeout(() => setFeedback(null), 3000)
    },
    [apiBase],
  )

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

  const updatePipeline = useCallback(
    async ({ mode, detector, classifier, planner }) => {
      try {
        const res = await fetch(`${apiBase}/api/pipeline`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            mode,
            detector_model: detector,
            classifier_model: classifier,
            planner_model: planner,
          }),
        })

        if (!res.ok) throw new Error('Pipeline update failed')
        setFeedback({ type: 'success', message: `Pipeline updated (${mode})` })
      } catch {
        setFeedback({ type: 'error', message: 'Failed to update pipeline' })
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

          <Select
            label="Vision Model"
            value={activeModel}
            data={availableModels}
            onChange={handleModelChange}
            placeholder="Select model"
          />

          <SegmentedControl
            value={pipelineMode}
            onChange={(value) => {
              setPipelineMode(value)
              updatePipeline({
                mode: value,
                detector: detectorModel,
                classifier: classifierModel,
                planner: plannerModel,
              })
            }}
            data={[
              { label: 'Single Model', value: 'single' },
              { label: 'Cascade', value: 'cascade' },
            ]}
            fullWidth
          />

          {pipelineMode === 'cascade' && (
            <>
              <Select
                label="Detector"
                value={detectorModel}
                data={availableModels}
                onChange={(value) => {
                  if (!value) return
                  setDetectorModel(value)
                  updatePipeline({
                    mode: pipelineMode,
                    detector: value,
                    classifier: classifierModel,
                    planner: plannerModel,
                  })
                }}
                placeholder="Select detector model"
              />

              <Select
                label="Classifier"
                value={classifierModel}
                data={availableModels}
                onChange={(value) => {
                  if (!value) return
                  setClassifierModel(value)
                  updatePipeline({
                    mode: pipelineMode,
                    detector: detectorModel,
                    classifier: value,
                    planner: plannerModel,
                  })
                }}
                placeholder="Select classifier model"
              />

              <Select
                label="Planner"
                value={plannerModel}
                data={availableModels}
                onChange={(value) => {
                  if (!value) return
                  setPlannerModel(value)
                  updatePipeline({
                    mode: pipelineMode,
                    detector: detectorModel,
                    classifier: classifierModel,
                    planner: value,
                  })
                }}
                placeholder="Select planner model"
              />
            </>
          )}

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
          <ChatPanel
            apiBase={apiBase}
            isBackendReachable={isBackendReachable}
            promptMonitor={recordMonitor || selectedMonitor}
          />

          <Divider />

          <RecordTaskPanel
            apiBase={apiBase}
            isBackendReachable={isBackendReachable}
            monitors={monitors}
            selectedMonitor={selectedMonitor}
            recordMonitor={recordMonitor}
            onRecordMonitorChange={(value) => setRecordMonitor(value || selectedMonitor || '1')}
          />
        </Stack>
      </Grid.Col>
    </Grid>
  )
}
