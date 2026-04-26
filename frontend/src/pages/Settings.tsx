import React, { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getSettings, updateSettings } from '../lib/api'
import { Card, Button, Input, Loading, ErrorState } from '../components/ui'
import { PageHeader } from '../components/layout/Sidebar'

export default function SettingsPage() {
  const qc = useQueryClient()
  const [form, setForm] = useState<any>({})
  const [saved, setSaved] = useState(false)

  const { data: settings, isLoading, error } = useQuery({ queryKey: ['settings'], queryFn: getSettings })

  useEffect(() => {
    if (settings) setForm({ ...settings })
  }, [settings])

  const updateMut = useMutation({
    mutationFn: updateSettings,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings'] })
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    },
  })

  if (isLoading) return <div style={{ padding: '2rem' }}><Loading /></div>
  if (error) return <div style={{ padding: '2rem' }}><ErrorState message="Failed to load settings." /></div>

  const set = (k: string) => (e: React.ChangeEvent<any>) => {
    const val = e.target.type === 'checkbox' ? e.target.checked : e.target.type === 'number' ? Number(e.target.value) : e.target.value
    setForm((f: any) => ({ ...f, [k]: val }))
  }

  const inputStyle: React.CSSProperties = {
    background: '#0f1117', border: '1px solid #2d3048', borderRadius: 8,
    padding: '8px 12px', color: '#e4e4f0', fontSize: 14, outline: 'none', width: '100%',
  }

  const Section = ({ title, children }: { title: string; children: React.ReactNode }) => (
    <Card style={{ marginBottom: 20 }}>
      <h3 style={{ fontWeight: 700, marginBottom: 16, fontSize: 15, borderBottom: '1px solid #2d3048', paddingBottom: 10 }}>{title}</h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>{children}</div>
    </Card>
  )

  const Row = ({ label, desc, children }: { label: string; desc?: string; children: React.ReactNode }) => (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 220px', gap: 16, alignItems: 'center' }}>
      <div>
        <div style={{ fontSize: 14, color: '#e4e4f0', marginBottom: 2 }}>{label}</div>
        {desc && <div style={{ fontSize: 12, color: '#8b8fa8' }}>{desc}</div>}
      </div>
      <div>{children}</div>
    </div>
  )

  return (
    <div style={{ padding: '2rem' }}>
      <PageHeader
        title="Settings"
        action={
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            {saved && <span style={{ color: '#22c55e', fontSize: 14 }}>✓ Saved!</span>}
            <Button onClick={() => updateMut.mutate(form)} loading={updateMut.isPending}>Save Settings</Button>
          </div>
        }
      />

      <Section title="⏱ Scraping Defaults">
        <Row label="Default Scan Interval" desc="How often to scan each competitor (minutes)">
          <input type="number" min={5} value={form.default_scan_interval_minutes || 60} onChange={set('default_scan_interval_minutes')} style={inputStyle} />
        </Row>
        <Row label="Max Pages Per Scan" desc="Maximum pages to paginate per listing URL">
          <input type="number" min={1} max={50} value={form.default_max_pages || 5} onChange={set('default_max_pages')} style={inputStyle} />
        </Row>
        <Row label="Delay Between Pages (seconds)" desc="Wait time between paginated requests">
          <input type="number" min={0.5} step={0.5} value={form.default_page_delay_seconds || 2} onChange={set('default_page_delay_seconds')} style={inputStyle} />
        </Row>
        <Row label="User Agent" desc="Browser user agent string sent with requests">
          <input type="text" value={form.user_agent || ''} onChange={set('user_agent')} style={inputStyle} />
        </Row>
      </Section>

      <Section title="🔔 Discord Notifications">
        <Row label="Enable Discord Notifications" desc="Send alerts to Discord webhooks">
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
            <input type="checkbox" checked={form.discord_notifications_enabled ?? true} onChange={set('discord_notifications_enabled')} style={{ width: 18, height: 18 }} />
            <span style={{ color: '#e4e4f0', fontSize: 14 }}>Enabled</span>
          </label>
        </Row>
        <Row label="Min Price Change Amount" desc="Only notify if price changes by this amount or more">
          <input type="number" min={0} step={0.01} value={form.min_price_change_amount || 0.01} onChange={set('min_price_change_amount')} style={inputStyle} />
        </Row>
        <Row label="Min Price Change %" desc="Only notify if price changes by this percentage or more">
          <input type="number" min={0} step={0.1} value={form.min_price_change_percentage || 0.1} onChange={set('min_price_change_percentage')} style={inputStyle} />
        </Row>
      </Section>

      <Section title="📊 Daily Summary">
        <Row label="Enable Daily Summary" desc="Send a daily recap to Discord">
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
            <input type="checkbox" checked={form.daily_summary_enabled ?? true} onChange={set('daily_summary_enabled')} style={{ width: 18, height: 18 }} />
            <span style={{ color: '#e4e4f0', fontSize: 14 }}>Enabled</span>
          </label>
        </Row>
        <Row label="Summary Time" desc="What time to send the daily summary (24h format, e.g. 08:00)">
          <input type="text" pattern="\d{2}:\d{2}" value={form.daily_summary_time || '08:00'} onChange={set('daily_summary_time')} style={inputStyle} />
        </Row>
      </Section>

      <Section title="🔇 Filtering">
        <Row label="Ignore Keywords" desc="Comma-separated keywords — products matching these won't be tracked">
          <input type="text" value={form.ignore_keywords || ''} onChange={set('ignore_keywords')} placeholder="sample, test, demo" style={inputStyle} />
        </Row>
      </Section>
    </div>
  )
}
