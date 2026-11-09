import assert from 'node:assert/strict';
import test from 'node:test';
import { decodeSchemeText } from '../src/SchemeText.ts';

test('scheme labels recover punctuation and status text', () => {
  assert.equal(decodeSchemeText('Save records&#63; &#40;observed&#41;'), 'Save records? (observed)');
  assert.equal(decodeSchemeText('Read &#8594; save'), 'Read → save');
});

test('scheme label decoding leaves markup as text and decodes only once', () => {
  assert.equal(decodeSchemeText('&#60;script&#62;unsafe&#60;/script&#62;'), '<script>unsafe</script>');
  assert.equal(decodeSchemeText('&#38;#63;'), '&#63;');
  assert.equal(decodeSchemeText('&lt;script&gt;'), '&lt;script&gt;');
});

test('scheme label decoding preserves invalid XML code points and supports Unicode', () => {
  assert.equal(decodeSchemeText('&#0; &#55296; &#1114112; &#65535;'), '&#0; &#55296; &#1114112; &#65535;');
  assert.equal(decodeSchemeText('&#128300;'), '🔬');
});
