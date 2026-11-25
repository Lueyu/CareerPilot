import assert from 'node:assert/strict';
import test from 'node:test';
import { adjacentEpisode, scopeEpisodes, shuffleRound } from '../src/podcasts/podcastQueue';
import { safeFileName, zipMp3Files } from '../src/podcasts/podcastZip';

const episodes = [
  { id: 1, topic: 'Agent', status: 'ready', familiarity: 'unfamiliar' },
  { id: 2, topic: 'RAG', status: 'ready', familiarity: 'familiar' },
  { id: 3, topic: 'Agent', status: 'synthesizing', familiarity: 'unfamiliar' },
  { id: 4, topic: 'Agent', status: 'ready', familiarity: 'unfamiliar' },
  { id: 5, topic: 'Agent', status: 'ready', familiarity: 'unmarked' },
];
const library = {
  episodes,
  playlists: [
    { id: 8, name: 'Agent', episode_ids: [4, 3, 1, 4, 999] },
    { id: 9, name: 'Agent', episode_ids: [2, 4] },
  ],
};
const ids = list => list.map(episode => episode.id);

test('scopes keep playable episodes in the expected order', () => {
  assert.deepEqual(ids(scopeEpisodes(library, { kind: 'all' })), [1, 2, 4, 5]);
  assert.deepEqual(ids(scopeEpisodes(library, { kind: 'topic', topic: 'Agent' })), [1, 4, 5]);
  assert.deepEqual(ids(scopeEpisodes(library, { kind: 'playlist', id: 8 })), [4, 1]);
  assert.deepEqual(ids(scopeEpisodes(library, { kind: 'playlist', id: 9 })), [2, 4]);
  assert.deepEqual(ids(scopeEpisodes(library, { kind: 'unfamiliar' })), [1, 4]);
  assert.deepEqual(ids(scopeEpisodes(library, { kind: 'selection', source: 'search', episodeIds: [5, 3, 1, 5] })), [5, 1]);
  assert.deepEqual(ids(scopeEpisodes(library, { kind: 'selection', source: 'production', episodeIds: [4, 2] })), [4, 2]);
});

test('a shuffle round visits each episode once and stops at the end', () => {
  const shuffled = shuffleRound([1, 2, 4, 5, 1], 2, () => 0.4);
  const available = scopeEpisodes(library, { kind: 'all' });
  let episode = episodes[1];
  const played = [];
  while (episode) {
    played.push(episode.id);
    episode = adjacentEpisode(shuffled, available, episode.id, 1);
  }
  assert.equal(played[0], 2);
  assert.deepEqual(played.toSorted(), [1, 2, 4, 5]);
  assert.equal(adjacentEpisode(shuffled, available, 2, -1), undefined);
});

test('the current card stays anchored if marked familiar while listening', () => {
  const available = scopeEpisodes(library, { kind: 'unfamiliar' });
  const order = [1, 2, 4];
  assert.equal(adjacentEpisode(order, available, 2, -1)?.id, 1);
  assert.equal(adjacentEpisode(order, available, 2, 1)?.id, 4);
  assert.equal(adjacentEpisode(order, available, 4, 1), undefined);
});

test('playlist ZIP uses UTF-8 filenames, ordered files and the MP3 bytes', async () => {
  const names = ['01 - ' + safeFileName('检索/评估') + '.mp3', '02 - 工作区.mp3'];
  const payloads = ['123456789', 'second MP3 file'];
  const zip = await zipMp3Files(names.map((name, index) => ({ name, audio: new Blob([payloads[index]]) })));
  const bytes = new Uint8Array(await zip.arrayBuffer());
  const view = new DataView(bytes.buffer);
  const decoder = new TextDecoder();
  let offset = 0;
  const localOffsets = [];
  names.forEach((name, index) => {
    localOffsets.push(offset);
    assert.equal(view.getUint32(offset, true), 0x04034b50);
    assert.equal(view.getUint16(offset + 6, true), 0x800);
    assert.equal(view.getUint16(offset + 8, true), 0);
    if (index === 0) assert.equal(view.getUint32(offset + 14, true), 0xcbf43926);
    const size = view.getUint32(offset + 18, true);
    const nameLength = view.getUint16(offset + 26, true);
    const audioStart = offset + 30 + nameLength;
    assert.equal(decoder.decode(bytes.slice(offset + 30, audioStart)), name);
    assert.equal(decoder.decode(bytes.slice(audioStart, audioStart + size)), payloads[index]);
    offset = audioStart + size;
  });
  const directoryStart = offset;
  names.forEach((name, index) => {
    assert.equal(view.getUint32(offset, true), 0x02014b50);
    assert.equal(view.getUint32(offset + 42, true), localOffsets[index]);
    const nameLength = view.getUint16(offset + 28, true);
    assert.equal(decoder.decode(bytes.slice(offset + 46, offset + 46 + nameLength)), name);
    offset += 46 + nameLength;
  });
  assert.equal(view.getUint32(offset, true), 0x06054b50);
  assert.equal(view.getUint16(offset + 10, true), names.length);
  assert.equal(view.getUint32(offset + 16, true), directoryStart);
});
