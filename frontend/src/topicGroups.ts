export type TopicGroup = { id: string; label: string; description: string; item_ids: string[] };
export type TopicChoice<T> = { id: string; label: string; description: string; items: T[]; statuses: Record<string, number> };
export const ALL_TOPICS = '__all__';
export const OTHER_TOPICS = '__other__';

// AI metadata only selects existing records; it never supplies or changes records.
export function parseTopicGroups(raw: unknown, itemIds: Iterable<string>): TopicGroup[] {
  if (!Array.isArray(raw)) return [];
  const validIds = new Set(itemIds);
  const seen = new Set<string>([ALL_TOPICS, OTHER_TOPICS]);
  return raw.flatMap((value): TopicGroup[] => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
    const group = value as Record<string, unknown>;
    if (typeof group.id !== 'string' || !group.id.trim() || seen.has(group.id)
      || typeof group.label !== 'string' || !group.label.trim() || typeof group.description !== 'string'
      || !Array.isArray(group.item_ids) || !group.item_ids.every(id => typeof id === 'string')) return [];
    const ids = [...new Set(group.item_ids as string[])].filter(id => validIds.has(id));
    if (!ids.length) return [];
    seen.add(group.id);
    return [{ id: group.id, label: group.label, description: group.description, item_ids: ids }];
  });
}

export function topicChoices<T>(items: T[], raw: unknown, idOf: (item: T) => string,
  statusOf: (item: T) => string = () => ''): TopicChoice<T>[] {
  const unique = [...new Map(items.map(item => [idOf(item), item])).values()];
  const groups = parseTopicGroups(raw, unique.map(idOf));
  const assigned = new Set(groups.flatMap(group => group.item_ids));
  const choice = (id: string, label: string, description: string, members: T[]): TopicChoice<T> => ({
    id, label, description, items: members, statuses: members.reduce<Record<string, number>>((counts, item) => {
      const status = statusOf(item);
      if (status) Object.defineProperty(counts, status, { value: (Object.hasOwn(counts, status) ? counts[status] : 0) + 1,
        enumerable: true, configurable: true, writable: true });
      return counts;
    }, {}),
  });
  return [choice(ALL_TOPICS, 'All items', 'All records in this version.', unique),
    ...groups.map(group => {
      const ids = new Set(group.item_ids);
      return choice(group.id, group.label, group.description, unique.filter(item => ids.has(idOf(item))));
    }),
    choice(OTHER_TOPICS, 'Untagged', 'Records without an assigned tag.', unique.filter(item => !assigned.has(idOf(item))))];
}

export function matchingTopicChoices<T>(choices: TopicChoice<T>[], query: string): TopicChoice<T>[] {
  const needle = query.toLocaleLowerCase().trim();
  return choices.filter(group => group.id !== ALL_TOPICS && group.id !== OTHER_TOPICS
    && (!needle || group.label.toLocaleLowerCase().includes(needle)));
}

export function filterTopicItems<T>(items: T[], selected: string, choices: TopicChoice<T>[], idOf: (item: T) => string, query = ''): T[] {
  if (query.trim()) {
    const matching = new Set(matchingTopicChoices(choices, query).flatMap(group => group.items.map(idOf)));
    return items.filter(item => matching.has(idOf(item)));
  }
  const choice = choices.find(group => group.id === selected) ?? choices.find(group => group.id === ALL_TOPICS);
  if (!choice) return items;
  const ids = new Set(choice.items.map(idOf));
  return items.filter(item => ids.has(idOf(item)));
}
