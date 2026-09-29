import { test } from 'node:test';
import assert from 'node:assert/strict';
import { splitSentences } from '../js/speech.js';

test('splitSentences groups sentences into bounded chunks without losing text', () => {
  const text = 'One. Two is here! Three? ' + 'A long sentence goes on and on. '.repeat(20);
  const chunks = splitSentences(text, 120);
  assert.ok(chunks.every((c) => c.length <= 120));
  assert.equal(chunks.join(' ').replace(/\s+/g, ' ').trim(), text.replace(/\s+/g, ' ').trim());
});

test('splitSentences handles text without punctuation', () => {
  assert.deepEqual(splitSentences('no punctuation here'), ['no punctuation here']);
});
