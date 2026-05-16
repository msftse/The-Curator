import ReactMarkdown from "react-markdown";

/**
 * Strip a leading YAML frontmatter block (`---\n…\n---\n`) from the source.
 *
 * The classifier already consumes the frontmatter on the server, so rendering
 * it in the catalog detail page is noise — and react-markdown interprets the
 * second `---` as a setext H2 underline, which yields a malformed-looking
 * heading + horizontal rule combo right at the top of every page.
 */
function stripFrontmatter(source: string): string {
  if (!source.startsWith("---")) return source;
  // Match `---\n…\n---\n?` at the very start. Be permissive about line endings.
  const m = source.match(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/);
  if (!m) return source;
  return source.slice(m[0].length);
}

export function MarkdownView({ source }: { source: string }) {
  const cleaned = stripFrontmatter(source);
  if (!cleaned.trim()) {
    return (
      <p className="text-sm italic text-muted">
        No SKILL.md body recorded for this skill.
      </p>
    );
  }
  // We don't have @tailwindcss/typography wired in, so tag-level classes
  // do the work. Keep these tied to the Curator Indigo / Ink / Muted tokens
  // already defined in tailwind.config.ts so headings and code blocks read
  // as the same surface as the rest of the catalog page.
  return (
    <div className="skillmd max-w-none text-[15px] leading-[1.65] text-ink-2">
      <ReactMarkdown
        components={{
          h1: ({ node: _node, ...props }) => (
            <h1
              {...props}
              className="mt-7 mb-3 font-display text-[24px] font-bold leading-snug tracking-ms-display text-ink first:mt-0"
            />
          ),
          h2: ({ node: _node, ...props }) => (
            <h2
              {...props}
              className="mt-7 mb-2.5 border-b border-line pb-1 font-display text-[19px] font-bold leading-snug text-ink first:mt-0"
            />
          ),
          h3: ({ node: _node, ...props }) => (
            <h3
              {...props}
              className="mt-5 mb-2 font-display text-[16px] font-semibold text-ink"
            />
          ),
          h4: ({ node: _node, ...props }) => (
            <h4
              {...props}
              className="mt-4 mb-1.5 font-display text-[14px] font-semibold uppercase tracking-[0.1em] text-ink-2"
            />
          ),
          p: ({ node: _node, ...props }) => (
            <p {...props} className="my-3 text-ink-2" />
          ),
          a: ({ node: _node, ...props }) => (
            <a
              {...props}
              className="text-ms-blue underline underline-offset-2 hover:text-ms-blue-dark"
              target={props.href?.startsWith("http") ? "_blank" : undefined}
              rel={
                props.href?.startsWith("http") ? "noopener noreferrer" : undefined
              }
            />
          ),
          ul: ({ node: _node, ...props }) => (
            <ul
              {...props}
              className="my-3 list-disc space-y-1 pl-6 marker:text-muted"
            />
          ),
          ol: ({ node: _node, ...props }) => (
            <ol
              {...props}
              className="my-3 list-decimal space-y-1 pl-6 marker:text-muted"
            />
          ),
          li: ({ node: _node, ...props }) => (
            <li {...props} className="text-ink-2" />
          ),
          blockquote: ({ node: _node, ...props }) => (
            <blockquote
              {...props}
              className="my-4 border-l-4 border-ms-blue/40 bg-ms-blue/[0.04] px-4 py-2 italic text-ink-2"
            />
          ),
          hr: ({ node: _node, ...props }) => (
            <hr {...props} className="my-6 border-line" />
          ),
          table: ({ node: _node, ...props }) => (
            <div className="my-4 overflow-x-auto rounded-md border border-line">
              <table {...props} className="w-full border-collapse text-sm" />
            </div>
          ),
          th: ({ node: _node, ...props }) => (
            <th
              {...props}
              className="border-b border-line bg-bg-2 px-3 py-2 text-left font-semibold text-ink"
            />
          ),
          td: ({ node: _node, ...props }) => (
            <td {...props} className="border-b border-line/60 px-3 py-2 text-ink-2" />
          ),
          code: ({ node: _node, className, children, ...props }) => {
            const isInline = !(className?.startsWith("language-") ?? false);
            if (isInline) {
              return (
                <code
                  {...props}
                  className="rounded bg-bg-2 px-1.5 py-0.5 font-mono text-[0.88em] text-ink"
                >
                  {children}
                </code>
              );
            }
            return (
              <code {...props} className={className}>
                {children}
              </code>
            );
          },
          pre: ({ node: _node, ...props }) => (
            <pre
              {...props}
              className="my-4 overflow-x-auto rounded-md border border-line bg-ink text-cream"
            >
              <div className="p-4 font-mono text-[13px] leading-relaxed">
                {props.children}
              </div>
            </pre>
          ),
          strong: ({ node: _node, ...props }) => (
            <strong {...props} className="font-semibold text-ink" />
          ),
        }}
      >
        {cleaned}
      </ReactMarkdown>
    </div>
  );
}
