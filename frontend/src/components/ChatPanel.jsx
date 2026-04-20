import { useEffect, useRef, useState, useCallback } from 'react'
import {
  Stack,
  TextInput,
  Paper,
  Text,
  ScrollArea,
  ActionIcon,
  Group,
  Image,
  Modal,
  Badge,
} from '@mantine/core'
import { IconSend, IconTrash, IconPhoto } from '@tabler/icons-react'

export default function ChatPanel({ apiBase, isBackendReachable, promptMonitor }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [previewImage, setPreviewImage] = useState(null)
  const viewport = useRef(null)

  // Fetch chat history on mount / when backend comes online
  useEffect(() => {
    if (!isBackendReachable) return

    const fetchChat = async () => {
      try {
        const res = await fetch(`${apiBase}/api/chat`)
        if (!res.ok) return
        const data = await res.json()
        setMessages(data.messages || [])
      } catch {
        // silently ignore
      }
    }

    fetchChat()
  }, [apiBase, isBackendReachable])

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    if (viewport.current) {
      viewport.current.scrollTo({
        top: viewport.current.scrollHeight,
        behavior: 'smooth',
      })
    }
  }, [messages])

  const sendPrompt = useCallback(async () => {
    const trimmed = input.trim()
    if (!trimmed || sending || !isBackendReachable) return

    // Optimistically add user message
    setMessages((prev) => [...prev, { role: 'user', content: trimmed, screenshot: null }])
    setInput('')
    setSending(true)

    try {
      const res = await fetch(`${apiBase}/api/prompt`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: trimmed,
          monitor_index: Number(promptMonitor || 1),
        }),
      })

      if (!res.ok) throw new Error('Prompt failed')

      const data = await res.json()
      setMessages((prev) => {
        const next = [
          ...prev,
          { role: 'assistant', content: data.reply, screenshot: null },
        ]

        if (data.debug?.latency_ms) {
          const latency = data.debug.latency_ms
          next.push({
            role: 'assistant',
            content: `Pipeline debug: ${JSON.stringify(latency)}`,
            screenshot: null,
          })
        }

        return next
      })
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: 'Error: failed to reach agent', screenshot: null },
      ])
    } finally {
      setSending(false)
    }
  }, [input, sending, apiBase, isBackendReachable, promptMonitor])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendPrompt()
    }
  }

  const clearChat = async () => {
    try {
      await fetch(`${apiBase}/api/chat`, { method: 'DELETE' })
      setMessages([])
    } catch {
      // ignore
    }
  }

  return (
    <Stack gap="xs" style={{ height: '100%' }}>
      <Group justify="space-between">
        <Text fw={600} size="sm">
          Agent Chat
        </Text>
        <ActionIcon
          variant="subtle"
          color="gray"
          size="sm"
          onClick={clearChat}
          title="Clear chat"
        >
          <IconTrash size={14} />
        </ActionIcon>
      </Group>

      <ScrollArea
        style={{ flex: 1, minHeight: 200, maxHeight: 400 }}
        viewportRef={viewport}
      >
        <Stack gap={6} p="xs">
          {messages.length === 0 && (
            <Text size="sm" c="dimmed" ta="center">
              Send a message or record actions to get started
            </Text>
          )}

          {messages.map((msg, i) => (
            <Paper
              key={i}
              p="xs"
              radius="sm"
              style={{
                alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
                maxWidth: '85%',
                backgroundColor:
                  msg.role === 'user'
                    ? 'var(--mantine-color-blue-light)'
                    : 'var(--mantine-color-dark-6)',
              }}
            >
              <Text size="xs" c="dimmed" mb={2}>
                {msg.role === 'user' ? 'You' : 'Agent'}
              </Text>
              <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
                {msg.content}
              </Text>
              {msg.screenshot && (
                <Badge
                  leftSection={<IconPhoto size={12} />}
                  variant="outline"
                  size="xs"
                  mt={4}
                  style={{ cursor: 'pointer' }}
                  onClick={() => setPreviewImage(msg.screenshot)}
                >
                  Screenshot attached
                </Badge>
              )}
            </Paper>
          ))}
        </Stack>
      </ScrollArea>

      <Group gap="xs">
        <TextInput
          placeholder={isBackendReachable ? 'Type a message…' : 'Backend offline'}
          value={input}
          onChange={(e) => setInput(e.currentTarget.value)}
          onKeyDown={handleKeyDown}
          disabled={!isBackendReachable || sending}
          style={{ flex: 1 }}
        />
        <ActionIcon
          variant="filled"
          color="blue"
          size="lg"
          onClick={sendPrompt}
          disabled={!isBackendReachable || sending || !input.trim()}
          loading={sending}
        >
          <IconSend size={16} />
        </ActionIcon>
      </Group>

      <Modal
        opened={!!previewImage}
        onClose={() => setPreviewImage(null)}
        title="Screenshot"
        size="xl"
      >
        {previewImage && (
          <Image src={`data:image/png;base64,${previewImage}`} alt="Recorded screenshot" />
        )}
      </Modal>
    </Stack>
  )
}
