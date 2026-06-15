const BLOCK_SIZE = 2880;

function card(keyword: string, value?: string): string {
  if (value === undefined) {
    return keyword.padEnd(80, " ");
  }
  return `${keyword.padEnd(8, " ")}= ${value}`.padEnd(80, " ").slice(0, 80);
}

function stringValue(value: string): string {
  return `'${value}'`.padEnd(20, " ");
}

function integerValue(value: number): string {
  return String(value).padStart(20, " ");
}

function booleanValue(value: boolean): string {
  return (value ? "T" : "F").padStart(20, " ");
}

function header(cards: string[]): Buffer {
  const text = [...cards, card("END")].join("");
  return Buffer.from(text.padEnd(Math.ceil(text.length / BLOCK_SIZE) * BLOCK_SIZE, " "), "ascii");
}

function imageData(width: number, height: number, offset: number): Buffer {
  const byteLength = width * height * 2;
  const paddedLength = Math.ceil(byteLength / BLOCK_SIZE) * BLOCK_SIZE;
  const output = Buffer.alloc(paddedLength);
  for (let index = 0; index < width * height; index += 1) {
    output.writeInt16BE(offset + index, index * 2);
  }
  return output;
}

export function deterministicFitsFixture(): Buffer {
  const primary = header([
    card("SIMPLE", booleanValue(true)),
    card("BITPIX", integerValue(16)),
    card("NAXIS", integerValue(2)),
    card("NAXIS1", integerValue(2)),
    card("NAXIS2", integerValue(2)),
    card("EXTEND", booleanValue(true)),
    card("OBJECT", stringValue("STARUN E2E")),
  ]);
  const primaryData = imageData(2, 2, 1);
  const extension = header([
    card("XTENSION", stringValue("IMAGE   ")),
    card("BITPIX", integerValue(16)),
    card("NAXIS", integerValue(2)),
    card("NAXIS1", integerValue(8)),
    card("NAXIS2", integerValue(8)),
    card("PCOUNT", integerValue(0)),
    card("GCOUNT", integerValue(1)),
    card("EXTNAME", stringValue("LARGE_IMAGE")),
  ]);
  const extensionData = imageData(8, 8, 100);
  return Buffer.concat([primary, primaryData, extension, extensionData]);
}

export const fitsFile = {
  name: "starun-e2e.fits",
  mimeType: "application/fits",
  buffer: deterministicFitsFixture(),
};
