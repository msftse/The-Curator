// Pure catalog filter / sort / extract helpers.
//
// No React imports — these are usable in any context and trivially testable.
// Every function returns a NEW array (never mutates input, never returns the
// input reference) so `useMemo` consumers can rely on reference inequality
// when filter args change.

import type { SkillListItem } from "@/lib/api/types";

export interface FilterArgs {
  category: string | null; // null = "all categories"
  tags: string[]; // [] = "any tags"
  q: string; // "" = "no search"
}

export type SortKey = "newest" | "quality" | "name";

export interface CatalogFilterState extends FilterArgs {
  sort: SortKey;
}

export const DEFAULT_FILTER_STATE: CatalogFilterState = {
  category: null,
  tags: [],
  q: "",
  sort: "newest",
};

export function isFilterActive(state: CatalogFilterState): boolean {
  return (
    state.category !== null ||
    state.tags.length > 0 ||
    state.q.trim() !== "" ||
    state.sort !== "newest"
  );
}

export function filterSkills(
  skills: SkillListItem[],
  { category, tags, q }: FilterArgs,
): SkillListItem[] {
  const needle = q.trim().toLowerCase();
  return skills.filter((s) => {
    if (category && s.classification?.category !== category) return false;
    if (tags.length > 0) {
      const skillTags = new Set(s.classification?.tags ?? []);
      if (!tags.every((t) => skillTags.has(t))) return false;
    }
    if (needle) {
      const hay = [
        s.name,
        s.description,
        s.classification?.summary ?? "",
        ...(s.classification?.tags ?? []),
      ]
        .join(" ")
        .toLowerCase();
      if (!hay.includes(needle)) return false;
    }
    return true;
  });
}

export function sortSkills(
  skills: SkillListItem[],
  key: SortKey,
): SkillListItem[] {
  const copy = [...skills];
  if (key === "newest") {
    copy.sort(
      (a, b) =>
        new Date(b.uploaded_at).getTime() - new Date(a.uploaded_at).getTime(),
    );
  } else if (key === "quality") {
    copy.sort(
      (a, b) =>
        (b.classification?.quality_score ?? 0) -
        (a.classification?.quality_score ?? 0),
    );
  } else {
    copy.sort((a, b) => a.name.localeCompare(b.name));
  }
  return copy;
}

export function extractCategories(skills: SkillListItem[]): string[] {
  const set = new Set<string>();
  for (const s of skills) {
    const c = s.classification?.category;
    if (c) set.add(c);
  }
  return Array.from(set).sort();
}

export function extractTags(skills: SkillListItem[]): string[] {
  const set = new Set<string>();
  for (const s of skills) {
    for (const t of s.classification?.tags ?? []) set.add(t);
  }
  return Array.from(set).sort();
}
