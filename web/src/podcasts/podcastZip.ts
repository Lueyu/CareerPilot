const encoder = new TextEncoder();
const crcTable = Uint32Array.from({ length: 256 }, (_, value) => {
  for (let bit = 0; bit < 8; bit++) value = (value & 1 ? 0xEDB88320 : 0) ^ (value >>> 1);
  return value;
});

export function safeFileName(name: string) {
  const cleaned = name.replace(/[<>:"/\\|?*\u0000-\u001f\u007f]/g, '_').trim().replace(/[. ]+$/, '').slice(0, 90) || '播客';
  return /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(cleaned) ? '_' + cleaned : cleaned;
}

function header(size: number, signature: number) {
  const buffer = new ArrayBuffer(size);
  const view = new DataView(buffer);
  view.setUint32(0, signature, true);
  return { buffer, view };
}

export async function zipMp3Files(files: { name: string; audio: Blob }[]) {
  const parts: BlobPart[] = [];
  const directory: BlobPart[] = [];
  let offset = 0;
  let directorySize = 0;
  if (files.length > 0xffff) throw new Error('歌单过大，请分批导出');
  for (const file of files) {
    const name = encoder.encode(file.name);
    if (file.audio.size + offset + directorySize + name.length * 2 + 98 >= 0xffffffff) {
      throw new Error('歌单过大，请分批导出');
    }
    let crc = 0xffffffff;
    const reader = file.audio.stream().getReader();
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        for (const byte of value) crc = crcTable[(crc ^ byte) & 0xff] ^ (crc >>> 8);
      }
    } finally {
      reader.releaseLock();
    }
    crc ^= 0xffffffff;

    const local = header(30, 0x04034b50);
    local.view.setUint16(4, 20, true);
    local.view.setUint16(6, 0x800, true);
    local.view.setUint16(12, 33, true);
    local.view.setUint32(14, crc, true);
    local.view.setUint32(18, file.audio.size, true);
    local.view.setUint32(22, file.audio.size, true);
    local.view.setUint16(26, name.length, true);

    const central = header(46, 0x02014b50);
    central.view.setUint16(4, 20, true);
    central.view.setUint16(6, 20, true);
    central.view.setUint16(8, 0x800, true);
    central.view.setUint16(14, 33, true);
    central.view.setUint32(16, crc, true);
    central.view.setUint32(20, file.audio.size, true);
    central.view.setUint32(24, file.audio.size, true);
    central.view.setUint16(28, name.length, true);
    central.view.setUint32(42, offset, true);

    parts.push(local.buffer, name, file.audio);
    directory.push(central.buffer, name);
    offset += 30 + name.length + file.audio.size;
    directorySize += 46 + name.length;
  }
  const end = header(22, 0x06054b50);
  end.view.setUint16(8, files.length, true);
  end.view.setUint16(10, files.length, true);
  end.view.setUint32(12, directorySize, true);
  end.view.setUint32(16, offset, true);
  return new Blob([...parts, ...directory, end.buffer], { type: 'application/zip' });
}
