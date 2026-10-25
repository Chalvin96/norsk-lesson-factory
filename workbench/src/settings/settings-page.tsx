import * as React from 'react';
import type { WorkbenchEffort, WorkbenchRole, WorkbenchSettings } from '@/api/contracts';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useResetWorkbenchSettings, useSaveWorkbenchSettings, useWorkbenchSettings } from '@/settings/api';

const ROLE_ROWS: { role: WorkbenchRole; label: string; description: string }[] = [
  { role: 'chat', label: 'Chat', description: 'Codex answers questions and drafts proposals in chat.' },
  { role: 'generation', label: 'Generation', description: 'Drafts and regenerates lessons and exercises.' },
  { role: 'lesson_review', label: 'Lesson review', description: 'Runs the advisory AI lesson review.' },
  { role: 'exercise_review', label: 'Exercise review', description: 'Reviews exercise sets after regeneration.' },
];

function modelLabel(model: string, options: string[]): string {
  const name = model.split('/', 2).at(-1) ?? model;
  const hasDuplicateName = options.some(
    (option) => option !== model && (option.split('/', 2).at(-1) ?? option) === name,
  );
  return hasDuplicateName ? `${name} (${model.split('/', 2)[0]})` : name;
}

export function SettingsPage() {
  const query = useWorkbenchSettings();
  const save = useSaveWorkbenchSettings();
  const reset = useResetWorkbenchSettings();
  const [settings, setSettings] = React.useState<WorkbenchSettings | null>(null);
  const settingsRef = React.useRef(settings);
  settingsRef.current = settings;
  const savedBaseline = React.useRef<WorkbenchSettings | null>(null);
  const settingsRevision = React.useRef(0);
  React.useEffect(() => {
    if (!query.data) return;
    const incoming = structuredClone(query.data.settings);
    const baseline = savedBaseline.current;
    const draft = settingsRef.current;
    const draftMatchesBaseline = baseline === null || draft === null || settingsEqual(draft, baseline);
    savedBaseline.current = incoming;
    if (draftMatchesBaseline) {
      settingsRef.current = incoming;
      setSettings(incoming);
    }
  }, [query.data]);

  const updateSettings = (update: (current: WorkbenchSettings) => WorkbenchSettings) => {
    const current = settingsRef.current;
    if (!current) return;
    const next = update(current);
    settingsRevision.current += 1;
    settingsRef.current = next;
    setSettings(next);
  };

  const updateRole = (role: WorkbenchRole, field: 'model' | 'effort', value: string) => {
    updateSettings((current) => ({
      ...current,
      roles: { ...current.roles, [role]: { ...current.roles[role], [field]: value } },
    }));
  };

  const saveSettings = () => {
    if (!settings) return;
    const submitted = structuredClone(settings);
    const submittedRevision = settingsRevision.current;
    save.mutate(submitted, {
      onSuccess: (result) => {
        const baseline = structuredClone(result.settings);
        savedBaseline.current = baseline;
        if (settingsRevision.current === submittedRevision) {
          settingsRef.current = baseline;
          setSettings(baseline);
        }
      },
    });
  };

  const resetSettings = () => {
    const submittedRevision = settingsRevision.current;
    reset.mutate(undefined, {
      onSuccess: (result) => {
        const baseline = structuredClone(result.settings);
        savedBaseline.current = baseline;
        if (settingsRevision.current === submittedRevision) {
          settingsRef.current = baseline;
          setSettings(baseline);
        }
      },
    });
  };
  const error = query.error ?? save.error ?? reset.error;

  return (
    <main
      id="workbench-main"
      tabIndex={-1}
      className="min-h-0 flex-1 overflow-y-auto bg-background px-4 py-8 focus-visible:outline-2 focus-visible:outline-ring sm:px-8"
    >
      <div className="mx-auto max-w-[760px]">
        <h1 className="m-0 text-3xl font-semibold">Settings</h1>
        <p className="mt-2 text-sm text-secondary-80">
          Model choices for each workbench role. Stored with the workspace; changes apply to the next job.
        </p>
        {query.isLoading ? (
          <p role="status" className="mt-6">
            Loading settings…
          </p>
        ) : null}
        {error ? (
          <p
            role="alert"
            className="mt-4 rounded-lg border border-destructive-40 bg-destructive-0 p-3 text-sm text-destructive-100"
          >
            Could not load or save settings: {error.message}
          </p>
        ) : null}
        {settings ? (
          <div className="mt-6 space-y-5">
            <section aria-labelledby="models-title" className="rounded-2xl border border-primary-20 bg-white p-5">
              <h2 id="models-title" className="m-0 text-xl font-semibold">
                Models by role
              </h2>
              <p className="mt-1 text-sm text-secondary-80">
                One model per role. Reasoning effort is optional and defaults to medium.
              </p>
              <div className="mt-4 space-y-3">
                <div
                  aria-hidden="true"
                  className="hidden grid-cols-[minmax(7rem,1fr)_minmax(8rem,1.2fr)_minmax(7rem,.8fr)] gap-3 sm:grid"
                >
                  <span />
                  <span className="text-xs font-semibold text-secondary-80">Model</span>
                  <span className="text-xs font-semibold text-secondary-80">Reasoning effort</span>
                </div>
                {ROLE_ROWS.map(({ role, label, description }) => (
                  <div
                    key={role}
                    className="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(7rem,1fr)_minmax(8rem,1.2fr)_minmax(7rem,.8fr)] sm:items-center"
                  >
                    <div>
                      <h3 className="m-0 text-sm font-semibold">{label}</h3>
                      <p className="m-0 mt-1 text-xs text-secondary-80">{description}</p>
                    </div>
                    <div className="min-w-0">
                      <span className="mb-1 block text-xs font-semibold text-secondary-80 sm:hidden">Model</span>
                      <Select
                        value={settings.roles[role].model}
                        onValueChange={(value) => updateRole(role, 'model', value)}
                      >
                        <SelectTrigger aria-label={`${label} model`} className="w-full">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {(query.data?.modelOptions[role] ?? []).map((model) => (
                            <SelectItem key={model} value={model}>
                              {modelLabel(model, query.data?.modelOptions[role] ?? [])}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="min-w-0">
                      <span className="mb-1 block text-xs font-semibold text-secondary-80 sm:hidden">
                        Reasoning effort
                      </span>
                      <Select
                        value={settings.roles[role].effort}
                        onValueChange={(value) => updateRole(role, 'effort', value as WorkbenchEffort)}
                      >
                        <SelectTrigger aria-label={`${label} reasoning effort`} className="w-full">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {(query.data?.effortOptions[role] ?? []).map((effort) => (
                            <SelectItem key={effort} value={effort}>
                              {effort}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <section
              aria-labelledby="second-opinion-title"
              className="rounded-2xl border border-primary-20 bg-white p-5"
            >
              <h2 id="second-opinion-title" className="m-0 text-xl font-semibold">
                Second opinion
              </h2>
              <p className="mt-1 text-sm text-secondary-80">
                Ask a reviewer from another model family to review the same source. Its findings stay advisory.
              </p>
              <div className="mt-4 flex flex-col items-stretch gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h3 className="m-0 text-sm font-semibold">Second-opinion reviewer</h3>
                  <p className="m-0 mt-1 text-xs text-secondary-80">Runs after the main AI review when switched on.</p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={settings.second_opinion.enabled}
                  aria-label="Second-opinion reviewer"
                  onClick={() =>
                    updateSettings((current) => ({
                      ...current,
                      second_opinion: {
                        ...current.second_opinion,
                        enabled: !current.second_opinion.enabled,
                      },
                    }))
                  }
                  className={`relative h-6 w-11 shrink-0 rounded-full transition-colors focus-visible:ring-2 focus-visible:ring-ring ${settings.second_opinion.enabled ? 'bg-primary-80' : 'bg-secondary-30'}`}
                >
                  <span
                    className={`absolute top-1 size-4 rounded-full bg-white transition-transform ${settings.second_opinion.enabled ? 'translate-x-6' : 'translate-x-1'}`}
                  />
                </button>
              </div>
              <div className="mt-4 flex flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:justify-between">
                <label htmlFor="second-opinion-model" className="text-sm font-semibold text-secondary-80">
                  Reviewer model
                </label>
                <Select
                  value={settings.second_opinion.model}
                  disabled={!settings.second_opinion.enabled}
                  onValueChange={(model) =>
                    updateSettings((current) => ({
                      ...current,
                      second_opinion: { ...current.second_opinion, model },
                    }))
                  }
                >
                  <SelectTrigger id="second-opinion-model" aria-label="Reviewer model" className="w-full sm:w-56">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(query.data?.modelOptions.second_opinion ?? []).map((model) => (
                      <SelectItem key={model} value={model}>
                        {modelLabel(model, query.data?.modelOptions.second_opinion ?? [])}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </section>

            <p className="text-sm text-secondary-80">
              AI review findings are always advisory. Promotion requires valid source and complete audio.
            </p>

            <section aria-labelledby="audio-title" className="rounded-2xl border border-primary-20 bg-white p-5">
              <h2 id="audio-title" className="m-0 text-xl font-semibold">
                Lesson audio
              </h2>
              <p className="mt-1 text-sm text-secondary-80">
                Set the most you want to spend on workbench audio synthesis each month.
              </p>
              <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <label htmlFor="audio-monthly-cap" className="text-sm font-semibold text-secondary-80">
                  Monthly cap (USD)
                </label>
                <Input
                  id="audio-monthly-cap"
                  aria-label="Monthly audio cap in USD"
                  type="number"
                  min="0"
                  step="0.01"
                  className="w-full sm:w-40"
                  value={settings.audio.monthly_cap_usd}
                  onChange={(event) =>
                    updateSettings((current) => ({
                      ...current,
                      audio: { monthly_cap_usd: Number(event.target.value) },
                    }))
                  }
                />
              </div>
            </section>

            <section aria-labelledby="defaults-title" className="rounded-2xl border border-primary-20 bg-white p-5">
              <h2 id="defaults-title" className="m-0 text-xl font-semibold">
                Defaults
              </h2>
              <div className="mt-4 flex flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h3 className="m-0 text-sm font-semibold">Review scope</h3>
                  <p className="m-0 mt-1 text-xs text-secondary-80">Used when you press Run AI review.</p>
                </div>
                <Select
                  value={settings.defaults.review_scope}
                  onValueChange={(review_scope) =>
                    updateSettings((current) => ({
                      ...current,
                      defaults: { review_scope: review_scope as WorkbenchSettings['defaults']['review_scope'] },
                    }))
                  }
                >
                  <SelectTrigger aria-label="Review scope" className="w-full sm:w-56">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="lesson">Whole lesson</SelectItem>
                    <SelectItem value="section">Current section</SelectItem>
                    <SelectItem value="item">Selected block</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </section>

            <div className="flex flex-wrap gap-2">
              <Button disabled={save.isPending || reset.isPending} onClick={saveSettings}>
                {save.isPending ? 'Saving…' : 'Save settings'}
              </Button>
              <Button variant="outline" disabled={save.isPending || reset.isPending} onClick={resetSettings}>
                {reset.isPending ? 'Resetting…' : 'Reset to defaults'}
              </Button>
              {save.isSuccess || reset.isSuccess ? (
                <span role="status" className="self-center text-sm text-primary-90">
                  Settings saved.
                </span>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>
    </main>
  );
}

function settingsEqual(left: WorkbenchSettings, right: WorkbenchSettings): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}
