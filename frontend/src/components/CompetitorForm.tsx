import React, { useState, useEffect } from 'react'
import { Modal, Button, Input, Select, Textarea } from '../ui'

const DEFAULT_SELECTOR = JSON.stringify({
  "product_card": ".product-card",
  "title": ".product-title",
  "price": ".price",
  "url": "a.product-link",
  "image": "img",
  "stock": ".stock-status",
  "pagination_next": "a.next"
}, null, 2)

const DEFAULT_FORM = {
  name: '', base_url: '', category: '', active: true,
  scan_frequency_minutes: 60, scrape_type: 'generic_selector',
  listing_urls_text: '', selector_config_text: DEFAULT_SELECTOR,
  discord_webhook_url: '', notes: '',
}

export default function CompetitorForm({
  open, onClose, onSubmit, initial, loading,
}: {
  open: boolean; onClose: () => void;
  onSubmit: (data: any) => void;
  initial?: any; loading?: boolean;
}) {
  const [form, setForm] = useState(DEFAULT_FORM)
  const [error, setError] = useState('')

  useEffect(() => {
    if (initial) {
      setForm({
        name: initial.name || '',
        base_url: initial.base_url || '',
        category: initial.category || '',
        active: initial.active ?? true,
        scan_frequency_minutes: initial.scan_frequency_minutes || 60,
        scrape_type: initial.scrape_type || 'generic_selector',
        listing_urls_text: (initial.listing_urls || []).join('\n'),
        selector_config_text: JSON.stringify(initial.selector_config || {}, null, 2),
        discord_webhook_url: initial.discord_webhook_url || '',
        notes: initial.notes || '',
      })
    } else {
      setForm(DEFAULT_FORM)
    }
    setError('')
  }, [initial, open])

  const set = (k: string) => (e: React.ChangeEvent<any>) =>
    setForm(f => ({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }))

  const submit = () => {
    setError('')
    if (!form.name.trim() || !form.base_url.trim()) {
      setError('Name and Base URL are required.')
      return
    }
    let selector_config = {}
    try { selector_config = JSON.parse(form.selector_config_text) } catch {
      setError('Selector config must be valid JSON.')
      return
    }
    const listing_urls = form.listing_urls_text.split('\n').map(s => s.trim()).filter(Boolean)
    onSubmit({
      name: form.name.trim(),
      base_url: form.base_url.trim(),
      category: form.category.trim() || null,
      active: form.active,
      scan_frequency_minutes: Number(form.scan_frequency_minutes),
      scrape_type: form.scrape_type,
      listing_urls,
      selector_config,
      discord_webhook_url: form.discord_webhook_url.trim() || null,
      notes: form.notes.trim() || null,
    })
  }

  const fieldStyle = { display: 'flex', flexDirection: 'column' as const, gap: 5 }
  const labelStyle = { fontSize: 13, color: '#8b8fa8' }

  return (
    <Modal open={open} onClose={onClose} title={initial ? 'Edit Competitor' : 'Add Competitor'} width={660}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {error && <div style={{ background: '#ef444422', border: '1px solid #ef4444', borderRadius: 8, padding: '10px 14px', color: '#ef4444', fontSize: 14 }}>{error}</div>}

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          <Input label="Name *" value={form.name} onChange={set('name')} placeholder="e.g. Nike Store" />
          <Input label="Category" value={form.category} onChange={set('category')} placeholder="e.g. Apparel" />
        </div>
        <Input label="Base URL *" value={form.base_url} onChange={set('base_url')} placeholder="https://example.com" />

        <div style={fieldStyle}>
          <label style={labelStyle}>Listing URLs (one per line)</label>
          <textarea value={form.listing_urls_text} onChange={set('listing_urls_text')}
            placeholder={"https://example.com/shop\nhttps://example.com/sale"}
            rows={3}
            style={{ width: '100%', background: '#0f1117', border: '1px solid #2d3048', borderRadius: 8, padding: '8px 12px', color: '#e4e4f0', fontSize: 14, outline: 'none', resize: 'vertical' }} />
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          <div style={fieldStyle}>
            <label style={labelStyle}>Scan Frequency (minutes)</label>
            <input type="number" min={5} value={form.scan_frequency_minutes} onChange={set('scan_frequency_minutes')}
              style={{ background: '#0f1117', border: '1px solid #2d3048', borderRadius: 8, padding: '8px 12px', color: '#e4e4f0', fontSize: 14, outline: 'none' }} />
          </div>
          <div style={fieldStyle}>
            <label style={labelStyle}>Scrape Type</label>
            <select value={form.scrape_type} onChange={set('scrape_type')}
              style={{ background: '#0f1117', border: '1px solid #2d3048', borderRadius: 8, padding: '8px 12px', color: '#e4e4f0', fontSize: 14, outline: 'none' }}>
              <option value="generic_selector">Generic Selector</option>
              <option value="custom">Custom</option>
            </select>
          </div>
        </div>

        <div style={fieldStyle}>
          <label style={labelStyle}>Selector Config (JSON)</label>
          <textarea value={form.selector_config_text} onChange={set('selector_config_text')}
            rows={7}
            style={{ width: '100%', background: '#0f1117', border: '1px solid #2d3048', borderRadius: 8, padding: '8px 12px', color: '#e4e4f0', fontSize: 13, outline: 'none', resize: 'vertical', fontFamily: 'monospace' }} />
        </div>

        <Input label="Discord Webhook URL (optional)" value={form.discord_webhook_url} onChange={set('discord_webhook_url')}
          placeholder="https://discord.com/api/webhooks/..." />
        <Textarea label="Notes (optional)" value={form.notes} onChange={set('notes')} rows={2} placeholder="Any notes about this competitor..." />

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <input type="checkbox" id="active" checked={form.active} onChange={set('active')} />
          <label htmlFor="active" style={{ fontSize: 14, color: '#c4c6d8', cursor: 'pointer' }}>Active (will be scanned)</label>
        </div>

        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', paddingTop: 8 }}>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} loading={loading}>{initial ? 'Save Changes' : 'Add Competitor'}</Button>
        </div>
      </div>
    </Modal>
  )
}
