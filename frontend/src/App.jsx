import { AppShell, Title, Badge, Group } from '@mantine/core'
import { useState, useEffect } from 'react'
import InterventionDashboard from './components/InterventionDashboard'

const API_BASE = 'http://localhost:8000'

function App() {
  const [agentStatus, setAgentStatus] = useState('idle')

  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/status`)
        const data = await res.json()
        setAgentStatus(data.status)
      } catch {
        setAgentStatus('disconnected')
      }
    }

    fetchStatus()
    const interval = setInterval(fetchStatus, 3000)
    return () => clearInterval(interval)
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
        <InterventionDashboard apiBase={API_BASE} />
      </AppShell.Main>
    </AppShell>
  )
}

export default App
