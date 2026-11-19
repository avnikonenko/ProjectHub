import assert from 'node:assert/strict';
import test from 'node:test';
import { generationBody, generationDestination } from '../src/generationProvider.ts';

test('Codex requests keep their existing shape and Claude requests identify the provider', () => {
  assert.deepEqual(generationBody({ model: '', effort: '' }), { model: null, effort: null });
  assert.deepEqual(generationBody({ model: 'configured', effort: 'low', provider: 'codex' }),
    { model: 'configured', effort: 'low' });
  assert.deepEqual(generationBody({ model: 'sonnet', effort: 'medium', provider: 'claude' }),
    { model: 'sonnet', effort: 'medium', provider: 'claude' });
});

test('generation disclosure names the selected provider and data destination', () => {
  assert.equal(generationDestination(), 'OpenAI through Codex');
  assert.equal(generationDestination('codex'), 'OpenAI through Codex');
  assert.equal(generationDestination('claude'), 'Anthropic through Claude Code');
});
