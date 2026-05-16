"use client";

import type { CatalogFilterState, SortKey } from "@/lib/catalog/filter";
import { DEFAULT_FILTER_STATE, isFilterActive } from "@/lib/catalog/filter";

interface Props {
  categories: string[];
  tags: string[];
  value: CatalogFilterState;
  onChange: (next: CatalogFilterState) => void;
}

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "newest", label: "Newest first" },
  { key: "quality", label: "Quality score" },
  { key: "name", label: "Name A→Z" },
];

export function CatalogFilters({ categories, tags, value, onChange }: Props) {
  const toggleTag = (t: string) => {
    const has = value.tags.includes(t);
    onChange({
      ...value,
      tags: has ? value.tags.filter((x) => x !== t) : [...value.tags, t],
    });
  };

  const active = isFilterActive(value);

  return (
    <div className="ms-card flex flex-col gap-4 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex flex-1 min-w-[200px] flex-col gap-1">
          <label
            htmlFor="catalog-search"
            className="text-[11px] font-semibold uppercase tracking-[0.15em] text-muted"
          >
            Search
          </label>
          <input
            id="catalog-search"
            type="search"
            className="ms-input"
            placeholder="name, summary, tag…"
            value={value.q}
            onChange={(e) => onChange({ ...value, q: e.target.value })}
          />
        </div>

        <div className="flex flex-col gap-1">
          <label
            htmlFor="catalog-category"
            className="text-[11px] font-semibold uppercase tracking-[0.15em] text-muted"
          >
            Category
          </label>
          <select
            id="catalog-category"
            className="ms-select"
            value={value.category ?? ""}
            onChange={(e) =>
              onChange({
                ...value,
                category: e.target.value === "" ? null : e.target.value,
              })
            }
          >
            <option value="">All categories</option>
            {categories.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label
            htmlFor="catalog-sort"
            className="text-[11px] font-semibold uppercase tracking-[0.15em] text-muted"
          >
            Sort
          </label>
          <select
            id="catalog-sort"
            className="ms-select"
            value={value.sort}
            onChange={(e) =>
              onChange({ ...value, sort: e.target.value as SortKey })
            }
          >
            {SORT_OPTIONS.map((o) => (
              <option key={o.key} value={o.key}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        {active && (
          <button
            type="button"
            onClick={() => onChange(DEFAULT_FILTER_STATE)}
            className="self-end text-xs font-semibold text-ms-blue hover:underline"
          >
            Clear filters
          </button>
        )}
      </div>

      {tags.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <span className="text-[11px] font-semibold uppercase tracking-[0.15em] text-muted">
            Tags
          </span>
          <div className="flex flex-wrap gap-1.5">
            {tags.map((t) => {
              const on = value.tags.includes(t);
              return (
                <button
                  key={t}
                  type="button"
                  onClick={() => toggleTag(t)}
                  className={
                    "ms-chip cursor-pointer transition " +
                    (on
                      ? "bg-ms-blue text-white"
                      : "hover:bg-bg-2")
                  }
                >
                  {t}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
