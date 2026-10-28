"use client"

import { useCallback, useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { api } from "@/lib/api"
import { useToast } from "@/components/feedback/toast"
import type { ProviderConfig, ProviderTestResult, TutorProfile, User } from "@/lib/types"
import {
  Settings,
  Plus,
  Trash2,
  Key,
  Server,
  Save,
  AlertCircle,
  UserCircle,
  Database,
  CheckCircle2,
  Pencil,
  PlugZap,
  Loader2,
} from "lucide-react"

const EMPTY_PROVIDER = {
  provider_name: "",
  api_key: "",
  base_url: "",
  model: "",
  protocol: "openai",
  priority: 0,
  task_routing: "conversation,correction,lesson",
}

// Inline result of a connectivity probe (card or unsaved form).
function TestResultLine({ result }: { result: ProviderTestResult }) {
  if (result.ok) {
    return (
      <p
        role="status"
        className="flex flex-wrap items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400"
      >
        <CheckCircle2 className="h-3.5 w-3.5" />
        Connected · {result.provider ?? "provider"}
        {typeof result.latency_ms === "number" ? ` · ${result.latency_ms} ms` : ""}
        {result.reply ? ` · “${result.reply}”` : ""}
      </p>
    )
  }
  return (
    <p role="alert" className="flex items-start gap-1 text-xs text-destructive">
      <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>{result.error ?? "Connection failed"}</span>
    </p>
  )
}

const PERSONALITY_PRESETS = [
  "friendly",
  "professional",
  "strict",
  "humorous",
  "encouraging",
  "patient",
  "witty",
  "academic",
  "casual",
  "motivating",
]

export default function SettingsPage() {
  const { toast } = useToast()
  const [providers, setProviders] = useState<ProviderConfig[]>([])
  const [settings, setSettings] = useState<Record<string, string>>({})
  const [currentUser, setCurrentUser] = useState<User | null>(null)
  const [hasActivePath, setHasActivePath] = useState(false)
  const [savedCefr, setSavedCefr] = useState<string>("A1")
  const [preferencesSaved, setPreferencesSaved] = useState(false)
  const [savingPreferences, setSavingPreferences] = useState(false)
  const [showCefrConfirm, setShowCefrConfirm] = useState(false)
  const [pendingCefr, setPendingCefr] = useState<string | null>(null)
  const [, setTutorProfile] = useState<TutorProfile | null>(null)
  const [tutorForm, setTutorForm] = useState<Partial<Omit<TutorProfile, "id" | "user_id">>>({
    name: "",
    age: null,
    gender: "",
    personality: "",
    voice: "",
  })
  const [tutorSaved, setTutorSaved] = useState(false)
  const [customPersonality, setCustomPersonality] = useState(false)
  const [error, setError] = useState("")
  const [success, setSuccess] = useState("")
  const [showAddProvider, setShowAddProvider] = useState(false)
  const [showClearConfirm, setShowClearConfirm] = useState(false)
  const [clearing, setClearing] = useState(false)
  // Load failures used to be console.error only: the form silently rendered
  // defaults and users could save over settings they never saw.
  const [loadError, setLoadError] = useState("")
  const [loadingSettings, setLoadingSettings] = useState(true)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const [newProvider, setNewProvider] = useState({ ...EMPTY_PROVIDER })
  // Editing reuses the add form; the API key is never sent back to the client,
  // so a blank field means "keep the stored key".
  const [editingProviderId, setEditingProviderId] = useState<string | null>(null)
  const [providerSaving, setProviderSaving] = useState(false)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [testingDraft, setTestingDraft] = useState(false)
  const [draftTest, setDraftTest] = useState<ProviderTestResult | null>(null)
  const [providerTests, setProviderTests] = useState<Record<string, ProviderTestResult>>({})

  const loadSettings = useCallback(async () => {
    // Deliberately no setState before the first await: the effect runs this on
    // mount and must not cascade renders synchronously (react-hooks lint), and
    // `loadingSettings` already starts true.
    const [providersRes, settingsRes, meRes, pathRes, tutorRes] = await Promise.allSettled([
      api.settings.getProviders(),
      api.settings.get(),
      api.auth.me(),
      api.learningPath.current(),
      api.tutorProfile.get(),
    ])

    if (providersRes.status === "fulfilled") setProviders(providersRes.value)
    if (settingsRes.status === "fulfilled") {
      const map: Record<string, string> = {}
      settingsRes.value.forEach((s) => {
        map[s.key] = s.value
      })
      setSettings(map)
      if (map.default_cefr) setSavedCefr(map.default_cefr)
    }
    if (meRes.status === "fulfilled") {
      const loadedUser = meRes.value
      setCurrentUser(loadedUser)
      setSavedCefr((prev) => (prev && prev !== "A1" ? prev : loadedUser.current_level || "A1"))
    }
    setHasActivePath(pathRes.status === "fulfilled" && Boolean(pathRes.value?.is_active))
    if (tutorRes.status === "fulfilled") {
      const profile = tutorRes.value
      setTutorProfile(profile)
      setTutorForm({
        name: profile.name || "",
        age: profile.age,
        gender: profile.gender || "",
        personality: profile.personality || "",
        voice: profile.voice || "",
      })
      setCustomPersonality(
        !!profile.personality && !PERSONALITY_PRESETS.includes(profile.personality.toLowerCase()),
      )
    }

    const failed = [providersRes, settingsRes, meRes, tutorRes].find(
      (result) => result.status === "rejected",
    )
    if (failed && failed.status === "rejected") {
      setLoadError(
        failed.reason instanceof Error
          ? failed.reason.message
          : "Some settings could not be loaded. Retry before changing anything.",
      )
    }
    setLoadingSettings(false)
  }, [])

  useEffect(() => {
    // Initial fetch on mount. The lint rule matches setState anywhere in the
    // callee, but every state update here happens after the awaited
    // Promise.allSettled — nothing is set synchronously during the effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadSettings()
  }, [loadSettings])

  const announceTest = (result: ProviderTestResult) => {
    if (result.ok) {
      toast({
        title: "Connection OK",
        description: `${result.provider ?? "Provider"}${
          typeof result.latency_ms === "number" ? ` · ${result.latency_ms} ms` : ""
        }`,
        variant: "success",
      })
    } else {
      toast({
        title: "Connection failed",
        description: result.error ?? "Unknown provider error",
        variant: "error",
      })
    }
  }

  const closeProviderForm = () => {
    setShowAddProvider(false)
    setEditingProviderId(null)
    setDraftTest(null)
    setNewProvider({ ...EMPTY_PROVIDER })
  }

  const openAddProvider = () => {
    setConfirmDeleteId(null)
    setEditingProviderId(null)
    setDraftTest(null)
    setNewProvider({ ...EMPTY_PROVIDER })
    setShowAddProvider(true)
  }

  const openEditProvider = (provider: ProviderConfig) => {
    setConfirmDeleteId(null)
    setEditingProviderId(provider.id)
    setDraftTest(null)
    // The API key is never sent to the browser; blank means "keep it".
    setNewProvider({
      provider_name: provider.provider_name,
      api_key: "",
      base_url: provider.base_url,
      model: provider.model,
      protocol: provider.protocol,
      priority: provider.priority,
      task_routing: provider.task_routing,
    })
    setShowAddProvider(true)
  }

  const saveProvider = async () => {
    const isEdit = Boolean(editingProviderId)
    const missing =
      !newProvider.provider_name.trim() ||
      !newProvider.base_url.trim() ||
      !newProvider.model.trim() ||
      (!isEdit && !newProvider.api_key.trim())
    if (missing) {
      const message = isEdit
        ? "Provider name, base URL and model are required."
        : "Provider name, API key, base URL and model are all required."
      setError(message)
      toast({ title: "Provider not saved", description: message, variant: "error" })
      return
    }

    setProviderSaving(true)
    try {
      if (editingProviderId) {
        const payload: Record<string, unknown> = {
          provider_name: newProvider.provider_name.trim(),
          base_url: newProvider.base_url.trim(),
          model: newProvider.model.trim(),
          protocol: newProvider.protocol,
          priority: newProvider.priority,
          task_routing: newProvider.task_routing,
        }
        if (newProvider.api_key.trim()) payload.api_key = newProvider.api_key
        await api.settings.updateProvider(editingProviderId, payload)
      } else {
        await api.settings.createProvider(newProvider)
      }
      setProviders(await api.settings.getProviders())
      toast({
        title: isEdit ? "Provider updated" : "Provider saved",
        description: newProvider.provider_name.trim(),
        variant: "success",
      })
      closeProviderForm()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to save provider"
      setError(message)
      toast({ title: "Could not save the provider", description: message, variant: "error" })
    } finally {
      setProviderSaving(false)
    }
  }

  const testDraftProvider = async () => {
    if (!newProvider.base_url.trim() || !newProvider.model.trim()) {
      toast({
        title: "Nothing to test yet",
        description: "Enter the base URL and model first.",
        variant: "error",
      })
      return
    }
    if (!editingProviderId && !newProvider.api_key.trim()) {
      toast({ title: "API key required", description: "Enter a key to test.", variant: "error" })
      return
    }

    setTestingDraft(true)
    setDraftTest(null)
    try {
      const result =
        editingProviderId && !newProvider.api_key.trim()
          ? await api.settings.testProvider(editingProviderId)
          : await api.settings.testProviderDraft({
              provider_name: newProvider.provider_name.trim() || "test",
              protocol: newProvider.protocol,
              base_url: newProvider.base_url.trim(),
              model: newProvider.model.trim(),
              api_key: newProvider.api_key,
            })
      setDraftTest(result)
      announceTest(result)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Connection test failed"
      setDraftTest({ ok: false, error: message })
      toast({ title: "Connection failed", description: message, variant: "error" })
    } finally {
      setTestingDraft(false)
    }
  }

  const testSavedProvider = async (provider: ProviderConfig) => {
    setTestingId(provider.id)
    try {
      const result = await api.settings.testProvider(provider.id)
      setProviderTests((prev) => ({ ...prev, [provider.id]: result }))
      announceTest(result)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Connection test failed"
      setProviderTests((prev) => ({ ...prev, [provider.id]: { ok: false, error: message } }))
      toast({ title: "Connection failed", description: message, variant: "error" })
    } finally {
      setTestingId(null)
    }
  }

  const deleteProvider = async (id: string) => {
    const removed = providers.find((provider) => provider.id === id)
    try {
      await api.settings.deleteProvider(id)
      setProviders((current) => current.filter((provider) => provider.id !== id))
      setConfirmDeleteId(null)
      toast({
        title: "Provider removed",
        description: removed ? `${removed.provider_name} and its stored API key were deleted.` : undefined,
        variant: "success",
      })
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to delete provider"
      setError(message)
      toast({ title: "Could not remove the provider", description: message, variant: "error" })
    }
  }

  const cancelCefrChange = () => {
    setSettings((prev) => ({ ...prev, default_cefr: savedCefr }))
    setPendingCefr(null)
    setShowCefrConfirm(false)
  }

  const confirmCefrChange = async () => {
    if (!pendingCefr) return
    setSavingPreferences(true)
    setError("")
    try {
      const updatedSettings = { ...settings, default_cefr: pendingCefr }
      await api.settings.update({
        ...updatedSettings,
        regenerate_path: true,
      })
      setSettings(updatedSettings)
      setSavedCefr(pendingCefr)
      if (currentUser) {
        setCurrentUser({ ...currentUser, current_level: pendingCefr })
      }
      setHasActivePath(true)
      setShowCefrConfirm(false)
      setPendingCefr(null)
      setPreferencesSaved(true)
      setTimeout(() => setPreferencesSaved(false), 2000)
      toast({
        title: "Preferences saved",
        description: `Default level is now ${pendingCefr}; your learning path was regenerated.`,
        variant: "success",
      })
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to update preferences and regenerate path"
      setError(message)
      toast({ title: "Could not save preferences", description: message, variant: "error" })
    } finally {
      setSavingPreferences(false)
    }
  }

  const saveSettings = async () => {
    const newLevel = settings.default_cefr || "A1"
    const levelChanged = newLevel !== savedCefr
    if (levelChanged && (currentUser?.assessment_completed || hasActivePath)) {
      setPendingCefr(newLevel)
      setShowCefrConfirm(true)
      return
    }

    setSavingPreferences(true)
    setError("")
    try {
      await api.settings.update(settings)
      setSavedCefr(newLevel)
      if (currentUser && levelChanged) {
        setCurrentUser({ ...currentUser, current_level: newLevel })
      }
      setPreferencesSaved(true)
      setTimeout(() => setPreferencesSaved(false), 2000)
      toast({ title: "Preferences saved", variant: "success" })
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to save preferences"
      setError(message)
      toast({ title: "Could not save preferences", description: message, variant: "error" })
    } finally {
      setSavingPreferences(false)
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
      toast({ title: "Tutor profile saved", variant: "success" })
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to save tutor profile"
      setError(message)
      toast({ title: "Could not save the tutor profile", description: message, variant: "error" })
    }
  }

  const clearData = async () => {
    setClearing(true)
    setError("")
    try {
      await api.settings.clearData()
      setSuccess("All data cleared successfully. Refreshing...")
      toast({
        title: "All learning data cleared",
        description: "Your account and provider keys were kept.",
        variant: "success",
      })
      setTimeout(() => {
        window.location.reload()
      }, 1500)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to clear data"
      setError(message)
      toast({ title: "Could not clear your data", description: message, variant: "error" })
    } finally {
      setClearing(false)
      setShowClearConfirm(false)
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

      {loadError && (
        <div role="alert" className="flex items-center gap-2 rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span className="flex-1">{loadError}</span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setLoadError("")
              setLoadingSettings(true)
              void loadSettings()
            }}
          >
            Retry
          </Button>
        </div>
      )}

      {loadingSettings && (
        <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
          Loading your settings…
        </p>
      )}

      {success && (
        <div className="flex items-center gap-2 p-3 rounded-md bg-green-600/10 text-green-400 text-sm">
          <CheckCircle2 className="h-4 w-4" />
          {success}
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
            <div key={p.id} className="rounded-lg bg-secondary p-3 space-y-2">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="space-y-1 min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{p.provider_name}</span>
                    <Badge variant="secondary">{p.protocol}</Badge>
                    <Badge className="max-w-full break-all whitespace-normal">{p.model}</Badge>
                    {p.is_active ? (
                      <Badge className="bg-green-600">Active</Badge>
                    ) : (
                      <Badge variant="outline">Inactive</Badge>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground break-all">{p.base_url}</p>
                  <p className="text-xs text-muted-foreground">Tasks: {p.task_routing}</p>
                </div>
                {confirmDeleteId === p.id ? (
                  <div className="flex items-center gap-1 self-end sm:self-center shrink-0">
                    <Button size="sm" variant="destructive" onClick={() => deleteProvider(p.id)}>
                      Delete
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setConfirmDeleteId(null)}>
                      Cancel
                    </Button>
                  </div>
                ) : (
                  <div className="flex items-center gap-1 self-end sm:self-center shrink-0">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => void testSavedProvider(p)}
                      disabled={testingId === p.id}
                      title="Send a tiny completion to verify the key, URL and model"
                    >
                      {testingId === p.id ? (
                        <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <PlugZap className="mr-1 h-3.5 w-3.5" />
                      )}
                      Test
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => openEditProvider(p)}
                      aria-label={`Edit ${p.provider_name}`}
                      title="Edit provider"
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => setConfirmDeleteId(p.id)}
                      aria-label={`Delete ${p.provider_name}`}
                      title="Delete this provider and its stored API key"
                    >
                      <Trash2 className="h-4 w-4 text-destructive" />
                    </Button>
                  </div>
                )}
              </div>
              {providerTests[p.id] && <TestResultLine result={providerTests[p.id]} />}
            </div>
          ))}

          {showAddProvider ? (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">
                  {editingProviderId ? "Edit provider" : "Add provider"}
                </CardTitle>
                <CardDescription>
                  {editingProviderId
                    ? "Leave the API key blank to keep the stored one."
                    : "Keys are encrypted at rest and never sent back to the browser."}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
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
                    placeholder={editingProviderId ? "Leave blank to keep the stored key" : "sk-..."}
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
                {draftTest && <TestResultLine result={draftTest} />}
                <div className="flex flex-wrap gap-2">
                  <Button onClick={() => void saveProvider()} size="sm" disabled={providerSaving || testingDraft}>
                    {providerSaving ? (
                      <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                    ) : (
                      <Save className="mr-1 h-4 w-4" />
                    )}
                    {editingProviderId ? "Save changes" : "Save Provider"}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void testDraftProvider()}
                    disabled={testingDraft || providerSaving}
                  >
                    {testingDraft ? (
                      <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                    ) : (
                      <PlugZap className="mr-1 h-4 w-4" />
                    )}
                    Test connection
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={closeProviderForm}
                    disabled={providerSaving || testingDraft}
                  >
                    Cancel
                  </Button>
                </div>
              </CardContent>
            </Card>
          ) : (
            <Button variant="outline" onClick={openAddProvider}>
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
            {customPersonality ? (
              <>
                <Input
                  value={tutorForm.personality || ""}
                  onChange={(e) => setTutorForm({ ...tutorForm, personality: e.target.value })}
                  placeholder="Describe the personality, e.g. friendly and witty..."
                />
                <button
                  type="button"
                  className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
                  onClick={() => {
                    setCustomPersonality(false)
                    setTutorForm((f) => ({ ...f, personality: "" }))
                  }}
                >
                  Choose from presets
                </button>
              </>
            ) : (
              <Select
                value={tutorForm.personality || undefined}
                onValueChange={(v) => {
                  if (v === "__custom__") {
                    setCustomPersonality(true)
                    setTutorForm((f) => ({ ...f, personality: "" }))
                  } else {
                    setTutorForm((f) => ({ ...f, personality: v }))
                  }
                }}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select a personality" />
                </SelectTrigger>
                <SelectContent>
                  {PERSONALITY_PRESETS.map((preset) => (
                    <SelectItem key={preset} value={preset} className="capitalize">
                      {preset}
                    </SelectItem>
                  ))}
                  <SelectItem value="__custom__">Custom...</SelectItem>
                </SelectContent>
              </Select>
            )}
            <p className="text-xs text-muted-foreground">Pick a preset or write your own to guide the tutor&apos;s tone in conversations, lessons, and assessment.</p>
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
                <SelectItem value="web_speech">Web Speech API (Browser native, lowest latency)</SelectItem>
                <SelectItem value="personal_api">Personal API / Moonshine (Highest accuracy & fluency)</SelectItem>
                <SelectItem value="whisper_server">faster-whisper (Server-side transcription)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label>TTS Voice</Label>
            <Input
              value={settings.tts_voice ?? ""}
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
              value={settings.default_cefr || "A1"}
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
          <Button onClick={saveSettings} disabled={preferencesSaved || savingPreferences}>
            {preferencesSaved ? (
              <>
                <CheckCircle2 className="h-4 w-4 mr-1" />
                Saved!
              </>
            ) : (
              <>
                <Save className="h-4 w-4 mr-1" />
                {savingPreferences ? "Saving..." : "Save Preferences"}
              </>
            )}
          </Button>
        </CardContent>
      </Card>

      <Card className="border-destructive/50">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-destructive">
            <Database className="h-5 w-5" />
            Clear All Data
          </CardTitle>
          <CardDescription>
            Delete all your learning data (sessions, messages, vocabulary, assessments, lessons, progress).
            Your account and provider configurations will be preserved.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {showClearConfirm ? (
            <div className="space-y-3">
              <p className="text-sm text-destructive font-medium">
                Are you sure? This action cannot be undone.
              </p>
              <div className="flex gap-2">
                <Button
                  variant="destructive"
                  onClick={clearData}
                  disabled={clearing}
                >
                  <Trash2 className="h-4 w-4 mr-1" />
                  {clearing ? "Clearing..." : "Yes, Clear All Data"}
                </Button>
                <Button
                  variant="outline"
                  onClick={() => setShowClearConfirm(false)}
                  disabled={clearing}
                >
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <Button
              variant="destructive"
              onClick={() => setShowClearConfirm(true)}
            >
              <Trash2 className="h-4 w-4 mr-1" />
              Clear All Data
            </Button>
          )}
        </CardContent>
      </Card>

      {showCefrConfirm && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="confirm-cefr-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in duration-150"
          onClick={(e) => {
            if (e.target === e.currentTarget && !savingPreferences) {
              cancelCefrChange()
            }
          }}
        >
          <Card className="w-full max-w-md shadow-2xl border-primary/20" onClick={(e) => e.stopPropagation()}>
            <CardHeader>
              <CardTitle id="confirm-cefr-title">Confirm CEFR Level Change</CardTitle>
              <CardDescription>
                Changing your default CEFR level to {pendingCefr} will update your profile level and regenerate your active learning path. Do you want to proceed?
              </CardDescription>
            </CardHeader>
            <CardContent className="flex justify-end gap-2">
              <Button
                variant="outline"
                onClick={cancelCefrChange}
                disabled={savingPreferences}
              >
                Cancel
              </Button>
              <Button
                onClick={confirmCefrChange}
                disabled={savingPreferences}
              >
                {savingPreferences ? "Regenerating..." : "Confirm & Regenerate"}
              </Button>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}
