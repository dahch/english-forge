"use client"

import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { api } from "@/lib/api"
import type { ProviderConfig, TutorProfile } from "@/lib/types"
import { Settings, Plus, Trash2, Key, Server, Save, AlertCircle, UserCircle } from "lucide-react"

export default function SettingsPage() {
  const [providers, setProviders] = useState<ProviderConfig[]>([])
  const [settings, setSettings] = useState<Record<string, string>>({})
  const [tutorProfile, setTutorProfile] = useState<TutorProfile | null>(null)
  const [tutorForm, setTutorForm] = useState<Partial<Omit<TutorProfile, "id" | "user_id">>>({
    name: "",
    age: null,
    gender: "",
    personality: "",
    voice: "",
  })
  const [tutorSaved, setTutorSaved] = useState(false)
  const [error, setError] = useState("")
  const [showAddProvider, setShowAddProvider] = useState(false)
  const [newProvider, setNewProvider] = useState({
    provider_name: "",
    api_key: "",
    base_url: "",
    model: "",
    protocol: "openai",
    priority: 0,
    task_routing: "conversation,correction,lesson",
  })

  useEffect(() => {
    api.settings.getProviders().then(setProviders).catch(console.error)
    api.settings.get().then((data) => {
      const map: Record<string, string> = {}
      data.forEach((s) => { map[s.key] = s.value })
      setSettings(map)
    }).catch(console.error)
    api.tutorProfile.get().then((p) => {
      setTutorProfile(p)
      setTutorForm({
        name: p.name || "",
        age: p.age,
        gender: p.gender || "",
        personality: p.personality || "",
        voice: p.voice || "",
      })
    }).catch(console.error)
  }, [])

  const addProvider = async () => {
    if (!newProvider.provider_name || !newProvider.api_key || !newProvider.base_url || !newProvider.model) return
    try {
      await api.settings.createProvider(newProvider)
      const updated = await api.settings.getProviders()
      setProviders(updated)
      setShowAddProvider(false)
      setNewProvider({ provider_name: "", api_key: "", base_url: "", model: "", protocol: "openai", priority: 0, task_routing: "conversation,correction,lesson" })
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to save provider")
    }
  }

  const deleteProvider = async (id: string) => {
    try {
      await api.settings.deleteProvider(id)
      setProviders(providers.filter((p) => p.id !== id))
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to delete provider")
    }
  }

  const saveSettings = async () => {
    try {
      await api.settings.update(settings)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to save preferences")
    }
  }

  const saveTutorProfile = async () => {
    try {
      const payload = {
        ...tutorForm,
        age: tutorForm.age ? Number(tutorForm.age) : null,
      }
      const updated = await api.tutorProfile.update(payload)
      setTutorProfile(updated)
      setTutorSaved(true)
      setTimeout(() => setTutorSaved(false), 2000)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to save tutor profile")
    }
  }

  return (
    <div className="max-w-3xl mx-auto p-6 space-y-6">
      <h1 className="text-2xl font-bold flex items-center gap-2">
        <Settings className="h-6 w-6 text-primary" />
        Settings
      </h1>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-md bg-destructive/10 text-destructive text-sm">
          <AlertCircle className="h-4 w-4" />
          {error}
          <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setError("")}>Dismiss</Button>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Key className="h-5 w-5" />
            LLM Providers
          </CardTitle>
          <CardDescription>Configure your BYOK providers. At least one is required for conversations.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {providers.length === 0 && !showAddProvider && (
            <p className="text-sm text-muted-foreground">No providers configured. Add one to get started.</p>
          )}
          {providers.map((p) => (
            <div key={p.id} className="flex items-center justify-between p-3 rounded-lg bg-secondary">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{p.provider_name}</span>
                  <Badge variant="secondary">{p.protocol}</Badge>
                  <Badge>{p.model}</Badge>
                  {p.is_active ? (
                    <Badge className="bg-green-600">Active</Badge>
                  ) : (
                    <Badge variant="outline">Inactive</Badge>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">{p.base_url}</p>
                <p className="text-xs text-muted-foreground">Tasks: {p.task_routing}</p>
              </div>
              <Button variant="ghost" size="icon" onClick={() => deleteProvider(p.id)}>
                <Trash2 className="h-4 w-4 text-destructive" />
              </Button>
            </div>
          ))}

          {showAddProvider ? (
            <Card>
              <CardContent className="p-4 space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <Label>Provider Name</Label>
                    <Input
                      placeholder="e.g. openai, deepseek"
                      value={newProvider.provider_name}
                      onChange={(e) => setNewProvider({ ...newProvider, provider_name: e.target.value })}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label>Protocol</Label>
                    <Select value={newProvider.protocol} onValueChange={(v) => setNewProvider({ ...newProvider, protocol: v })}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="openai">OpenAI Compatible</SelectItem>
                        <SelectItem value="anthropic">Anthropic</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <div className="space-y-1">
                  <Label>API Key</Label>
                  <Input
                    type="password"
                    placeholder="sk-..."
                    value={newProvider.api_key}
                    onChange={(e) => setNewProvider({ ...newProvider, api_key: e.target.value })}
                  />
                </div>
                <div className="space-y-1">
                  <Label>Base URL</Label>
                  <Input
                    placeholder="https://api.openai.com/v1"
                    value={newProvider.base_url}
                    onChange={(e) => setNewProvider({ ...newProvider, base_url: e.target.value })}
                  />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <Label>Model</Label>
                    <Input
                      placeholder="gpt-4.1-mini"
                      value={newProvider.model}
                      onChange={(e) => setNewProvider({ ...newProvider, model: e.target.value })}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label>Priority</Label>
                    <Input
                      type="number"
                      value={newProvider.priority}
                      onChange={(e) => setNewProvider({ ...newProvider, priority: parseInt(e.target.value) || 0 })}
                    />
                  </div>
                </div>
                <div className="flex gap-2">
                  <Button onClick={addProvider} size="sm">Save Provider</Button>
                  <Button variant="outline" size="sm" onClick={() => setShowAddProvider(false)}>Cancel</Button>
                </div>
              </CardContent>
            </Card>
          ) : (
            <Button variant="outline" onClick={() => setShowAddProvider(true)}>
              <Plus className="h-4 w-4 mr-1" />
              Add Provider
            </Button>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <UserCircle className="h-5 w-5" />
            Tutor Profile
          </CardTitle>
          <CardDescription>Personalize the tutor that chats, teaches, and assesses you.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>Name</Label>
              <Input
                value={tutorForm.name}
                onChange={(e) => setTutorForm({ ...tutorForm, name: e.target.value })}
                placeholder="e.g. Sarah"
              />
            </div>
            <div className="space-y-1">
              <Label>Age</Label>
              <Input
                type="number"
                value={tutorForm.age ?? ""}
                onChange={(e) => setTutorForm({ ...tutorForm, age: e.target.value ? Number(e.target.value) : null })}
                placeholder="30"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>Gender</Label>
              <Select value={tutorForm.gender || "unspecified"} onValueChange={(v) => setTutorForm({ ...tutorForm, gender: v === "unspecified" ? "" : v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="unspecified">Unspecified</SelectItem>
                  <SelectItem value="female">Female</SelectItem>
                  <SelectItem value="male">Male</SelectItem>
                  <SelectItem value="non-binary">Non-binary</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>TTS Voice</Label>
              <Input
                value={tutorForm.voice || ""}
                onChange={(e) => setTutorForm({ ...tutorForm, voice: e.target.value })}
                placeholder="e.g. alba"
              />
              <p className="text-xs text-muted-foreground">Voice ID used when the tutor speaks.</p>
            </div>
          </div>
          <div className="space-y-1">
            <Label>Personality</Label>
            <Input
              value={tutorForm.personality || ""}
              onChange={(e) => setTutorForm({ ...tutorForm, personality: e.target.value })}
              placeholder="Friendly, encouraging, patient, witty..."
            />
            <p className="text-xs text-muted-foreground">A short description guides the tutor's tone in conversations, lessons, and assessment.</p>
          </div>
          <Button onClick={saveTutorProfile} disabled={tutorSaved}>
            <Save className="h-4 w-4 mr-1" />
            {tutorSaved ? "Saved!" : "Save Tutor Profile"}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Server className="h-5 w-5" />
            Preferences
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1">
            <Label>STT Mode</Label>
            <Select
              value={settings.stt_mode || "web_speech"}
              onValueChange={(v) => setSettings({ ...settings, stt_mode: v })}
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="web_speech">Web Speech API (default, lowest latency)</SelectItem>
                <SelectItem value="whisper_wasm">Whisper WASM (offline, browser-based)</SelectItem>
                <SelectItem value="whisper_server">faster-whisper (server-side)</SelectItem>
                <SelectItem value="personal_api">Personal API / Moonshine (highest accuracy)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label>TTS Voice</Label>
            <Input
              value={settings.tts_voice || "alba"}
              onChange={(e) => setSettings({ ...settings, tts_voice: e.target.value })}
              placeholder="alba"
            />
            <p className="text-xs text-muted-foreground">
              Verify available voices against GET /v1/voices on Pocket TTS before changing.
            </p>
          </div>
          <div className="space-y-1">
            <Label>Default CEFR Level</Label>
            <Select
              value={settings.default_cefr || "B1"}
              onValueChange={(v) => setSettings({ ...settings, default_cefr: v })}
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {["A1", "A2", "B1", "B2", "C1", "C2"].map((l) => (
                  <SelectItem key={l} value={l}>{l}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button onClick={saveSettings}>
            <Save className="h-4 w-4 mr-1" />
            Save Preferences
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
