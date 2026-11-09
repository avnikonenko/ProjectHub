/** Mermaid leaves escaped decimal entities in some SVG labels. Decode once as
 * text, after sanitization, rather than interpreting the value as HTML. */
export function decodeSchemeText(value: string): string {
  return value.replace(/&#(\d{1,7});/g, (entity, digits: string) => {
    const point = Number(digits);
    const valid = point === 9 || point === 10 || point === 13
      || (point >= 0x20 && point <= 0xd7ff)
      || (point >= 0xe000 && point <= 0xfffd)
      || (point >= 0x10000 && point <= 0x10ffff);
    return valid ? String.fromCodePoint(point) : entity;
  });
}
