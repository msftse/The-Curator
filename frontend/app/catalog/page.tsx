"use client";

import { useMemo, useState } from "react";

import { CatalogFilters } from "@/components/catalog/CatalogFilters";
import { CatalogGrid } from "@/components/catalog/CatalogGrid";
import { api } from "@/lib/api/client";
import {
  DEFAULT_FILTER_STATE,
  extractCategories,
  extractTags,
  filterSkills,
  sortSkills,
  type CatalogFilterState,
} from "@/lib/catalog/filter";
import { useResource } from "@/lib/hooks/useResource";

export default function CatalogPage() {
  const { data, error, isLoading, mutate } = useResource(
    ["catalog", "list"],
    () => api.catalog.list(),
  );
  const [filter, setFilter] = useState<CatalogFilterState>(DEFAULT_FILTER_STATE);

  const skills = useMemo(() => data ?? [], [data]);
  const categories = useMemo(() => extractCategories(skills), [skills]);
  const tags = useMemo(() => extractTags(skills), [skills]);
  const visible = useMemo(
    () =>
      sortSkills(
        filterSkills(skills, {
          category: filter.category,
          tags: filter.tags,
          q: filter.q,
        }),
        filter.sort,
      ),
    [skills, filter],
  );

  const [refreshing, setRefreshing] = useState(false);
  const onRefresh = async () => {
    setRefreshing(true);
    try {
      await mutate();
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div className="mx-auto max-w-[1280px] px-6 py-12">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="ms-eyebrow-blue">Catalog</span>
          <h1 className="mt-1 font-display text-[28px] font-bold tracking-ms-display text-ink">
            Browse skills
          </h1>
          <p className="mt-1 max-w-[60ch] text-sm text-muted">
            Every approved skill in the hub. Filter by category, search by
            keyword, or sort by quality. Click any card for the full SKILL.md
            and a direct bundle download.
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          className="ms-btn-ghost disabled:opacity-50"
        >
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </header>

      {error ? (
        <div className="mb-4 ms-msgbar-danger">
          <span>{String(error)}</span>
        </div>
      ) : null}

      <div className="flex flex-col gap-5">
        <CatalogFilters
          categories={categories}
          tags={tags}
          value={filter}
          onChange={setFilter}
        />

        {isLoading && skills.length === 0 ? (
          <div className="text-sm text-muted">Loading catalog…</div>
        ) : (
          <CatalogGrid skills={visible} totalBeforeFilter={skills.length} />
        )}
      </div>
    </div>
  );
}
