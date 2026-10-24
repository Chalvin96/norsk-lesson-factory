import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { WorkbenchAudioClip, WorkbenchAudioPlan } from '@/api/contracts';

import { AudioClipControl, LessonAudioPlanDialog, VoiceLegend } from './lesson-audio';

const clip: WorkbenchAudioClip = {
  binding: 'section:intro:block:0',
  rowKey: 'intro#0',
  fingerprint: 'a'.repeat(64),
  audioId: 'clip-1',
  filename: 'clip-1.wav',
  text: 'Hei, jeg heter Mina.',
  translation: 'Hi, my name is Mina.',
  voice: 'nb-NO-Finn',
  speakerId: 'mina',
  speakerName: 'Mina',
  origin: 'lesson_reading',
  characters: 20,
  status: 'missing',
  durationMs: null,
  playUrl: '/api/lessons/demo/audio/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  findings: [],
};

const plan: WorkbenchAudioPlan = {
  token: 'plan-token',
  lessonId: 'demo',
  scope: 'block',
  selection: 'missing',
  blockKey: 'intro#0',
  clips: [
    {
      binding: clip.binding,
      rowKey: clip.rowKey,
      fingerprint: clip.fingerprint,
      audioId: clip.audioId,
      filename: clip.filename,
      text: clip.text,
      translation: clip.translation,
      voice: clip.voice,
      speakerId: clip.speakerId,
      speakerName: clip.speakerName,
      origin: clip.origin,
      source: {},
      characters: clip.characters,
      forceSynthesis: false,
    },
  ],
  newCount: 1,
  reusedCount: 2,
  charactersToSynthesize: 20,
  estimatedCostUsd: 0.01,
  monthlySpentUsd: 0.17,
  monthlyCapUsd: 5,
  blockedReason: null,
};

describe('Lesson audio', () => {
  it('test_audio_clip_given_missing_and_stale_status_expect_generation_actions', async () => {
    const onGenerate = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(<AudioClipControl clip={clip} onGenerate={onGenerate} />);

    expect(screen.getByText('No audio yet')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Generate audio…' }));
    expect(onGenerate).toHaveBeenCalledWith('intro#0');

    rerender(
      <AudioClipControl
        clip={{ ...clip, status: 'stale', findings: [{ code: 'length', message: 'Clip is too short.' }] }}
        onGenerate={onGenerate}
      />,
    );
    expect(screen.getByText('Audio stale · text changed')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Regenerate audio…' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Audio check · length: Clip is too short.');
  });

  it('test_audio_clip_given_ready_status_expect_playback_control_and_voice', () => {
    render(<AudioClipControl clip={{ ...clip, status: 'ready', durationMs: 3400 }} onGenerate={vi.fn()} />);

    expect(screen.getByRole('button', { name: 'Play audio: Hei, jeg heter Mina.' })).toBeInTheDocument();
    expect(screen.getByText('Audio ready · 0:03 · voice nb-NO-Finn')).toBeInTheDocument();
  });

  it('test_voice_legend_given_release_voice_changed_expect_warning', () => {
    render(
      <VoiceLegend
        status={{
          clips: [],
          voices: [
            {
              speakerId: 'mina',
              speakerName: 'Mina',
              voice: 'nb-NO-Finn',
              changedSinceRelease: true,
              releasedVoice: 'nb-NO-Leda',
            },
          ],
          missingCount: 0,
          staleCount: 0,
          readyCount: 0,
          blockers: [],
        }}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent(
      'Voice changed since last release · Mina: nb-NO-Leda → nb-NO-Finn',
    );
  });

  it('test_audio_plan_dialog_given_confirmable_estimate_expect_cost_and_confirmation', async () => {
    const onConfirm = vi.fn();
    const user = userEvent.setup();
    render(
      <LessonAudioPlanDialog
        plan={plan}
        onClose={vi.fn()}
        onConfirm={onConfirm}
        busy={false}
        progress={[]}
        completed={false}
        error={null}
      />,
    );
    const dialog = await screen.findByRole('dialog', { name: 'Generate missing audio' });

    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveClass('border-border-strong', 'bg-popover');
    expect(within(dialog).getByText('Hi, my name is Mina.')).toBeInTheDocument();
    expect(within(dialog).getByText('$0.01').closest('dl')).toHaveClass('dark:bg-control-disabled');
    expect(within(dialog).getByText('Hi, my name is Mina.')).toHaveClass('dark:text-muted-foreground');
    expect(within(dialog).getByText('$0.17 / $5.00')).toBeInTheDocument();
    expect(
      within(dialog).getByText('Release still runs from the CLI and checks audio coverage there.'),
    ).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Generate 1 clip' }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it('test_audio_plan_dialog_given_synthesis_error_expect_reconfirmation_required', async () => {
    render(
      <LessonAudioPlanDialog
        plan={plan}
        onClose={vi.fn()}
        onConfirm={vi.fn()}
        busy={false}
        progress={[]}
        completed={false}
        error="Lesson audio changed after the estimate; review a new cost confirmation."
      />,
    );

    const dialog = await screen.findByRole('dialog', { name: 'Generate missing audio' });
    expect(within(dialog).getByRole('alert')).toHaveTextContent('review a new cost confirmation');
    expect(within(dialog).queryByRole('button', { name: 'Generate 1 clip' })).not.toBeInTheDocument();
  });

  it('test_audio_plan_dialog_given_provider_failure_expect_friendly_error_and_raw_details_disclosure', async () => {
    const rawError = 'HTTPStatusError: Google TTS returned 503 Service Unavailable';
    render(
      <LessonAudioPlanDialog
        plan={plan}
        onClose={vi.fn()}
        onConfirm={vi.fn()}
        busy={false}
        progress={[]}
        completed={false}
        error="Google Cloud Text-to-Speech call failed."
        errorDetail={rawError}
      />,
    );

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Google Cloud Text-to-Speech call failed.');
    const details = alert.querySelector('details');
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute('open');
    await userEvent.click(within(alert).getByText('Details'));
    expect(details).toHaveAttribute('open');
    expect(within(alert).getByText(rawError)).toBeInTheDocument();
  });

  it('test_audio_plan_dialog_given_provider_synthesis_is_running_expect_cancel_action_hidden', async () => {
    render(
      <LessonAudioPlanDialog
        plan={plan}
        onClose={vi.fn()}
        onConfirm={vi.fn()}
        busy
        progress={['Mina']}
        completed={false}
        error={null}
      />,
    );

    const dialog = await screen.findByRole('dialog', { name: 'Generate missing audio' });
    expect(within(dialog).queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
    expect(within(dialog).getByRole('status', { name: 'Audio synthesis progress' })).toBeInTheDocument();
  });
});
