/** Match every search word across the supplied text fields without changing items. */
export function matchesItemSearch(query: string, fields: unknown[]) {
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const content = fields.filter((value): value is string => typeof value === 'string')
    .join(' ').toLocaleLowerCase();
  return words.every((word) => content.includes(word));
}
