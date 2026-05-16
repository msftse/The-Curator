import type { SkillStatus, ClassifierStatus } from "@/lib/api/types";

const STATUS_COLORS: Record<SkillStatus, string> = {
  pending: "bg-amber-100 text-amber-800",
  classified: "bg-sky-100 text-sky-800",
  approved: "bg-emerald-100 text-emerald-800",
  rejected: "bg-rose-100 text-rose-800",
  stale: "bg-gray-200 text-gray-700",
  archived: "bg-gray-300 text-gray-800",
};

const CLASSIFIER_COLORS: Record<ClassifierStatus, string> = {
  queued: "bg-amber-100 text-amber-800",
  running: "bg-sky-100 text-sky-800",
  done: "bg-emerald-100 text-emerald-800",
  failed: "bg-rose-100 text-rose-800",
};

export function StatusBadge({ status }: { status: SkillStatus }) {
  return (
    <span
      className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[status]}`}
    >
      {status}
    </span>
  );
}

export function ClassifierBadge({ status }: { status: ClassifierStatus }) {
  return (
    <span
      className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${CLASSIFIER_COLORS[status]}`}
    >
      classifier: {status}
    </span>
  );
}
