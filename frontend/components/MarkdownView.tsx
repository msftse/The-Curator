import ReactMarkdown from "react-markdown";

export function MarkdownView({ source }: { source: string }) {
  if (!source.trim()) {
    return (
      <p className="text-sm italic text-gray-500">
        Report not generated for this run.
      </p>
    );
  }
  return (
    <div className="prose prose-sm max-w-none">
      <ReactMarkdown>{source}</ReactMarkdown>
    </div>
  );
}
