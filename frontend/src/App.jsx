import { AppShell, Title, Badge, Group } from '@mantine/core'
import { useState, useEffect } from 'react'
import InterventionDashboard from './components/InterventionDashboard'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

function App() {
  const [agentStatus, setAgentStatus] = useState('idle')
  const [isBackendReachable, setIsBackendReachable] = useState(false)

  useEffect(() => {
    let isUnmounted = false
    let timer
    let retryDelayMs = 3000

    const fetchStatus = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/status`)
        if (!res.ok) throw new Error('Status request failed')
        const data = await res.json()
        if (isUnmounted) return
        setAgentStatus(data.status)
        setIsBackendReachable(true)
        retryDelayMs = 3000
      } catch {
        if (isUnmounted) return
        setAgentStatus('disconnected')
        setIsBackendReachable(false)
        retryDelayMs = Math.min(retryDelayMs * 2, 30000)
      } finally {
        if (!isUnmounted) {
          timer = setTimeout(fetchStatus, retryDelayMs)
        }
      }
    }

    fetchStatus()

    return () => {
      isUnmounted = true
      clearTimeout(timer)
    }
  }, [])

  const statusColor =
    agentStatus === 'idle'
      ? 'blue'
      : agentStatus === 'running'
        ? 'green'
        : agentStatus === 'requires_intervention'
          ? 'orange'
          : 'red'

  return (
    <AppShell header={{ height: 60 }} padding="md">
      <AppShell.Header
        style={{
          display: 'flex',
          alignItems: 'center',
          padding: '0 16px',
        }}
      >
        <Group justify="space-between" style={{ width: '100%' }}>
          <Title order={3}>Windows Agent</Title>
          <Badge color={statusColor} size="lg" variant="filled">
            {agentStatus}
          </Badge>
        </Group>
      </AppShell.Header>

      <AppShell.Main>
        <InterventionDashboard
          apiBase={API_BASE}
          isBackendReachable={isBackendReachable}
        />
      </AppShell.Main>
    </AppShell>
  )
}

export default App
