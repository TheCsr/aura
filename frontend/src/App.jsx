import React, { useState, useEffect } from 'react'
import {
  Shield,
  Activity,
  Ticket as TicketIcon,
  History,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Play,
  ArrowRight,
  RefreshCw,
  Cpu,
  Mic,
  FileText,
  Send,
  Cloud,
  Search,
  Info,
  ChevronRight,
  Eye,
  Sparkles,
  Layers,
  BarChart3,
  MessageCircle
} from 'lucide-react'

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard')
  const [systemStatus, setSystemStatus] = useState(null)
  const [latestResult, setLatestResult] = useState(null)
  const [events, setEvents] = useState([])
  const [tickets, setTickets] = useState([])
  const [metrics, setMetrics] = useState(null)
  const [baselineSuggestions, setBaselineSuggestions] = useState([])
  const [buddies, setBuddies] = useState([])
  const [buddyRequests, setBuddyRequests] = useState([])
  const [statusMessage, setStatusMessage] = useState(null)

  // Ticket filter state
  const [ticketFilter, setTicketFilter] = useState('all')
  const [ticketSearch, setTicketSearch] = useState('')
  const [caregiverNotes, setCaregiverNotes] = useState({})
  const [feedbackDrafts, setFeedbackDrafts] = useState({})

  // Active agent inspect tab
  const [agentInspectTab, setAgentInspectTab] = useState('supervisor')

  // Refresh shared dashboard state continuously while the worker processes events.
  useEffect(() => {
    let active = true
    let timer

    const refresh = async () => {
      await Promise.all([
        fetchStatus(), fetchLatest(), fetchTickets(), fetchEvents(),
        fetchMetrics(), fetchBaselineSuggestions(), fetchBuddies(), fetchBuddyRequests(),
      ])
      if (active) {
        timer = window.setTimeout(refresh, 3000)
      }
    }

    refresh()
    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [])

  const fetchStatus = async () => {
    try {
      const res = await fetch('/api/status')
      if (res.ok) {
        const data = await res.json()
        setSystemStatus(data)
      }
    } catch (e) {
      console.error(e)
    }
  }

  const fetchLatest = async () => {
    try {
      const res = await fetch('/api/latest')
      if (res.ok) {
        const data = await res.json()
        setLatestResult(data.latest)
      }
    } catch (e) {
      console.error(e)
    }
  }

  const fetchTickets = async () => {
    try {
      const res = await fetch('/api/tickets')
      if (res.ok) {
        const data = await res.json()
        setTickets(data.tickets || [])
      }
    } catch (e) {
      console.error(e)
    }
  }

  const fetchEvents = async () => {
    try {
      const res = await fetch('/api/events')
      if (res.ok) {
        const data = await res.json()
        setEvents(data.events || [])
      }
    } catch (e) {
      console.error(e)
    }
  }

  const fetchMetrics = async () => {
    try {
      const res = await fetch('/api/metrics')
      if (res.ok) setMetrics(await res.json())
    } catch (e) {
      console.error(e)
    }
  }

  const fetchBaselineSuggestions = async () => {
    try {
      const res = await fetch('/api/baseline-suggestions')
      if (res.ok) {
        const data = await res.json()
        setBaselineSuggestions(data.suggestions || [])
      }
    } catch (e) {
      console.error(e)
    }
  }

  const fetchBuddies = async () => {
    try {
      const res = await fetch('/api/buddies')
      if (res.ok) {
        const data = await res.json()
        setBuddies(data.buddies || [])
      }
    } catch (e) {
      console.error(e)
    }
  }

  const fetchBuddyRequests = async () => {
    try {
      const res = await fetch('/api/buddy-requests')
      if (res.ok) {
        const data = await res.json()
        setBuddyRequests(data.requests || [])
      }
    } catch (e) {
      console.error(e)
    }
  }

  const handleRequestBuddy = async (buddyId) => {
    const subjectId = latestResult?.sensor_data?.subject_id || 'synthetic-resident-1'
    try {
      const res = await fetch(`/api/buddies/${encodeURIComponent(buddyId)}/request`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ subject_id: subjectId }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Demo buddy request could not be recorded.')
      await fetchBuddyRequests()
      setStatusMessage({ type: 'success', text: 'Demo conversation request recorded. No one was contacted.' })
    } catch (e) {
      setStatusMessage({ type: 'error', text: e.message })
    }
  }

  const handleUpdateTicket = async (ticketId, newStatus, notes) => {
    try {
      const body = {}
      if (newStatus) body.status = newStatus
      if (notes !== undefined) body.notes = notes

      const res = await fetch(`/api/tickets/${ticketId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (res.ok) {
        await Promise.all([fetchTickets(), fetchStatus()])
        setStatusMessage({ type: 'success', text: `Ticket ${ticketId} updated.` })
      }
    } catch (e) {
      setStatusMessage({ type: 'error', text: e.message })
    }
  }

  const handleSubmitFeedback = async (ticketId) => {
    const feedback = feedbackDrafts[ticketId]
    if (!feedback?.label) return
    try {
      const res = await fetch(`/api/tickets/${ticketId}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(feedback),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Feedback could not be saved.')
      await Promise.all([fetchTickets(), fetchMetrics(), fetchBaselineSuggestions()])
      setStatusMessage({ type: 'success', text: 'Caseworker feedback saved.' })
    } catch (e) {
      setStatusMessage({ type: 'error', text: e.message })
    }
  }

  const handleApplyBaselineSuggestion = async (suggestion) => {
    try {
      const path = `/api/baseline-suggestions/${encodeURIComponent(suggestion.subject_id)}/${encodeURIComponent(suggestion.field)}/apply`
      const res = await fetch(path, { method: 'POST' })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Baseline suggestion could not be applied.')
      await Promise.all([fetchBaselineSuggestions(), fetchMetrics()])
      setStatusMessage({ type: 'success', text: `Baseline updated for ${suggestion.field}.` })
    } catch (e) {
      setStatusMessage({ type: 'error', text: e.message })
    }
  }

  return (
    <div className="flex h-screen bg-[#090d16] text-slate-100 font-sans overflow-hidden">
      {/* Sidebar */}
      <aside className="w-72 bg-[#0e1626] border-r border-slate-800/80 flex flex-col justify-between shrink-0">
        <div>
          {/* Logo Brand */}
          <div className="p-6 border-b border-slate-800/80 flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-cyan-500 to-indigo-600 flex items-center justify-center shadow-lg shadow-cyan-500/20">
              <Shield className="w-5 h-5 text-white" />
            </div>
            <div>
              <h1 className="font-bold text-lg text-white tracking-wide">AURA CARE</h1>
              <p className="text-xs text-slate-400 font-medium">Multi-Agent Eldercare</p>
            </div>
          </div>

          {/* Navigation Links */}
          <nav className="p-4 space-y-1.5">
            <button
              onClick={() => setActiveTab('dashboard')}
              className={`w-full flex items-center justify-between px-4 py-3 rounded-xl text-sm font-medium transition-all ${
                activeTab === 'dashboard'
                  ? 'bg-gradient-to-r from-cyan-500/20 to-indigo-500/20 text-cyan-300 border border-cyan-500/30 shadow-md shadow-cyan-950/40'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
              }`}
            >
              <div className="flex items-center gap-3">
                <Activity className="w-4 h-4" />
                <span>Agent Workflow</span>
              </div>
              {latestResult && (
                <span className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
              )}
            </button>

            <button
              onClick={() => setActiveTab('tickets')}
              className={`w-full flex items-center justify-between px-4 py-3 rounded-xl text-sm font-medium transition-all ${
                activeTab === 'tickets'
                  ? 'bg-gradient-to-r from-cyan-500/20 to-indigo-500/20 text-cyan-300 border border-cyan-500/30 shadow-md shadow-cyan-950/40'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
              }`}
            >
              <div className="flex items-center gap-3">
                <TicketIcon className="w-4 h-4" />
                <span>Caregiver Tickets</span>
              </div>
              {tickets.length > 0 && (
                <span className="px-2 py-0.5 text-xs font-semibold rounded-full bg-indigo-500/30 text-indigo-300 border border-indigo-500/40">
                  {tickets.length}
                </span>
              )}
            </button>

            <button
              onClick={() => setActiveTab('history')}
              className={`w-full flex items-center justify-between px-4 py-3 rounded-xl text-sm font-medium transition-all ${
                activeTab === 'history'
                  ? 'bg-gradient-to-r from-cyan-500/20 to-indigo-500/20 text-cyan-300 border border-cyan-500/30 shadow-md shadow-cyan-950/40'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
              }`}
            >
              <div className="flex items-center gap-3">
                <History className="w-4 h-4" />
                <span>Event History</span>
              </div>
              {events.length > 0 && (
                <span className="text-xs text-slate-500 font-mono">
                  {events.length}
                </span>
              )}
            </button>

            <button
              onClick={() => setActiveTab('review')}
              className={`w-full flex items-center justify-between px-4 py-3 rounded-xl text-sm font-medium transition-all ${
                activeTab === 'review'
                  ? 'bg-gradient-to-r from-cyan-500/20 to-indigo-500/20 text-cyan-300 border border-cyan-500/30 shadow-md shadow-cyan-950/40'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
              }`}
            >
              <div className="flex items-center gap-3">
                <BarChart3 className="w-4 h-4" />
                <span>Dismissed & Metrics</span>
              </div>
              {metrics?.dismissed_critical_review > 0 && (
                <span className="px-2 py-0.5 text-xs font-semibold rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/30">
                  {metrics.dismissed_critical_review}
                </span>
              )}
            </button>

            <button
              onClick={() => setActiveTab('buddy')}
              className={`w-full flex items-center justify-between px-4 py-3 rounded-xl text-sm font-medium transition-all ${
                activeTab === 'buddy'
                  ? 'bg-gradient-to-r from-cyan-500/20 to-indigo-500/20 text-cyan-300 border border-cyan-500/30 shadow-md shadow-cyan-950/40'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
              }`}
            >
              <div className="flex items-center gap-3">
                <MessageCircle className="w-4 h-4" />
                <span>Buddy</span>
              </div>
            </button>
          </nav>
        </div>

        {/* System Status Panel */}
        <div className="p-4 m-4 rounded-xl bg-[#090d16] border border-slate-800 text-xs space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-slate-400 flex items-center gap-1.5 font-medium">
              <Cloud className="w-3.5 h-3.5 text-cyan-400" /> LavinMQ Broker
            </span>
            <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${
              systemStatus?.broker_configured ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' : 'bg-slate-700/50 text-slate-400'
            }`}>
              {systemStatus?.broker_configured ? 'CONNECTED' : 'DIRECT ONLY'}
            </span>
          </div>
          {systemStatus?.broker_host && (
            <p className="text-[11px] text-slate-500 truncate font-mono">{systemStatus.broker_host}</p>
          )}
          <div className="pt-1 border-t border-slate-800/80 flex justify-between text-slate-400 text-[11px]">
            <span>Active Tickets: <strong className="text-white">{systemStatus?.total_tickets || 0}</strong></span>
            <span>Awaiting: <strong className="text-amber-400">{systemStatus?.awaiting_review || 0}</strong></span>
          </div>
        </div>
      </aside>

      {/* Main Content Area */}
      <main className="flex-1 flex flex-col h-screen overflow-y-auto bg-gradient-to-b from-[#090d16] to-[#0c1220]">
        {/* Top Header */}
        <header className="h-16 border-b border-slate-800/80 px-8 flex items-center justify-between shrink-0 bg-[#090d16]/70 backdrop-blur-md sticky top-0 z-20">
          <div>
            <h2 className="text-base font-semibold text-white">
              {activeTab === 'dashboard' && 'Operations Dashboard & Agent Workflow'}
              {activeTab === 'tickets' && 'Caregiver Review & Incident Tickets'}
              {activeTab === 'history' && 'Audited Sensor Telemetry History'}
              {activeTab === 'review' && 'Dismissed Events & Operations Metrics'}
              {activeTab === 'buddy' && 'Demo Buddy'}
            </h2>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={() => {
                fetchStatus()
                fetchLatest()
                fetchTickets()
                fetchEvents()
                fetchMetrics()
                fetchBaselineSuggestions()
                fetchBuddies()
                fetchBuddyRequests()
              }}
              className="p-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors"
              title="Refresh State"
            >
              <RefreshCw className="w-4 h-4" />
            </button>
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-800/60 border border-slate-700/50 text-xs text-slate-300">
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping" />
              <span>Pipeline Active</span>
            </div>
          </div>
        </header>

        {/* Alert Notification Toast */}
        {statusMessage && (
          <div className={`mx-8 mt-4 p-3 rounded-xl text-sm flex items-center justify-between border ${
            statusMessage.type === 'success'
              ? 'bg-emerald-950/40 border-emerald-500/40 text-emerald-200'
              : statusMessage.type === 'error'
              ? 'bg-rose-950/40 border-rose-500/40 text-rose-200'
              : 'bg-cyan-950/40 border-cyan-500/40 text-cyan-200'
          }`}>
            <span>{statusMessage.text}</span>
            <button onClick={() => setStatusMessage(null)} className="text-xs opacity-70 hover:opacity-100">✕</button>
          </div>
        )}

        {/* Tab View Container */}
        <div className="p-8 flex-1">
          {activeTab === 'dashboard' && (
            <DashboardView
              latestResult={latestResult}
              events={events}
              agentInspectTab={agentInspectTab}
              setAgentInspectTab={setAgentInspectTab}
              onNavigateTickets={() => setActiveTab('tickets')}
            />
          )}

          {activeTab === 'tickets' && (
            <TicketsView
              tickets={tickets}
              filter={ticketFilter}
              setFilter={setTicketFilter}
              search={ticketSearch}
              setSearch={setTicketSearch}
              onUpdateTicket={handleUpdateTicket}
              onSubmitFeedback={handleSubmitFeedback}
              feedbackDrafts={feedbackDrafts}
              setFeedbackDrafts={setFeedbackDrafts}
              notesState={caregiverNotes}
              setNotesState={setCaregiverNotes}
            />
          )}

          {activeTab === 'history' && (
            <HistoryView events={events} />
          )}

          {activeTab === 'review' && (
            <DismissedMetricsView
              events={events}
              metrics={metrics}
              baselineSuggestions={baselineSuggestions}
              onApplyBaselineSuggestion={handleApplyBaselineSuggestion}
            />
          )}

          {activeTab === 'buddy' && (
            <BuddyView
              buddies={buddies}
              requests={buddyRequests}
              subjectId={latestResult?.sensor_data?.subject_id || 'synthetic-resident-1'}
              onRequest={handleRequestBuddy}
            />
          )}
        </div>
      </main>
    </div>
  )
}

function SensorReadingsPanel({ title, result, emptyMessage }) {
  const sensorData = result?.sensor_data
  const observations = sensorData?.observations || {}

  return (
    <section className="rounded-2xl border border-white/[0.07] bg-[#0c121e]/70 px-5 py-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-xs font-medium uppercase tracking-widest text-slate-300">{title}</h3>
        <span className={`rounded-full border px-2.5 py-1 text-[10px] font-mono uppercase tracking-wide ${
          !sensorData
            ? 'border-white/10 bg-slate-900/40 text-slate-400'
            : sensorData.is_mock
            ? 'border-amber-500/25 bg-amber-950/20 text-amber-300'
            : 'border-emerald-500/25 bg-emerald-950/20 text-emerald-300'
        }`}>
          {!sensorData ? 'No event' : sensorData.is_mock ? 'Demo data' : 'Received'}
        </span>
      </div>
      {sensorData ? (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              ['Temperature', observations.temperature_c == null ? '—' : `${observations.temperature_c} °C`],
              ['Humidity', observations.humidity_pct == null ? '—' : `${observations.humidity_pct}%`],
              ['Door', observations.door_state ?? '—'],
              ['Motion', observations.motion_detected == null ? '—' : observations.motion_detected ? 'Detected' : 'None'],
            ].map(([label, value]) => (
              <div key={label} className="border-l border-white/10 pl-3">
                <span className="block text-[10px] uppercase tracking-wider text-slate-500">{label}</span>
                <span className="mt-1 block text-sm font-medium capitalize text-slate-100">{value}</span>
              </div>
            ))}
          </div>
          <p className="mt-3 text-[10px] text-slate-500">
            Event: {result.event_id} · {sensorData.source} · {sensorData.occurred_at}
          </p>
        </>
      ) : (
        <p className="py-4 text-sm font-light text-slate-500">{emptyMessage}</p>
      )}
    </section>
  )
}

function DashboardView({
  latestResult,
  events,
  agentInspectTab,
  setAgentInspectTab,
  onNavigateTickets,
}) {
  const [selectedAgent, setSelectedAgent] = useState('supervisor')
  const [showRawJson, setShowRawJson] = useState(false)

  // Auto-focus the terminal agent when outcome changes
  useEffect(() => {
    if (!latestResult) return
    if (latestResult.outcome === 'awaiting_resident' || latestResult.outcome === 'waiting_for_resident' || latestResult.outcome === 'voice_expired') {
      setSelectedAgent('voice')
    } else if (latestResult.outcome === 'sensor_fault') {
      setSelectedAgent('dispatcher')
    } else if (latestResult.outcome === 'dispatched' || latestResult.outcome === 'escalated_no_reply') {
      setSelectedAgent('dispatcher')
    } else if (latestResult.outcome === 'held' || latestResult.outcome === 'denied') {
      setSelectedAgent('guardian')
    } else if (latestResult.outcome === 'dismissed') {
      setSelectedAgent('triage')
    } else if (latestResult.outcome === 'no_action') {
      setSelectedAgent('supervisor')
    } else if (latestResult.outcome === 'dropped') {
      setSelectedAgent('supervisor')
    }
  }, [latestResult?.event_id, latestResult?.outcome])

  const hasEvent = Boolean(latestResult)
  const outcome = latestResult?.outcome ?? 'waiting'
  const isAnomaly = Boolean(latestResult?.supervisor?.is_anomaly || latestResult?.supervisor?.requires_review)
  const waitingForResident = outcome === 'awaiting_resident' || outcome === 'waiting_for_resident'
  const voiceExpired = outcome === 'voice_expired'
  const escalatedNoReply = outcome === 'escalated_no_reply'
  const voiceExecuted = Boolean(latestResult?.resident_reply)
  const triageExecuted = Boolean(latestResult?.triage)
  const triagePasses = latestResult?.triage_passes?.length
    ? latestResult.triage_passes
    : latestResult?.triage ? [latestResult.triage] : []
  const voiceSkipped = hasEvent && !waitingForResident && !voiceExecuted && !voiceExpired && !escalatedNoReply
  const guardianStatus = latestResult?.guardian?.status
  const isApproved = guardianStatus === 'approve'
  const isHeld = guardianStatus === 'hold'
  const isDenied = guardianStatus === 'deny'
  const ticketId = latestResult?.dashboard_record_id
  const visibleEvents = latestResult
    ? [latestResult, ...events.filter((event) => event.event_id !== latestResult.event_id)]
    : events
  const lastCriticalResult = visibleEvents.find((event) => {
    const reasonCodes = event.guardian?.reason_codes || []
    return event.guardian?.status === 'approve' && reasonCodes.some(
      (reason) => reason.startsWith('critical_')
        || reason === 'door_open_without_motion'
        || reason === 'resident_reply_distress',
    )
  }) || null

  // Scandinavian Status Palette
  const outcomeMeta = {
    awaiting_resident: {
      badge: 'WAITING FOR RESIDENT REPLY',
      tagColor: 'text-cyan-300 border-cyan-500/30 bg-cyan-950/20',
      icon: Mic,
    },
    waiting_for_resident: {
      badge: 'WAITING FOR RESIDENT REPLY',
      tagColor: 'text-cyan-300 border-cyan-500/30 bg-cyan-950/20',
      icon: Mic,
    },
    voice_expired: {
      badge: 'REPLY TIMED OUT · NO AUTOMATIC DISPATCH',
      tagColor: 'text-amber-300 border-amber-500/30 bg-amber-950/20',
      icon: AlertTriangle,
    },
    escalated_no_reply: {
      badge: 'NO REPLY · FOLLOW-UP ESCALATED',
      tagColor: 'text-amber-300 border-amber-500/30 bg-amber-950/20',
      accentColor: '#f59e0b',
      halo: 'shadow-amber-500/20',
      desc: latestResult?.triage?.action_type === 'urgent_visit'
        ? 'No reply arrived before the critical deadline; Guardian approved an urgent follow-up.'
        : 'No reply arrived before the moderate deadline; Guardian approved a remote follow-up.',
      icon: AlertTriangle,
    },
    sensor_fault: {
      badge: 'SENSOR FAULT · MAINTENANCE TICKET',
      tagColor: 'text-amber-300 border-amber-500/30 bg-amber-950/20',
      accentColor: '#f59e0b',
      halo: 'shadow-amber-500/20',
      desc: 'Sensor-quality evidence was routed to maintenance; no resident visit was requested.',
      icon: AlertTriangle,
    },
    dispatched: {
      badge: 'DISPATCHED · INTERVENTION APPROVED',
      nordicText: 'Åtgärd Godkänd & Skickad',
      tagColor: 'text-emerald-400 border-emerald-500/30 bg-emerald-950/20',
      accentColor: '#10b981',
      halo: 'shadow-emerald-500/20',
      desc: 'All four care agents approved the intervention. Caregiver ticket is created.',
      icon: CheckCircle2,
    },
    no_action: {
      badge: 'NO ACTION · BASELINE MATCH',
      nordicText: 'Normalt Läge · Avslutat',
      tagColor: 'text-slate-300 border-white/10 bg-slate-900/40',
      accentColor: '#94a3b8',
      halo: 'shadow-slate-500/10',
      desc: 'Telemetry aligns with the resident baseline. Task dropped at Supervisor.',
      icon: Info,
    },
    dropped: {
      badge: latestResult?.supervisor?.drop_reason === 'awaiting_persistence'
        ? 'WAITING FOR CONFIRMATION · READING DEBOUNCED'
        : 'REPEATED ANOMALY SUPPRESSED',
      tagColor: 'text-amber-300 border-amber-500/30 bg-amber-950/20',
      accentColor: '#f59e0b',
      halo: 'shadow-amber-500/20',
      desc: latestResult?.supervisor?.drop_reason === 'awaiting_persistence'
        ? 'One more consecutive non-critical reading is required before follow-up.'
        : 'This anomaly is already being handled for the resident.',
      icon: AlertTriangle,
    },
    dismissed: {
      badge: 'DISMISSED · NO FOLLOW-UP ACTION',
      tagColor: 'text-slate-300 border-white/10 bg-slate-900/40',
      accentColor: '#94a3b8',
      halo: 'shadow-slate-500/10',
      desc: latestResult?.triage?.rationale || 'Triage dismissed the event; the reason is retained in event history.',
      icon: Info,
    },
    held: {
      badge: 'HELD · POLICY THRESHOLD NOT MET',
      nordicText: 'Avvaktan · Policygräns Ej Nådd',
      tagColor: 'text-amber-300 border-amber-500/30 bg-amber-950/20',
      accentColor: '#f59e0b',
      halo: 'shadow-amber-500/20',
      desc: 'Anomaly noted, but critical risk threshold was not met. Task dropped at Guardian.',
      icon: AlertTriangle,
    },
    denied: {
      badge: 'DENIED · ACTION BLOCKED',
      nordicText: 'Nekad · Åtgärd Stoppad',
      tagColor: 'text-rose-300 border-rose-500/30 bg-rose-950/20',
      accentColor: '#f43f5e',
      halo: 'shadow-rose-500/20',
      desc: 'Guardian safety policy denied the proposed triage action.',
      icon: XCircle,
    },
  }[outcome] || {
    badge: outcome === 'waiting' ? 'WAITING FOR SENSOR EVENT' : outcome.toUpperCase(),
    nordicText: 'Status Avslutad',
    tagColor: 'text-slate-300 border-white/10 bg-slate-900/40',
    accentColor: '#38bdf8',
    halo: 'shadow-cyan-500/10',
    desc: outcome === 'waiting' ? 'The agent pipeline is ready for incoming sensor data.' : 'Processing complete.',
    icon: Info,
  }

  const OutcomeIcon = outcomeMeta.icon

  // Ordered stages in the agent pipeline.
  const agents = [
    {
      id: 'supervisor',
      name: 'Supervisor',
      role: 'Anomaly Assessment',
      swedishRole: 'Avvikelseanalys',
      icon: Eye,
      flowing: isAnomaly,
      dropped: hasEvent && !isAnomaly,
      passToNext: isAnomaly,
      statusLabel: !hasEvent ? 'Waiting' : isAnomaly ? 'Anomaly Flagged' : 'Normal · Dropped',
      color: '#38bdf8', // Nordic glacier blue
    },
    {
      id: 'voice',
      name: 'Resident check-in',
      role: 'If Triage asks',
      swedishRole: 'Röst & Akustik',
      icon: Mic,
      flowing: waitingForResident || voiceExecuted,
      dropped: voiceExpired || voiceSkipped,
      passToNext: voiceExecuted || escalatedNoReply,
      statusLabel: !hasEvent ? 'Waiting' : voiceSkipped ? 'Skipped · not requested' : waitingForResident ? 'Awaiting reply' : escalatedNoReply ? 'Escalated · No reply' : voiceExpired ? 'Reply expired' : voiceExecuted ? 'Reply received' : 'Bypassed',
      color: '#818cf8', // Lavender mist
    },
    {
      id: 'triage',
      name: 'Triage Agent',
      role: 'Clinical Action Proposal',
      swedishRole: 'Åtgärdsförslag',
      icon: Layers,
      flowing: triageExecuted,
      dropped: false,
      passToNext: triageExecuted,
      statusLabel: !hasEvent ? 'Waiting' : triageExecuted ? `${triagePasses.length} Triage pass${triagePasses.length === 1 ? '' : 'es'}` : 'Bypassed',
      color: '#c084fc', // Soft heather
    },
    {
      id: 'guardian',
      name: 'Guardian Policy',
      role: 'Deterministic Safety Gate',
      swedishRole: 'Säkerhetsgrind',
      icon: Shield,
      flowing: isApproved,
      dropped: isHeld || isDenied,
      passToNext: isApproved,
      statusLabel: !hasEvent ? 'Waiting' : isApproved ? 'Policy Approved' : isHeld ? 'Policy Held' : isDenied ? 'Policy Denied' : 'Bypassed',
      color: isApproved ? '#34d399' : isHeld ? '#fbbf24' : '#f87171',
    },
    {
      id: 'dispatcher',
      name: 'Dispatcher',
      role: 'Ticket & Action Execution',
      swedishRole: 'Verkställande',
      icon: CheckCircle2,
      flowing: !!ticketId,
      dropped: false,
      passToNext: false,
      statusLabel: !hasEvent || waitingForResident ? 'Waiting' : ticketId ? 'Ticket Dispatched' : 'No Action',
      color: '#10b981', // Scandinavian forest green
    },
  ]

  const activeAgentData = agents.find((a) => a.id === selectedAgent) || agents[0]

  return (
    <div className="space-y-8 max-w-7xl mx-auto">
      <section className="rounded-3xl border border-white/[0.06] bg-[#0c121e]/70 p-5 backdrop-blur-xl">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-medium text-white">Agent pipeline</h3>
          {!hasEvent && <span className="text-xs text-slate-500">Ready for incoming sensor data</span>}
        </div>
        <div className="-mt-2 flex items-start overflow-x-auto px-2 pb-3 pt-4">
          {agents.map((agent, index) => {
            const Icon = agent.icon
            const isSelected = selectedAgent === agent.id
            return (
              <React.Fragment key={agent.id}>
                <button
                  type="button"
                  onClick={() => setSelectedAgent(agent.id)}
                  className="group flex min-w-[118px] flex-col items-center px-2 text-center outline-none sm:min-w-[142px]"
                  aria-pressed={isSelected}
                  aria-controls="agent-detail-inspector"
                  title={`Inspect ${agent.name}`}
                >
                  <span className={`relative grid h-14 w-14 place-items-center rounded-full border transition-all duration-300 ${
                    agent.flowing
                      ? 'border-white/30 bg-slate-900 shadow-lg'
                      : agent.dropped
                      ? 'border-amber-500/35 bg-amber-950/20'
                      : isSelected
                      ? 'border-white/35 bg-slate-900/80'
                      : 'border-white/[0.08] bg-slate-950/40'
                  }`}>
                    {agent.flowing && (
                      <span
                        className="absolute -inset-2 rounded-full animate-pulse-ring"
                        style={{
                          backgroundColor: `${agent.color}18`,
                          border: `1px solid ${agent.color}70`,
                        }}
                      />
                    )}
                    <Icon
                      className="relative h-5 w-5"
                      style={{ color: agent.flowing ? agent.color : isSelected ? '#e2e8f0' : '#64748b' }}
                    />
                  </span>
                  <span className="mt-3 text-xs font-medium text-slate-200">{agent.name}</span>
                  <span className={`mt-1 text-[10px] ${
                    agent.flowing ? 'text-emerald-300' : agent.dropped ? 'text-amber-300' : 'text-slate-500'
                  }`}>
                    {agent.statusLabel}
                  </span>
                </button>
                {index < agents.length - 1 && (
                  <div className="relative mt-7 h-px min-w-6 flex-1 bg-white/10 sm:min-w-10">
                    {agent.passToNext && hasEvent && (
                      <span
                        className="absolute inset-y-0 left-0 w-full animate-pulse"
                        style={{ backgroundColor: agent.color, boxShadow: `0 0 8px ${agent.color}` }}
                      />
                    )}
                    {agent.dropped && hasEvent && (
                      <span className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full border border-amber-300 bg-amber-500" />
                    )}
                  </div>
                )}
              </React.Fragment>
            )
          })}
        </div>
        {hasEvent && (
          <div className="mt-1 flex justify-end border-t border-white/[0.06] pt-3">
            <span className={`max-w-full truncate rounded-full border px-3 py-1 text-[10px] font-mono uppercase tracking-wider ${outcomeMeta.tagColor}`}>
              {outcomeMeta.badge}
            </span>
          </div>
        )}
      </section>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <SensorReadingsPanel
          title="Current incoming data"
          result={latestResult}
          emptyMessage="Waiting for the first sensor event."
        />
        <SensorReadingsPanel
          title="Last critical event"
          result={lastCriticalResult}
          emptyMessage="No Guardian-approved critical event has been recorded yet."
        />
      </div>

      {/* Agent detail inspector */}
      <div className="grid grid-cols-1 gap-8">
        <div
          id="agent-detail-inspector"
          role="region"
          aria-label={`${activeAgentData.name} details`}
          className="flex w-full flex-col rounded-3xl border border-white/[0.07] bg-[#0c121e]/80 p-6 shadow-2xl backdrop-blur-2xl space-y-6"
        >
          {/* Header of Inspector */}
          <div className="flex items-start justify-between pb-4 border-b border-white/[0.06]">
            <div>
              <div className="flex items-center gap-2">
                <span
                  className="w-2.5 h-2.5 rounded-full"
                  style={{ backgroundColor: activeAgentData.color }}
                />
                <span className="text-xs font-mono uppercase tracking-widest text-slate-400">
                  {activeAgentData.swedishRole}
                </span>
              </div>
              <h3 className="text-xl font-light tracking-tight text-white mt-0.5">
                {activeAgentData.name}
              </h3>
              <p className="text-xs text-slate-400 font-light">{activeAgentData.role}</p>
            </div>

            <span
              className={`text-xs px-3 py-1 rounded-full font-mono uppercase tracking-wider border ${
                activeAgentData.flowing
                  ? 'border-emerald-500/40 bg-emerald-950/20 text-emerald-300'
                  : activeAgentData.dropped
                  ? 'border-amber-500/40 bg-amber-950/20 text-amber-300'
                  : 'border-white/10 bg-slate-900/40 text-slate-400'
              }`}
            >
              {activeAgentData.statusLabel}
            </span>
          </div>

          {/* Detailed Agent Content Body */}
          <div className="space-y-4">
            {!hasEvent ? (
              <p className="rounded-2xl border border-white/[0.05] bg-slate-950/40 py-8 text-center text-sm font-light text-slate-500">
                This agent is ready and will show its actions when a sensor event arrives.
              </p>
            ) : (
              <>
            {/* SUPERVISOR VIEW */}
            {selectedAgent === 'supervisor' && (
              <div className="space-y-4">
                <div className="p-4 rounded-2xl bg-slate-950/40 border border-white/[0.05] space-y-2">
                  <span className="text-[11px] font-mono uppercase text-slate-400 block tracking-wider">
                    Assessment Summary
                  </span>
                  <p className="text-sm font-light text-slate-200">
                    {latestResult.supervisor.summary}
                  </p>
                  <div className="flex justify-between items-center pt-2 border-t border-white/[0.04] text-xs">
                    <span className="text-slate-400">Confidence Score:</span>
                    <strong className="text-cyan-300 font-mono">
                      {(latestResult.supervisor.confidence * 100).toFixed(0)}%
                    </strong>
                  </div>
                </div>

                <div className="space-y-2">
                  <span className="text-[11px] font-mono uppercase text-slate-400 tracking-wider">
                    Evidence & Observations
                  </span>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div className="p-3 rounded-xl bg-slate-900/40 border border-white/[0.04]">
                      <span className="text-slate-500 block text-[10px]">Anomaly Detected</span>
                      <strong className={latestResult.supervisor.is_anomaly ? 'text-rose-300' : 'text-slate-300'}>
                        {latestResult.supervisor.is_anomaly ? 'Yes (Avvikelse)' : 'No (Normal)'}
                      </strong>
                    </div>
                    <div className="p-3 rounded-xl bg-slate-900/40 border border-white/[0.04]">
                      <span className="text-slate-500 block text-[10px]">Review Required</span>
                      <strong className={latestResult.supervisor.requires_review ? 'text-amber-300' : 'text-slate-300'}>
                        {latestResult.supervisor.requires_review ? 'Yes' : 'No'}
                      </strong>
                    </div>
                  </div>
                </div>

                {latestResult.supervisor.evidence?.length > 0 && (
                  <div className="p-3 rounded-xl bg-slate-900/30 border border-white/[0.04] text-xs space-y-1">
                    <span className="text-[10px] font-mono uppercase text-slate-500">Evidence Items:</span>
                    {latestResult.supervisor.evidence.map((ev, i) => (
                      <div key={i} className="text-slate-300 font-light">
                        • {ev.description}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* RESIDENT VOICE VIEW */}
            {selectedAgent === 'voice' && (
              <div className="space-y-4">
                {latestResult.voice_prompt ? (
                  <>
                    <div className="p-4 rounded-2xl bg-indigo-950/20 border border-indigo-500/20 space-y-1.5">
                      <span className="text-[10px] font-mono uppercase text-indigo-300 tracking-wider">
                        Prompt to Resident
                      </span>
                      <p className="text-sm font-light text-slate-100 italic">
                        "{latestResult.voice_prompt.text}"
                      </p>
                      <span className="text-[10px] text-indigo-400/80 block pt-1">
                        Conversation ID: {latestResult.voice_prompt.conversation_id}
                      </span>
                    </div>

                    {latestResult.resident_reply ? (
                    <div className="p-4 rounded-2xl bg-slate-950/40 border border-white/[0.05] space-y-1.5">
                      <span className="text-[10px] font-mono uppercase text-slate-400 tracking-wider">
                        Transcribed Resident Response
                      </span>
                      <p className="text-sm font-medium text-emerald-300">
                        "{latestResult.resident_reply?.transcript}"
                      </p>
                      <span className="text-[10px] text-slate-500 block">
                        Source: {latestResult.resident_reply?.transcript_source}
                      </span>
                    </div>
                    ) : (
                      <p className="py-4 text-sm text-slate-400">
                        {escalatedNoReply ? `No reply arrived. Guardian approved ${latestResult.triage?.action_type || 'follow-up'}.` : voiceExpired ? 'No reply arrived before the deadline. No action was dispatched.' : 'Triage requested a resident check-in. Waiting for the resident device to send a recorded reply.'}
                      </p>
                    )}

                    {latestResult.resident_reply?.acoustic_features && (
                      <div className="p-3 rounded-xl bg-slate-900/40 border border-white/[0.04] text-xs space-y-2">
                        <span className="text-[10px] font-mono uppercase text-slate-400">
                          Acoustic WAV Analysis:
                        </span>
                        <div className="grid grid-cols-2 gap-2 text-[11px] font-mono">
                          <span className="text-slate-400">
                            RMS: <strong className="text-slate-200">{latestResult.resident_reply.acoustic_features.rms_amplitude?.toFixed(3)}</strong>
                          </span>
                          <span className="text-slate-400">
                            Zero-Crossing: <strong className="text-slate-200">{latestResult.resident_reply.acoustic_features.zero_crossing_rate?.toFixed(3)}</strong>
                          </span>
                        </div>
                      </div>
                    )}
                  </>
                ) : (
                  <p className="text-sm text-slate-500 font-light py-8 text-center">
                    Resident voice interaction was bypassed because the Supervisor detected no anomaly.
                  </p>
                )}
              </div>
            )}

            {/* TRIAGE VIEW */}
            {selectedAgent === 'triage' && (
              <div className="space-y-4">
                {latestResult.triage ? (
                  <>
                    <div className="flex flex-wrap gap-2">
                      {triagePasses.map((proposal, index) => (
                        <span key={proposal.proposal_id || index} className="px-2.5 py-1 rounded border border-purple-500/20 bg-purple-950/20 text-[10px] font-mono text-purple-200">
                          {proposal.triage_stage === 'post_reply' ? 'Post-reply' : 'Initial'}: {proposal.action_type}
                        </span>
                      ))}
                    </div>
                    <div className="p-4 rounded-2xl bg-slate-950/40 border border-white/[0.05] space-y-2">
                      <div className="flex justify-between items-center">
                        <span className="text-[10px] font-mono uppercase text-slate-400 tracking-wider">
                          Action Type
                        </span>
                        <span className="text-xs font-mono font-bold text-purple-300">
                          {latestResult.triage.action_type}
                        </span>
                      </div>
                      <p className="text-sm font-light text-slate-200 pt-1">
                        {latestResult.triage.rationale}
                      </p>
                    </div>

                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div className="p-3 rounded-xl bg-slate-900/40 border border-white/[0.04]">
                        <span className="text-slate-500 block text-[10px]">Triage Confidence</span>
                        <strong className="text-purple-300 font-mono">
                          {(latestResult.triage.confidence * 100).toFixed(0)}%
                        </strong>
                      </div>
                      <div className="p-3 rounded-xl bg-slate-900/40 border border-white/[0.04]">
                        <span className="text-slate-500 block text-[10px]">Evidence References</span>
                        <strong className="text-slate-300 font-mono">
                          {latestResult.triage.evidence_refs?.length || 0} bound
                        </strong>
                      </div>
                    </div>
                  </>
                ) : (
                  <p className="text-sm text-slate-500 font-light py-8 text-center">
                    Triage was not invoked for this event.
                  </p>
                )}
              </div>
            )}

            {/* GUARDIAN VIEW */}
            {selectedAgent === 'guardian' && (
              <div className="space-y-4">
                {latestResult.guardian ? (
                  <>
                    <div
                      className={`p-4 rounded-2xl border space-y-2 ${
                        isApproved
                          ? 'bg-emerald-950/20 border-emerald-500/30'
                          : isHeld
                          ? 'bg-amber-950/20 border-amber-500/30'
                          : 'bg-rose-950/20 border-rose-500/30'
                      }`}
                    >
                      <div className="flex justify-between items-center">
                        <span className="text-[10px] font-mono uppercase tracking-wider text-slate-400">
                          Guardian Gate Decision
                        </span>
                        <span
                          className={`text-xs font-bold uppercase font-mono px-2 py-0.5 rounded ${
                            isApproved ? 'bg-emerald-500/20 text-emerald-300' : isHeld ? 'bg-amber-500/20 text-amber-300' : 'bg-rose-500/20 text-rose-300'
                          }`}
                        >
                          {latestResult.guardian.status}
                        </span>
                      </div>
                      <p className="text-sm font-light text-slate-200 pt-1">
                        {latestResult.guardian.rationale}
                      </p>
                    </div>

                    <div className="space-y-1.5">
                      <span className="text-[10px] font-mono uppercase text-slate-400 tracking-wider">
                        Evaluated Policy Reason Codes:
                      </span>
                      <div className="flex flex-wrap gap-1.5">
                        {latestResult.guardian.reason_codes?.map((rc, i) => (
                          <span
                            key={i}
                            className="px-2.5 py-1 rounded-lg text-xs font-mono bg-slate-900 border border-white/[0.08] text-slate-300"
                          >
                            {rc}
                          </span>
                        ))}
                      </div>
                    </div>
                  </>
                ) : (
                  <p className="text-sm text-slate-500 font-light py-8 text-center">
                    Guardian safety gate was bypassed.
                  </p>
                )}
              </div>
            )}

            {/* DISPATCHER VIEW */}
            {selectedAgent === 'dispatcher' && (
              <div className="space-y-4">
                {ticketId ? (
                  <div className="p-5 rounded-2xl bg-emerald-950/20 border border-emerald-500/30 space-y-3">
                    <div className="flex justify-between items-center">
                      <span className="text-[10px] font-mono uppercase tracking-wider text-emerald-400">
                        Care Registry Ticket Dispatched
                      </span>
                      <span className="font-mono text-xs text-white bg-emerald-500/20 px-2 py-0.5 rounded">
                        {ticketId}
                      </span>
                    </div>
                    <p className="text-xs text-slate-300 font-light leading-relaxed">
                      Dispatcher executed the human-review proposal into the in-memory dashboard store.
                    </p>
                    <button
                      onClick={onNavigateTickets}
                      className="w-full py-2.5 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-semibold text-xs tracking-wider uppercase transition-all shadow-lg shadow-emerald-950/40"
                    >
                      Open in Caregiver Tickets ➔
                    </button>
                  </div>
                ) : (
                  <div className="p-8 text-center space-y-2">
                    <p className="text-sm text-slate-500 font-light">
                      No ticket or human review action was dispatched.
                    </p>
                    <span className="text-xs text-slate-600 block">
                      Requires Guardian APPROVE status.
                    </span>
                  </div>
                )}
              </div>
            )}

            {/* Toggle Raw JSON Payload */}
            <div className="pt-2 border-t border-white/[0.06]">
              <button
                onClick={() => setShowRawJson(!showRawJson)}
                className="text-xs text-slate-500 hover:text-slate-300 font-mono transition-colors"
              >
                {showRawJson ? 'Hide raw audit JSON ▲' : 'Inspect raw audit JSON ▼'}
              </button>
              {showRawJson && (
                <pre className="mt-2 p-3 rounded-xl bg-black/60 border border-white/[0.06] text-[10px] font-mono text-cyan-300/80 overflow-x-auto max-h-48">
                  {JSON.stringify(selectedAgent === 'voice'
                    ? { prompt: latestResult.voice_prompt, reply: latestResult.resident_reply }
                    : latestResult[selectedAgent] ?? null, null, 2)}
                </pre>
              )}
            </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function TicketsView({
  tickets,
  filter,
  setFilter,
  search,
  setSearch,
  onUpdateTicket,
  onSubmitFeedback,
  feedbackDrafts,
  setFeedbackDrafts,
  notesState,
  setNotesState,
}) {
  const filteredTickets = tickets.filter((t) => {
    if (filter !== 'all' && t.status !== filter) return false
    if (search.trim()) {
      const q = search.toLowerCase()
      return (
        t.record_id.toLowerCase().includes(q) ||
        t.subject_id.toLowerCase().includes(q) ||
        t.summary.toLowerCase().includes(q)
      )
    }
    return true
  })

  const awaitingCount = tickets.filter((t) => t.status === 'awaiting_human_review').length
  const inProgressCount = tickets.filter((t) => t.status === 'in_progress').length
  const resolvedCount = tickets.filter((t) => t.status === 'resolved').length

  return (
    <div className="space-y-6">
      {/* Metric Cards */}
      <div className="grid grid-cols-4 gap-4">
        <div className="p-5 rounded-2xl bg-[#0e1626] border border-slate-800">
          <span className="text-xs font-semibold text-slate-400">Total Created</span>
          <p className="text-2xl font-bold text-white mt-1">{tickets.length}</p>
        </div>
        <div className="p-5 rounded-2xl bg-[#0e1626] border border-slate-800">
          <span className="text-xs font-semibold text-amber-400">Awaiting Review</span>
          <p className="text-2xl font-bold text-amber-400 mt-1">{awaitingCount}</p>
        </div>
        <div className="p-5 rounded-2xl bg-[#0e1626] border border-slate-800">
          <span className="text-xs font-semibold text-cyan-400">In Progress</span>
          <p className="text-2xl font-bold text-cyan-400 mt-1">{inProgressCount}</p>
        </div>
        <div className="p-5 rounded-2xl bg-[#0e1626] border border-slate-800">
          <span className="text-xs font-semibold text-emerald-400">Resolved</span>
          <p className="text-2xl font-bold text-emerald-400 mt-1">{resolvedCount}</p>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div className="flex items-center justify-between gap-4 p-4 rounded-xl bg-[#0e1626] border border-slate-800">
        <div className="flex gap-2">
          {[
            { id: 'all', label: 'All Tickets' },
            { id: 'awaiting_human_review', label: 'Awaiting Review' },
            { id: 'in_progress', label: 'In Progress' },
            { id: 'resolved', label: 'Resolved' },
          ].map((f) => (
            <button
              key={f.id}
              onClick={() => setFilter(f.id)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                filter === f.id
                  ? 'bg-indigo-600 text-white shadow-md shadow-indigo-950/40'
                  : 'bg-slate-800/60 text-slate-400 hover:text-slate-200'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>

        <div className="relative w-64">
          <Search className="w-3.5 h-3.5 text-slate-500 absolute left-3 top-3" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search resident or ticket..."
            className="w-full pl-9 pr-3 py-1.5 rounded-lg bg-slate-900 border border-slate-700/80 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-cyan-500"
          />
        </div>
      </div>

      {/* Ticket Cards List */}
      {filteredTickets.length === 0 ? (
        <div className="p-12 text-center text-slate-500 rounded-2xl bg-[#0e1626] border border-slate-800">
          <TicketIcon className="w-8 h-8 mx-auto mb-2 opacity-50" />
          <p className="text-sm">No tickets match the selected criteria.</p>
        </div>
      ) : (
        <div className="space-y-4">
          {filteredTickets.map((ticket) => {
            const currentNotes =
              notesState[ticket.record_id] !== undefined
                ? notesState[ticket.record_id]
                : ticket.notes

            return (
              <div
                key={ticket.record_id}
                className="p-6 rounded-2xl bg-[#0e1626] border border-slate-800 hover:border-slate-700 transition-all space-y-4"
              >
                <div className="flex items-start justify-between">
                  <div className="space-y-1">
                    <div className="flex items-center gap-3">
                      <span className="font-mono text-sm font-bold text-cyan-400">
                        {ticket.record_id}
                      </span>
                      <span className="px-2 py-0.5 rounded border border-slate-700 text-[11px] uppercase text-slate-300">
                        {{
                          request_human_review: 'Human review',
                          maintenance_ticket: 'Maintenance',
                          remote_followup: 'Remote follow-up',
                          home_visit: 'Home visit',
                          urgent_visit: 'Urgent visit',
                        }[ticket.action_type] || ticket.action_type}
                      </span>
                      <span className="text-[10px] text-slate-500 font-mono">
                        {ticket.triage_stage === 'post_reply' ? 'Post-reply pass' : 'Initial pass'}
                      </span>
                      <span className={`px-2.5 py-0.5 rounded-full text-[11px] font-bold ${
                        ticket.priority === 'High' || ticket.priority === 'Urgent'
                          ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30'
                          : 'bg-slate-700/50 text-slate-300'
                      }`}>
                        {ticket.priority} Priority
                      </span>
                      <span className="text-xs text-slate-500">
                        Resident: <strong className="text-slate-300">{ticket.subject_id}</strong>
                      </span>
                    </div>
                    <p className="text-sm font-medium text-slate-200 mt-1">{ticket.summary}</p>
                    <span className="text-xs text-slate-500 block">Created: {ticket.created_at}</span>
                  </div>

                  {/* Status Dropdown */}
                  <div className="flex items-center gap-3">
                    <select
                      value={ticket.status}
                      onChange={(e) => onUpdateTicket(ticket.record_id, e.target.value)}
                      className={`px-3 py-1.5 rounded-xl text-xs font-semibold focus:outline-none border ${
                        ticket.status === 'awaiting_human_review'
                          ? 'bg-amber-950/40 border-amber-500/50 text-amber-300'
                          : ticket.status === 'maintenance_required'
                          ? 'bg-amber-950/40 border-amber-500/50 text-amber-300'
                          : ticket.status === 'in_progress'
                          ? 'bg-cyan-950/40 border-cyan-500/50 text-cyan-300'
                          : 'bg-emerald-950/40 border-emerald-500/50 text-emerald-300'
                      }`}
                    >
                      <option value="maintenance_required">🛠 Maintenance Required</option>
                      <option value="awaiting_human_review">🔴 Awaiting Review</option>
                      <option value="in_progress">🟡 In Progress</option>
                      <option value="resolved">🟢 Resolved</option>
                    </select>
                  </div>
                </div>

                {/* Telemetry Details */}
                <div className="grid grid-cols-4 gap-3 p-3 rounded-xl bg-[#090d16] border border-slate-800/80 text-xs">
                  <div>
                    <span className="text-slate-500 block">Temperature:</span>
                    <strong className="text-white">{ticket.observations?.temperature_c ?? 'Missing'}°C</strong>
                  </div>
                  <div>
                    <span className="text-slate-500 block">Humidity:</span>
                    <strong className="text-white">{ticket.observations?.humidity_pct ?? 'Missing'}%</strong>
                  </div>
                  <div>
                    <span className="text-slate-500 block">Door State:</span>
                    <strong className="text-white capitalize">{ticket.observations?.door_state ?? 'Missing'}</strong>
                  </div>
                  <div>
                    <span className="text-slate-500 block">Motion Detected:</span>
                    <strong className="text-white">{ticket.observations?.motion_detected ? 'Yes' : 'No'}</strong>
                  </div>
                </div>

                {/* Caregiver Notes Input */}
                <div className="flex gap-3 items-center pt-1">
                  <input
                    type="text"
                    value={currentNotes}
                    onChange={(e) =>
                      setNotesState({ ...notesState, [ticket.record_id]: e.target.value })
                    }
                    placeholder="Caregiver observations or resolution notes..."
                    className="flex-1 px-3 py-2 rounded-xl bg-slate-900 border border-slate-700/80 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-cyan-500"
                  />
                  <button
                    onClick={() => onUpdateTicket(ticket.record_id, null, currentNotes)}
                    className="px-3 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-medium text-slate-200 transition-colors"
                  >
                    Save Notes
                  </button>
                </div>

                {ticket.feedback ? (
                  <p className="text-xs text-slate-400 border-t border-slate-800 pt-3">
                    Caseworker feedback: <strong className="text-slate-200">{ticket.feedback.label.replaceAll('_', ' ')}</strong>
                    {ticket.feedback.note ? ` · ${ticket.feedback.note}` : ''}
                  </p>
                ) : ticket.status === 'resolved' ? (
                  <div className="grid gap-3 border-t border-slate-800 pt-3 sm:grid-cols-[180px_1fr_auto]">
                    <select
                      aria-label="Caseworker outcome"
                      value={feedbackDrafts[ticket.record_id]?.label || ''}
                      onChange={(event) => setFeedbackDrafts({
                        ...feedbackDrafts,
                        [ticket.record_id]: {
                          ...feedbackDrafts[ticket.record_id],
                          label: event.target.value,
                        },
                      })}
                      className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-200"
                    >
                      <option value="">Select outcome</option>
                      <option value="false_alarm">False alarm</option>
                      <option value="justified">Justified</option>
                      <option value="sensor_fault">Sensor fault</option>
                    </select>
                    <input
                      aria-label="Caseworker feedback note"
                      value={feedbackDrafts[ticket.record_id]?.note || ''}
                      onChange={(event) => setFeedbackDrafts({
                        ...feedbackDrafts,
                        [ticket.record_id]: {
                          ...feedbackDrafts[ticket.record_id],
                          note: event.target.value,
                        },
                      })}
                      placeholder="Optional caseworker note"
                      className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-200 placeholder:text-slate-500"
                    />
                    <button
                      disabled={!feedbackDrafts[ticket.record_id]?.label}
                      onClick={() => onSubmitFeedback(ticket.record_id)}
                      className="rounded-lg border border-cyan-500/30 bg-cyan-950/30 px-3 py-2 text-xs font-semibold text-cyan-200 disabled:opacity-40"
                    >
                      Save feedback
                    </button>
                  </div>
                ) : (
                  <p className="border-t border-slate-800 pt-3 text-[11px] text-slate-500">
                    Resolve this ticket to record caseworker feedback.
                  </p>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function DismissedMetricsView({
  events,
  metrics,
  baselineSuggestions,
  onApplyBaselineSuggestion,
}) {
  const [dismissedFilter, setDismissedFilter] = useState('critical')
  const dismissed = events.filter((event) => event.outcome === 'dismissed')
  const isCriticalDismissal = (event) => (
    event.supervisor?.severity === 'critical'
    || (event.guardian?.reason_codes || []).some((reason) => reason.startsWith('critical_'))
  )
  const visibleDismissals = dismissed.filter((event) => (
    dismissedFilter !== 'critical' || isCriticalDismissal(event)
  ))

  const metricItems = [
    ['Events', metrics?.events_processed ?? 0, 'text-slate-100'],
    ['Dropped by debounce', metrics?.dropped ?? 0, 'text-amber-300'],
    ['Dismissed', metrics?.dismissed ?? 0, 'text-slate-300'],
    ['Dismissed · critical review', metrics?.dismissed_critical_review ?? 0, 'text-rose-300'],
    ['Remote follow-ups', metrics?.remote_followups ?? 0, 'text-cyan-300'],
    ['Home visits', metrics?.home_visits ?? 0, 'text-amber-300'],
    ['Urgent visits', metrics?.urgent_visits ?? 0, 'text-rose-300'],
    ['Est. visits avoided', metrics?.estimated_visits_avoided ?? 0, 'text-emerald-300'],
    ['No-reply escalations', metrics?.escalated_no_reply ?? 0, 'text-orange-300'],
    ['Sensor faults', metrics?.sensor_faults ?? 0, 'text-yellow-300'],
    ['False alarms reported', metrics?.false_alarm_feedback ?? 0, 'text-slate-100'],
    ['False-alarm rate', `${Math.round((metrics?.false_alarm_rate ?? 0) * 100)}%`, 'text-amber-300'],
  ]

  return (
    <div className="mx-auto max-w-7xl space-y-8">
      <section className="space-y-4">
        <div>
          <h3 className="text-sm font-semibold text-white">Dismissed events</h3>
          <p className="mt-1 text-xs text-slate-500">Critical dismissals remain visible for caseworker review.</p>
        </div>
        <div className="flex gap-2" role="group" aria-label="Dismissed event filter">
          {[
            { id: 'critical', label: 'Critical · review' },
            { id: 'all', label: 'All dismissals' },
          ].map((filter) => (
            <button
              key={filter.id}
              onClick={() => setDismissedFilter(filter.id)}
              aria-pressed={dismissedFilter === filter.id}
              className={`rounded-lg border px-3 py-2 text-xs font-medium ${dismissedFilter === filter.id ? 'border-amber-500/40 bg-amber-950/30 text-amber-200' : 'border-slate-700 bg-slate-900 text-slate-400'}`}
            >
              {filter.label}
            </button>
          ))}
        </div>
        {visibleDismissals.length === 0 ? (
          <p className="rounded-xl border border-slate-800 bg-[#0e1626] p-6 text-sm text-slate-500">
            No dismissed events match this filter.
          </p>
        ) : (
          <div className="divide-y divide-slate-800 border-y border-slate-800">
            {visibleDismissals.map((event) => (
              <article key={event.event_id} className="grid gap-2 py-4 sm:grid-cols-[1fr_auto]">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs text-slate-300">{event.event_id}</span>
                    {isCriticalDismissal(event) && (
                      <span className="rounded border border-rose-500/30 bg-rose-950/30 px-2 py-0.5 text-[10px] font-semibold uppercase text-rose-300">
                        Critical · review
                      </span>
                    )}
                    <span className="text-[10px] text-slate-500">{event.supervisor?.severity || 'severity unavailable'}</span>
                  </div>
                  <p className="mt-2 text-sm text-slate-200">{event.triage?.rationale || event.supervisor?.summary || 'Dismissed by Triage.'}</p>
                  <p className="mt-1 text-[11px] text-slate-500">{event.triage?.triage_stage || 'unknown'} pass · {event.triage?.action_type || 'dismiss'}</p>
                </div>
                <span className="text-[11px] text-slate-500">{event.sensor_data?.occurred_at || ''}</span>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-4 border-t border-slate-800 pt-6">
        <div>
          <h3 className="text-sm font-semibold text-white">Session metrics</h3>
          <p className="mt-1 text-xs text-slate-500">In-memory counts reset when the backend restarts. Visit avoidance is an estimate based on event outcomes; false-alarm rate uses submitted ticket feedback only.</p>
        </div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
          {metricItems.map(([label, value, color]) => (
            <div key={label} className="border-l border-slate-700 pl-3 py-1">
              <span className="block text-[10px] uppercase text-slate-500">{label}</span>
              <strong className={`mt-1 block text-xl font-semibold ${color}`}>{value}</strong>
            </div>
          ))}
        </div>
      </section>

      <section className="space-y-4 border-t border-slate-800 pt-6">
        <div>
          <h3 className="text-sm font-semibold text-white">Suggested baseline changes</h3>
          <p className="mt-1 text-xs text-slate-500">Repeated false-alarm feedback creates suggestions only. A human must apply each change.</p>
        </div>
        {baselineSuggestions.length === 0 ? (
          <p className="text-sm text-slate-500">No baseline suggestions yet.</p>
        ) : (
          <div className="divide-y divide-slate-800 border-y border-slate-800">
            {baselineSuggestions.map((suggestion) => (
              <div key={`${suggestion.subject_id}:${suggestion.field}`} className="flex flex-wrap items-center justify-between gap-4 py-4">
                <div>
                  <p className="text-sm font-medium text-slate-200">{suggestion.field} · {suggestion.subject_id}</p>
                  <p className="mt-1 text-xs text-slate-400">
                    {String(suggestion.current_value)} → {String(suggestion.suggested_value)} · {suggestion.false_alarm_count} false-alarm reports
                  </p>
                </div>
                {suggestion.status === 'pending_review' ? (
                  <button
                    onClick={() => onApplyBaselineSuggestion(suggestion)}
                    className="rounded-lg border border-amber-500/30 bg-amber-950/20 px-3 py-2 text-xs font-semibold text-amber-200"
                  >
                    Apply suggestion
                  </button>
                ) : (
                  <span className="text-xs text-emerald-300">Applied</span>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

function BuddyView({ buddies, requests, subjectId, onRequest }) {
  const residentRequests = requests.filter((request) => request.subject_id === subjectId)

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <section className="border-b border-slate-800 pb-5">
        <h3 className="text-sm font-semibold text-white">Friendly conversation</h3>
        <p className="mt-1 text-xs text-slate-400">
          Synthetic demo companions for a friendly chat. This is not therapy or crisis support. Requests are recorded locally; nobody is contacted.
        </p>
        <p className="mt-2 text-[11px] text-slate-500">Resident: {subjectId}</p>
      </section>

      <section className="divide-y divide-slate-800 border-y border-slate-800">
        {buddies.map((buddy) => (
          <article key={buddy.buddy_id} className="flex flex-wrap items-center justify-between gap-4 py-5">
            <div className="flex min-w-0 items-start gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-cyan-500/20 bg-cyan-950/30 text-cyan-200">
                <MessageCircle className="h-4 w-4" />
              </div>
              <div>
                <h4 className="text-sm font-medium text-slate-100">{buddy.name}</h4>
                <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-2 text-xs">
                  <div className="flex gap-1.5">
                    <dt className="text-slate-500">Relationship:</dt>
                    <dd className="text-slate-200">{buddy.relationship || 'Not specified'}</dd>
                  </div>
                  <div className="flex gap-1.5">
                    <dt className="text-slate-500">Task NDA:</dt>
                    <dd className={buddy.nda_signed === true ? 'text-emerald-300' : 'text-amber-300'}>
                      {buddy.nda_signed === true ? 'Signed (demo)' : 'Not signed (demo)'}
                    </dd>
                  </div>
                </dl>
                <p className="mt-1 text-xs text-slate-400">{buddy.description}</p>
                <span className="mt-2 inline-block text-[10px] uppercase text-slate-500">
                  Synthetic · {buddy.language}
                </span>
              </div>
            </div>
            <button
              onClick={() => onRequest(buddy.buddy_id)}
              className="rounded-lg border border-cyan-500/30 bg-cyan-950/20 px-3 py-2 text-xs font-semibold text-cyan-200 hover:bg-cyan-900/30"
            >
              Request a chat
            </button>
          </article>
        ))}
      </section>

      <section className="space-y-3">
        <div>
          <h3 className="text-sm font-semibold text-white">Recent demo requests</h3>
          <p className="mt-1 text-xs text-slate-500">These entries are local test records only.</p>
        </div>
        {residentRequests.length === 0 ? (
          <p className="text-xs text-slate-500">No requests recorded for this resident.</p>
        ) : (
          <div className="divide-y divide-slate-800 border-y border-slate-800">
            {residentRequests.slice().reverse().map((request) => (
              <div key={request.request_id} className="flex flex-wrap justify-between gap-2 py-3 text-xs">
                <span className="text-slate-300">{request.buddy_name} · {request.status.replaceAll('_', ' ')}</span>
                <span className="text-slate-500">{request.created_at}</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

function SimulatorView({
  scenario,
  setScenario,
  temp,
  setTemp,
  humidity,
  setHumidity,
  doorState,
  setDoorState,
  motionDetected,
  setMotionDetected,
  residentReply,
  setResidentReply,
  execMode,
  setExecMode,
  onExecute,
  onConsumeOne,
  loading,
  brokerConfigured,
}) {
  return (
    <div className="space-y-8 max-w-4xl">
      {/* Telemetry Sliders Card */}
      <div className="p-6 rounded-2xl bg-[#0e1626] border border-slate-800 space-y-6 shadow-xl">
        <h3 className="font-semibold text-sm text-white flex items-center gap-2">
          <Sliders className="w-4 h-4 text-cyan-400" />
          Sensor Observation Controls
        </h3>

        <div className="grid grid-cols-2 gap-6">
          {/* Temperature */}
          <div className="space-y-2">
            <div className="flex justify-between text-xs">
              <span className="text-slate-400 flex items-center gap-1.5">
                <Thermometer className="w-3.5 h-3.5 text-rose-400" /> Temperature
              </span>
              <span className="font-mono font-bold text-white">{temp}°C</span>
            </div>
            <input
              type="range"
              min="-30"
              max="50"
              step="0.5"
              value={temp}
              onChange={(e) => setTemp(parseFloat(e.target.value))}
              className="w-full accent-cyan-500"
            />
            <div className="flex justify-between text-[10px] text-slate-500 font-mono">
              <span>-30°C</span>
              <span>18°C (Baseline)</span>
              <span>50°C</span>
            </div>
          </div>

          {/* Humidity */}
          <div className="space-y-2">
            <div className="flex justify-between text-xs">
              <span className="text-slate-400 flex items-center gap-1.5">
                <Droplets className="w-3.5 h-3.5 text-cyan-400" /> Humidity
              </span>
              <span className="font-mono font-bold text-white">{humidity}%</span>
            </div>
            <input
              type="range"
              min="0"
              max="100"
              step="1"
              value={humidity}
              onChange={(e) => setHumidity(parseFloat(e.target.value))}
              className="w-full accent-cyan-500"
            />
            <div className="flex justify-between text-[10px] text-slate-500 font-mono">
              <span>0%</span>
              <span>45% (Baseline)</span>
              <span>100%</span>
            </div>
          </div>

          {/* Door State */}
          <div className="space-y-2">
            <label className="text-xs text-slate-400 flex items-center gap-1.5">
              <DoorOpen className="w-3.5 h-3.5 text-amber-400" /> Door State
            </label>
            <div className="flex gap-2">
              {['closed', 'open'].map((st) => (
                <button
                  key={st}
                  type="button"
                  onClick={() => setDoorState(st)}
                  className={`flex-1 py-2 rounded-xl text-xs font-semibold capitalize transition-all ${
                    doorState === st
                      ? 'bg-cyan-500 text-slate-950 shadow-md shadow-cyan-950/40'
                      : 'bg-slate-800/80 text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {st}
                </button>
              ))}
            </div>
          </div>

          {/* Motion State */}
          <div className="space-y-2">
            <label className="text-xs text-slate-400 flex items-center gap-1.5">
              <Footprints className="w-3.5 h-3.5 text-emerald-400" /> Motion Detected
            </label>
            <div className="flex gap-2">
              {[true, false].map((val) => (
                <button
                  key={String(val)}
                  type="button"
                  onClick={() => setMotionDetected(val)}
                  className={`flex-1 py-2 rounded-xl text-xs font-semibold transition-all ${
                    motionDetected === val
                      ? 'bg-cyan-500 text-slate-950 shadow-md shadow-cyan-950/40'
                      : 'bg-slate-800/80 text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {val ? 'Yes (Detected)' : 'No (None)'}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Resident Speech Reply Input */}
        <div className="space-y-2 pt-2 border-t border-slate-800">
          <label className="text-xs text-slate-400 flex items-center gap-1.5">
            <Mic className="w-3.5 h-3.5 text-indigo-400" /> Simulated Resident Voice Response
          </label>
          <input
            type="text"
            value={residentReply}
            onChange={(e) => setResidentReply(e.target.value)}
            placeholder="Resident voice response text..."
            className="w-full px-4 py-2.5 rounded-xl bg-slate-900 border border-slate-700/80 text-sm text-slate-200 focus:outline-none focus:border-cyan-500"
          />
        </div>

        {/* Execution Mode Selector */}
        <div className="space-y-2 pt-2 border-t border-slate-800">
          <label className="text-xs text-slate-400 block">Execution Target:</label>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => setExecMode('direct')}
              className={`flex-1 p-3 rounded-xl border text-left transition-all ${
                execMode === 'direct'
                  ? 'bg-cyan-950/30 border-cyan-500/60 text-cyan-200 shadow-lg shadow-cyan-950/30'
                  : 'bg-slate-900/60 border-slate-800 text-slate-400 hover:text-slate-200'
              }`}
            >
              <span className="font-bold text-xs block text-white">⚡ Direct In-Memory Workflow</span>
              <span className="text-[11px] opacity-80 block mt-0.5">Executes all 4 agents immediately and shows result</span>
            </button>

            <button
              type="button"
              onClick={() => setExecMode('lavinmq')}
              disabled={!brokerConfigured}
              className={`flex-1 p-3 rounded-xl border text-left transition-all ${
                execMode === 'lavinmq'
                  ? 'bg-indigo-950/30 border-indigo-500/60 text-indigo-200 shadow-lg shadow-indigo-950/30'
                  : !brokerConfigured
                  ? 'bg-slate-900/20 border-slate-800/40 text-slate-600 cursor-not-allowed'
                  : 'bg-slate-900/60 border-slate-800 text-slate-400 hover:text-slate-200'
              }`}
            >
              <span className="font-bold text-xs block text-white">☁️ Publish via LavinMQ Broker</span>
              <span className="text-[11px] opacity-80 block mt-0.5">
                {brokerConfigured ? 'Pushes event to CloudAMQP queue' : 'Configure LAVINMQ_URL in .env to enable'}
              </span>
            </button>
          </div>
        </div>

        {/* Submit Button */}
        <div className="pt-2 flex justify-end">
          <button
            onClick={onExecute}
            disabled={loading}
            className="px-6 py-3 rounded-xl bg-gradient-to-r from-cyan-500 to-indigo-600 hover:brightness-110 font-bold text-sm text-white shadow-lg shadow-cyan-950/50 flex items-center gap-2"
          >
            <Play className="w-4 h-4" />
            <span>{execMode === 'direct' ? 'Execute Workflow' : 'Publish to LavinMQ'}</span>
          </button>
        </div>
      </div>

      {/* CloudAMQP Consumer Section */}
      <div className="p-6 rounded-2xl bg-[#0e1626] border border-slate-800 shadow-xl flex items-center justify-between">
        <div>
          <h4 className="font-bold text-sm text-white">Pull Message from LavinMQ Queue</h4>
          <p className="text-xs text-slate-400 mt-0.5">
            Consume and evaluate one waiting sensor message through the 4-agent workflow.
          </p>
        </div>
        <button
          onClick={onConsumeOne}
          disabled={loading || !brokerConfigured}
          className="px-4 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-xs font-semibold flex items-center gap-2 disabled:opacity-40"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          <span>Consume 1 Message</span>
        </button>
      </div>
    </div>
  )
}

function HistoryView({ events }) {
  if (events.length === 0) {
    return (
      <div className="p-12 text-center text-slate-500 rounded-2xl bg-[#0e1626] border border-slate-800">
        <History className="w-8 h-8 mx-auto mb-2 opacity-50" />
        <p className="text-sm">No sensor events recorded in this session yet.</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {events.map((evt, idx) => (
        <div key={idx} className="p-5 rounded-2xl bg-[#0e1626] border border-slate-800 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="font-mono text-xs font-bold text-cyan-400">{evt.event_id}</span>
              <span className={`px-2.5 py-0.5 rounded-full text-xs font-bold uppercase ${
                evt.outcome === 'dispatched'
                  ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                  : evt.outcome === 'no_action'
                  ? 'bg-slate-700/50 text-slate-300'
                  : 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
              }`}>
                {evt.outcome}
              </span>
            </div>
            {evt.dashboard_record_id && (
              <span className="text-xs text-emerald-400 font-mono">
                Ticket: {evt.dashboard_record_id}
              </span>
            )}
          </div>
          <pre className="p-3 rounded-xl bg-[#090d16] text-[11px] font-mono text-slate-400 overflow-x-auto max-h-48">
            {JSON.stringify(evt, null, 2)}
          </pre>
        </div>
      ))}
    </div>
  )
}
