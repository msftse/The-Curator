"use client";

import ReactMarkdown from "react-markdown";

export function SkillPreview({ source }: { source: string }) {
  return (
    <div className="prose prose-sm max-w-none">
      <ReactMarkdown>{source}</ReactMarkdown>
    </div>
  );
}
